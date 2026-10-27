#!/usr/bin/env python3
"""Henter begivenheder i Aarhus fra flere offentlige kilder og skriver .ics-kalenderfiler
(én pr. kategori, navngivet efter kategorien, fx musik-og-koncerter.ics, plus alt.ics,
gratis.ics og perioder.ics - langvarige begivenheder som udstillinger/skøjtebane er
udskilt i perioder.ics for ikke at fylde kalenderen i flere måneder, når man abonnerer på alt.ics).

Kilder (alle hentet via deres egne offentlige sider/API'er - ingen login, ingen
omgåelse af bot-beskyttelse):
  - VisitAarhus' events-database (det officielle turistbureaus JSON-API)
  - Musikhuset Aarhus' kalender
  - VoxHall's koncertprogram
  - Train's koncertkalender

Facebook/Instagram er bevidst udeladt: de kræver login og har aktiv
bot-detection, så automatiseret indsamling derfra ville bryde deres vilkår.

Kør:
    python3 scrape_aarhus_events.py [--days 180] [--out-dir .]
"""
import argparse
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

USER_AGENT = "aarhus-kalender-script/1.0 (personligt brug, kontakt: anders@warehousefilm.com)"
REQUEST_DELAY_SECONDS = 0.3


@dataclass
class Event:
    uid: str
    title: str
    start: datetime
    end: datetime
    all_day: bool
    location: str
    url: str
    source: str
    category: str = "Kunst"
    image: str = ""
    is_free: bool = False
    is_recurring: bool = False


# ---------------------------------------------------------------------------
# Kategorier - bruges til at lave separate, abonnerbare kalendre pr. emne
# ---------------------------------------------------------------------------

GENRE_CATEGORY_MAP = {
    "comedy": "Comedy & Show",
    "show": "Comedy & Show",
    "teater": "Teater & Dans",
    "dans": "Teater & Dans",
    "familie-børn": "Familie & Børn",
    "familie-born": "Familie & Børn",
    "foredrag-talk": "Foredrag & Talks",
    "pop-rock": "Musik & Koncerter",
    "jazz": "Musik & Koncerter",
    "klassisk": "Musik & Koncerter",
    "elektronisk": "Musik & Koncerter",
    "opera": "Musik & Koncerter",
    "verdensmusik": "Musik & Koncerter",
}

# Rækkefølge der afgør hvilken kategori der vælges, hvis et event har flere genre-tags
CATEGORY_PRIORITY = [
    "Comedy & Show", "Teater & Dans", "Familie & Børn", "Foredrag & Talks", "Musik & Koncerter",
]


def classify_genres(genre_slugs: list) -> str:
    found = {GENRE_CATEGORY_MAP[g] for g in genre_slugs if g in GENRE_CATEGORY_MAP}
    for cat in CATEGORY_PRIORITY:
        if cat in found:
            return cat
    return "Kunst"


# Fritekst-klassificering (bruges til Heromkring, som ikke har egne genre-tags
# som Musikhuset) - søgeord matches i rækkefølge, første match vinder.
TEXT_CATEGORY_KEYWORDS = [
    ("Comedy & Show", ("comedy", "standup", "stand-up", "humor")),
    ("Sport & Bevægelse", (
        "løbeklub", "løbetur", "fællesløb", "klatring", "boulder", "vinterbadning",
        "minigolf", "mini golf", "fodboldkamp", "fodbold ", " agf", "aarhus fremad",
        "kampprogram", "yoga", "motionsløb", "badminton", "volley", "capoeira",
        "salsa", "bachata", "dart", "zumba", "spinning",
    )),
    ("Teater & Dans", ("teater", " dans", "danseforestilling", "skuespil")),
    ("Familie & Børn", ("børn", "familie", "baby")),
    ("Foredrag & Talks", ("foredrag", "forelæsning", "forelæsningsrække", "artist talk", "booktalk", "forfattersamtale", "workshop")),
    ("Musik & Koncerter", ("koncert", "spiller", "liveinterview", "lyttesession", "pre-listening", "band", "album")),
    ("Film", ("biograf", "film")),
    ("Mad & Drikke", (
        "madmarked", "gastro", "vinsmagning", "ølsmagning", "smagning", "street food",
        "brunch", "frokost", " middag", "restaurant", "kulinarisk", "fermentering",
        "madmandag", "kokkeskole", "spiseklub", "julebanko", "fællesspisning",
    )),
    ("Kunst", ("udstilling", "kunst", "museum", "galleri", "kunstakademi", "nørkle")),
]


def classify_text(text: str) -> str:
    lowered = text.lower()
    for category, keywords in TEXT_CATEGORY_KEYWORDS:
        if any(kw in lowered for kw in keywords):
            return category
    return "Kunst"


def slugify(text: str) -> str:
    text = text.lower().replace("æ", "ae").replace("ø", "oe").replace("å", "aa").replace("&", "og")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def http_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8")


OG_IMAGE_RE = re.compile(r'<meta\s+property="og:image"\s+content="([^"]+)"', re.I)


SOCIAL_MEDIA_DOMAINS = ("instagram.com", "facebook.com", "fb.com")


def fetch_og_image(url: str) -> str:
    """Henter et repræsentativt billede fra et events egen side (og:image),
    så events uden billede i oversigtslisten alligevel får et rigtigt foto."""
    if any(domain in url for domain in SOCIAL_MEDIA_DOMAINS):
        return ""  # vi undgår Instagram/Facebook helt - login-krav og bot-beskyttelse
    try:
        page = http_get(url)
    except Exception:
        return ""
    m = OG_IMAGE_RE.search(page)
    if not m:
        return ""
    image_url = html.unescape(m.group(1))
    # nogle sider angiver og:image som en relativ sti - gør den absolut
    return urllib.parse.urljoin(url, image_url)


def enrich_missing_images(events: list, delay: float = 0.2) -> None:
    missing = [ev for ev in events if not ev.image and ev.url]
    if not missing:
        return
    print(f"  Henter billeder for {len(missing)} events uden billede i oversigten ...", file=sys.stderr)
    found = 0
    for i, ev in enumerate(missing, start=1):
        ev.image = fetch_og_image(ev.url)
        if ev.image:
            found += 1
        if i % 25 == 0:
            print(f"    {i}/{len(missing)} ...", file=sys.stderr)
        time.sleep(delay)
    print(f"  Fandt billeder til {found}/{len(missing)} events", file=sys.stderr)


def http_post_json(url: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Kilde 1: VisitAarhus events-database (JSON-API)
# ---------------------------------------------------------------------------

def fetch_free_ids(cutoff: datetime) -> set:
    # "Gratis" er et tværgående filter i VisitAarhus' API (facet sm_facet_923,
    # værdi 923//4758) - vi henter listen af gratis-event-id'er separat og
    # bruger den til at mærke de almindelige events.
    API_URL = "https://www.visitaarhus.dk/api/explore"
    PLACE_ID_AARHUS = "176"
    ids = set()
    page = 0
    while True:
        data = http_post_json(API_URL, {
            "filters": {"place": [PLACE_ID_AARHUS], "sm_facet_923": ["923//4758"]},
            "sort": "DATE",
            "page": page,
        })
        explore = data.get("explore", {})
        items = explore.get("items", [])
        if not items:
            break
        stop = False
        for item in items:
            periods = item.get("periodsByDate") or []
            if periods and datetime.strptime(periods[0]["startDate"], "%Y-%m-%dT%H:%M:%S") > cutoff:
                stop = True
            ids.add(item["id"])
        if stop or not explore.get("pagination", {}).get("hasNextPage"):
            break
        page += 1
        time.sleep(REQUEST_DELAY_SECONDS)
    return ids


def fetch_visitaarhus(cutoff: datetime) -> list:
    API_URL = "https://www.visitaarhus.dk/api/explore"
    PLACE_ID_AARHUS = "176"

    free_ids = fetch_free_ids(cutoff)

    events = []
    page = 0
    while True:
        data = http_post_json(API_URL, {
            "filters": {"place": [PLACE_ID_AARHUS]},
            "sort": "DATE",
            "page": page,
        })
        explore = data.get("explore", {})
        items = explore.get("items", [])
        if not items:
            break

        stop = False
        for item in items:
            periods = item.get("periodsByDate") or []
            for i, period in enumerate(periods):
                start = datetime.strptime(period["startDate"], "%Y-%m-%dT%H:%M:%S")
                end = datetime.strptime(period["endDate"], "%Y-%m-%dT%H:%M:%S")
                if start > cutoff:
                    stop = True
                    continue
                if start.date() < datetime.now().date():
                    # permanente udstillinger kan have en startdato langt tilbage
                    # i tiden - ikke interessant i en fremadrettet kalender
                    continue
                title = item.get("title") or "Uden titel"
                events.append(Event(
                    uid=f"va-{item['id']}-{i}",
                    title=title,
                    start=start,
                    end=end + timedelta(days=1),
                    all_day=True,
                    location=item.get("municipality") or "Aarhus",
                    url=item.get("path") or "",
                    source="VisitAarhus",
                    category=classify_text(title),
                    image=(item.get("mainImage") or {}).get("uri") or "",
                    is_free=item["id"] in free_ids,
                ))

        if stop or not explore.get("pagination", {}).get("hasNextPage"):
            break
        page += 1
        time.sleep(REQUEST_DELAY_SECONDS)
    print(f"  VisitAarhus: {len(events)} events", file=sys.stderr)
    return events


# ---------------------------------------------------------------------------
# Kilde 2: Musikhuset Aarhus
# ---------------------------------------------------------------------------

DANSKE_MAANEDER = {
    "januar": 1, "februar": 2, "marts": 3, "april": 4, "maj": 5, "juni": 6,
    "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11, "december": 12,
}


def fetch_musikhuset(cutoff: datetime, max_retries: int = 8) -> list:
    # NB: Musikhuset-sitet er bygget med Svelte, hvis CSS-klassenavne (svelte-xxxxx)
    # er build-hash'ede og kan skifte mellem deploys. Vi parser derfor ud fra de
    # stabile aria-label-attributter i stedet for klassenavne.
    #
    # Sitets server-rendering fejler desuden intermittent (ca. hvert andet kald
    # kommer tilbage uden events, selvom HTTP-statussen er 200) - det er ikke
    # noget på vores side, så vi prøver bare igen et par gange.
    page = ""
    for attempt in range(max_retries):
        page = http_get("https://www.musikhuset.dk/kalender")
        if 'aria-label="Kl.' in page:
            break
        print(f"  Musikhuset Aarhus: tomt svar, prøver igen ({attempt + 1}/{max_retries}) ...", file=sys.stderr)
        time.sleep(1.5)

    combined = re.compile(
        r'aria-label="(?:man|tirs|ons|tors|fre|lør|søn)dag (?P<day>\d{1,2})\. (?P<monthname>[a-zæøå]+)"'
        r'|href="(?P<href>/kalender/[a-z0-9\-]+)" aria-label="Kl\. (?P<time>\d{2}\.\d{2}): (?P<rest>[^"]+)"',
        re.I,
    )

    # Genre-tags (fx #pop-rock, #comedy) står inde i hvert events eget <a>-element,
    # så vi henter dem i en separat pas pr. event-blok.
    event_block_re = re.compile(
        r'href="(?P<href>/kalender/[a-z0-9\-]+)"[^>]*aria-label="Kl\.[^"]*".*?</a>', re.S | re.I,
    )
    genre_re = re.compile(r'class="genre[^"]*"[^>]*>#([a-zæøå0-9\-]+)', re.I)
    genres_by_href = {}
    for block_m in event_block_re.finditer(page):
        genres_by_href[block_m.group("href")] = genre_re.findall(block_m.group(0))

    today = datetime.now()
    year = today.year
    last_month = today.month
    current_date = None
    events = []
    seen = set()

    for m in combined.finditer(page):
        if m.group("day"):
            monthname = m.group("monthname").lower()
            month = DANSKE_MAANEDER.get(monthname)
            if not month:
                continue
            day = int(m.group("day"))
            if month < last_month:
                year += 1
            last_month = month
            current_date = (year, month, day)
            continue

        if current_date is None:
            continue
        href = m.group("href")
        if href in seen:
            continue
        seen.add(href)

        time_str = m.group("time")  # fx "19.00"
        try:
            hh, mm = time_str.split(".")
            start = datetime(*current_date, int(hh), int(mm))
        except ValueError:
            continue
        if start > cutoff:
            continue

        title = html.unescape(m.group("rest").split(",")[0]).strip()
        url = f"https://www.musikhuset.dk{href}"
        genres = genres_by_href.get(href, [])
        category = classify_genres(genres)

        events.append(Event(
            uid=f"mh-{href}",
            title=title,
            start=start,
            end=start + timedelta(hours=2),
            all_day=False,
            location="Musikhuset Aarhus",
            url=url,
            source="Musikhuset Aarhus",
            category=category,
            is_free="gratis" in genres,
        ))

    print(f"  Musikhuset Aarhus: {len(events)} events", file=sys.stderr)
    return events


# ---------------------------------------------------------------------------
# Kilde 3: VoxHall
# ---------------------------------------------------------------------------

def fetch_voxhall(cutoff: datetime) -> list:
    page = http_get("https://voxhall.dk/koncerter/")

    article_re = re.compile(r'<article class="grid-item w-full js-event-element".*?</article>', re.S)
    href_re = re.compile(r'<a href="([^"]+)"')
    date_re = re.compile(r'itemprop="startDate"\s+content="(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"')
    title_re = re.compile(r'itemprop="name">([^<]+)</h2>')
    image_re = re.compile(r'<img[^>]+src="([^"]+)"')

    events = []
    for article in article_re.findall(page):
        href_m = href_re.search(article)
        date_m = date_re.search(article)
        title_m = title_re.search(article)
        image_m = image_re.search(article)
        if not (href_m and date_m and title_m):
            continue

        start = datetime.strptime(date_m.group(1), "%Y-%m-%d %H:%M:%S")
        if start > cutoff:
            continue

        events.append(Event(
            uid=f"vh-{href_m.group(1)}",
            title=html.unescape(title_m.group(1)).strip(),
            start=start,
            end=start + timedelta(hours=2),
            all_day=False,
            location="VoxHall",
            url=href_m.group(1),
            source="VoxHall",
            category="Musik & Koncerter",
            image=image_m.group(1) if image_m else "",
        ))

    print(f"  VoxHall: {len(events)} events", file=sys.stderr)
    return events


# ---------------------------------------------------------------------------
# Kilde 4: Train
# ---------------------------------------------------------------------------

def fetch_train(cutoff: datetime) -> list:
    # NB: Train-sitet er ligesom Musikhuset bygget med Svelte (build-hash'ede
    # klassenavne). Vi matcher derfor kun klasse-præfikser (fx class="artist...")
    # i stedet for de fulde, ustabile klassenavne.
    page = http_get("https://train.dk/kalender/")

    tile_block_re = re.compile(r'<div class="tile[^"]*">.*?</a>', re.S)
    href_re = re.compile(r'<a href="(/kalender/[^"]+)"')
    title_re = re.compile(r'class="artist[^"]*">([^<]+)<')
    date_re = re.compile(r'class="date[^"]*">([^<]+)<')

    events = []
    seen = set()
    for block in tile_block_re.findall(page):
        href_m = href_re.search(block)
        title_m = title_re.search(block)
        date_m = date_re.search(block)
        if not (href_m and title_m and date_m):
            continue
        href = href_m.group(1)
        if href in seen:
            continue
        seen.add(href)

        date_str = date_m.group(1)  # fx "Tirsdag / 27.10.26"
        if "/" not in date_str:
            continue
        date_part = date_str.split("/")[-1].strip()
        try:
            start = datetime.strptime(date_part, "%d.%m.%y")
        except ValueError:
            continue
        if start > cutoff:
            continue

        events.append(Event(
            uid=f"tr-{href}",
            title=html.unescape(title_m.group(1)).strip(),
            start=start,
            end=start + timedelta(days=1),
            all_day=True,
            location="Train, Aarhus",
            url=f"https://train.dk{href}",
            source="Train",
            category="Musik & Koncerter",
        ))

    print(f"  Train: {len(events)} events", file=sys.stderr)
    return events


# ---------------------------------------------------------------------------
# Kilde 5: Øst for Paradis (biograf) - forsiden viser dagens filmprogram.
# ---------------------------------------------------------------------------

def fetch_oestforparadis(cutoff: datetime) -> list:
    try:
        page = http_get("https://www.paradisbio.dk/")
    except Exception as e:
        print(f"  ADVARSEL: kunne ikke hente Øst for Paradis: {e}", file=sys.stderr)
        return []

    title_re = re.compile(
        r'<div class="title phnx_flip_item phx_flip_item_show_(\d+)"[^>]*>([^<]*)</div>'
    )
    titles = {m.group(1): html.unescape(m.group(2)).strip() for m in title_re.finditer(page)}

    time_re = re.compile(
        r'class="hor_type phnx_flip_item phx_flip_item_show_(\d+)"(.*?)(?=class="hor_type phnx_flip_item|\Z)',
        re.S,
    )
    showtime_re = re.compile(r'(\d{1,2}:\d{2})\s*</button>')

    today = datetime.now().date()
    events = []
    seen = set()
    for m in time_re.finditer(page):
        movie_id, block = m.group(1), m.group(2)
        title = titles.get(movie_id)
        if not title:
            continue
        url = f"https://www.paradisbio.dk/movie_details/{movie_id}_date=all_dage"
        for time_m in showtime_re.finditer(block):
            key = (movie_id, time_m.group(1))
            if key in seen:
                continue
            seen.add(key)
            hh, mm = time_m.group(1).split(":")
            start = datetime(today.year, today.month, today.day, int(hh), int(mm))
            if start > cutoff or start < datetime.now() - timedelta(hours=1):
                continue
            events.append(Event(
                uid=f"ofp-{movie_id}-{time_m.group(1)}",
                title=title,
                start=start,
                end=start + timedelta(hours=2),
                all_day=False,
                location="Øst for Paradis",
                url=url,
                source="Øst for Paradis",
                category="Film",
            ))

    print(f"  Øst for Paradis: {len(events)} events (kun i dag)", file=sys.stderr)
    return events


# ---------------------------------------------------------------------------
# Kilde 6: Aarhus Bio + Trøjborg (Nordisk Film Biografer) - hele programmet
# (flere dage frem) står allerede i den almindelige HTML, bare skjult af CSS
# indtil man vælger dato/film i deres filter. Vi parser derfor hele siden i
# dokument-rækkefølge og holder styr på "nuværende film" og "nuværende dato",
# og dedupliker på visningens unikke id (nogle blokke er duplikeret til
# mobil/desktop-visning).
# ---------------------------------------------------------------------------

NFBIO_CINEMAS = [
    ("aarhus-bio", "Aarhus Bio"),
    ("troejborg", "Trøjborg"),
]

NFBIO_MARKER_RE = re.compile(
    r'field--name-title field--type-string field--label-hidden">(?P<title>[^<]+)</span>'
    r'|<time datetime="(?P<date>\d{4}-\d{2}-\d{2})"'
    r'|href="https://www\.nfbio\.dk/screening/\d+/(?P<uuid>[a-f0-9-]+)"[^>]*>\s*'
    r'<div class="movies-screenings-button[^"]*">\s*<div class="room">(?P<room>[^<]*)</div>\s*'
    r'<div class="time"><div>\s*(?P<time>\d{1,2})[.:](?P<minute>\d{2})\s*</div></div>',
    re.S,
)
NFBIO_POSTER_RE = re.compile(r'<img[^>]+src="([^"]+movie_poster_teaser[^"]+)"[^>]*alt=')


def fetch_nfbio_cinema(slug: str, name: str, cutoff: datetime) -> list:
    url = f"https://www.nfbio.dk/biografer/{slug}"
    try:
        page = http_get(url)
    except Exception as e:
        print(f"  ADVARSEL: kunne ikke hente {name}: {e}", file=sys.stderr)
        return []

    current_title = None
    current_date = None
    events = []
    seen = set()

    for m in NFBIO_MARKER_RE.finditer(page):
        if m.group("title"):
            current_title = html.unescape(m.group("title")).strip()
            continue
        if m.group("date"):
            current_date = m.group("date")
            continue
        if m.group("uuid"):
            if not current_title or not current_date:
                continue
            if m.group("uuid") in seen:
                continue
            seen.add(m.group("uuid"))
            try:
                y, mo, d = (int(x) for x in current_date.split("-"))
                start = datetime(y, mo, d, int(m.group("time")), int(m.group("minute")))
            except ValueError:
                continue
            if start > cutoff:
                continue
            events.append(Event(
                uid=f"nfb-{slug}-{m.group('uuid')}",
                title=current_title,
                start=start,
                end=start + timedelta(hours=2),
                all_day=False,
                location=name,
                url=url,
                source=name,
                category="Film",
            ))

    # billeder: match et plakat-billede til hver titel ud fra rækkefølgen de optræder i
    posters = NFBIO_POSTER_RE.findall(page)
    titles_in_order = []
    seen_titles = set()
    for m in re.finditer(r'field--name-title field--type-string field--label-hidden">([^<]+)</span>', page):
        t = html.unescape(m.group(1)).strip()
        if t not in seen_titles:
            seen_titles.add(t)
            titles_in_order.append(t)
    title_to_image = dict(zip(titles_in_order, posters))
    for ev in events:
        poster = title_to_image.get(ev.title, "")
        ev.image = urllib.parse.urljoin(url, poster) if poster else ""

    print(f"  {name}: {len(events)} events", file=sys.stderr)
    return events


def fetch_nfbio(cutoff: datetime) -> list:
    events = []
    for slug, name in NFBIO_CINEMAS:
        events.extend(fetch_nfbio_cinema(slug, name, cutoff))
        time.sleep(REQUEST_DELAY_SECONDS)
    return events


# ---------------------------------------------------------------------------
# Volume Village (spillested/klub) - events hentes fra forsidens CMS-grid,
# som indeholder dato, type-label, evt. udsolgt-status, billede og hashtags.
# ---------------------------------------------------------------------------

VOLUMEVILLAGE_CHUNK_SPLIT = 'class="collection-item---event-cms-grid w-dyn-item"'
VOLUMEVILLAGE_DATE_RE = re.compile(r'<div class="event_cms_date left[^"]*">([^<]*)</div>')
VOLUMEVILLAGE_URL_RE = re.compile(r'<a href="(/events/[^"]+)" class="event_link_block')
VOLUMEVILLAGE_IMG_RE = re.compile(r'background-image:url\(&quot;([^&]+)&quot;\)')
VOLUMEVILLAGE_TITLE_RE = re.compile(r'<h1 class="heading-cms-collections">([^<]*)</h1>')


def fetch_volumevillage(cutoff: datetime) -> list:
    try:
        page = http_get("https://www.volumevillage.dk/")
    except Exception as e:
        print(f"  ADVARSEL: kunne ikke hente Volume Village: {e}", file=sys.stderr)
        return []

    events = []
    # Hvert event er sit eget CMS-grid-element; vi splitter på den stabile
    # wrapper-klasse i stedet for én stor regex, så felter aldrig "lækker"
    # ind i naboeventet, hvis et felt mangler for ét event (fx tom type).
    for chunk in page.split(VOLUMEVILLAGE_CHUNK_SPLIT)[1:]:
        url_m = VOLUMEVILLAGE_URL_RE.search(chunk)
        title_m = VOLUMEVILLAGE_TITLE_RE.search(chunk)
        img_m = VOLUMEVILLAGE_IMG_RE.search(chunk)
        if not url_m or not title_m:
            continue

        dates = [d.strip() for d in VOLUMEVILLAGE_DATE_RE.findall(chunk) if d.strip() and d.strip() != "-"]
        if not dates:
            continue
        try:
            start = datetime.strptime(dates[0], "%d.%m.%Y")
            end = datetime.strptime(dates[-1], "%d.%m.%Y")
        except ValueError:
            continue
        if start > cutoff:
            continue
        if end.date() < datetime.now().date():
            continue

        events.append(Event(
            uid=f"vv-{slugify(url_m.group(1))}",
            title=html.unescape(title_m.group(1)).strip(),
            start=start,
            end=end + timedelta(days=1),
            all_day=True,
            location="Volume Village",
            url=urllib.parse.urljoin("https://www.volumevillage.dk/", url_m.group(1)),
            source="Volume Village",
            category="Musik & Koncerter",
            image=html.unescape(img_m.group(1)) if img_m else "",
        ))

    print(f"  Volume Village: {len(events)} events", file=sys.stderr)
    return events


# ---------------------------------------------------------------------------
# Godsbanen (kulturhus) - hele kalenderen er serverrenderet på én side, med
# pålidelige data-event-start/-end (ÅÅÅÅMM) attributter pr. event.
# ---------------------------------------------------------------------------

GODSBANEN_ARTICLE_RE = re.compile(
    r'<article class="event-overview__item" data-event-id="\d+" data-event-type="(?P<types>[^"]*)" '
    r'data-event-start="(?P<start>\d{6})" data-event-end="(?P<end>\d{0,6})">\s*'
    r'<a href="(?P<url>[^"]+)">\s*'
    r'<figure class="event-overview__item__image">\s*'
    r'<img[^>]+src="(?P<img>[^"]+)"[^>]*/>\s*'
    r'</figure>\s*'
    r'<div class="event-overview__item__content">\s*'
    r'<h3>(?P<h3>[^<]*)</h3>\s*'
    r'<h2>(?P<title>[^<]*)</h2>\s*'
    r'<div class="event-overview__item__date">\s*(?P<date>[^<]*)</div>',
    re.S,
)

GODSBANEN_TYPE_CATEGORY = [
    ("gods", "Mad & Drikke"),
    ("teater-katapult", "Teater & Dans"),
    ("scene", "Teater & Dans"),
    ("musik", "Musik & Koncerter"),
    ("radar", "Musik & Koncerter"),
    ("billedkunst", "Kunst"),
    ("udstilling", "Kunst"),
    ("litteratur", "Foredrag & Talks"),
    ("foredrag", "Foredrag & Talks"),
    ("kursus", "Foredrag & Talks"),
    ("aabne", "Foredrag & Talks"),
    ("marked", "Kunst"),
]


def classify_godsbanen(types: list, title: str) -> str:
    for slug, category in GODSBANEN_TYPE_CATEGORY:
        if slug in types:
            return category
    return classify_text(title)


def fetch_godsbanen(cutoff: datetime) -> list:
    try:
        page = http_get("https://godsbanen.dk/kalender/")
    except Exception as e:
        print(f"  ADVARSEL: kunne ikke hente Godsbanen: {e}", file=sys.stderr)
        return []

    events = []
    for m in GODSBANEN_ARTICLE_RE.finditer(page):
        day_nums = [int(n) for n in re.findall(r"\d+", m.group("date"))]
        if not day_nums:
            continue
        start_year, start_month = int(m.group("start")[:4]), int(m.group("start")[4:6])
        end_raw = m.group("end")
        end_year, end_month = (int(end_raw[:4]), int(end_raw[4:6])) if end_raw else (start_year, start_month)
        try:
            start = datetime(start_year, start_month, day_nums[0])
            end = datetime(end_year, end_month, day_nums[-1])
        except ValueError:
            continue
        if start > cutoff:
            continue
        if end.date() < datetime.now().date():
            continue

        types = m.group("types").split(",")
        title = html.unescape(m.group("title")).strip()
        url_slug = m.group("url").rstrip("/").rsplit("/", 1)[-1]

        events.append(Event(
            uid=f"gb-{url_slug}",
            title=title,
            start=start,
            end=end + timedelta(days=1),
            all_day=True,
            location="Godsbanen",
            url=m.group("url"),
            source="Godsbanen",
            category=classify_godsbanen(types, title),
            image=html.unescape(m.group("img")),
            is_free="gratis" in types,
        ))

    print(f"  Godsbanen: {len(events)} events", file=sys.stderr)
    return events


# ---------------------------------------------------------------------------
# Beboernes Hus (Øgadekvarteret) - kalenderen er renderet med WordPress-pluginet
# "ICS Calendar" via en AJAX-visning, ikke i den statiske HTML. Vi henter først
# forsiden for et frisk nonce + den konfigurerede visnings-args, og kalder så
# selve AJAX-endpointet direkte (samme kald som pluginets eget JS laver) -
# det giver os alle ~6 måneder på én gang, inkl. "faste aktiviteter" som
# yoga, capoeira, badminton osv.
# ---------------------------------------------------------------------------

BEBOERNESHUS_NONCE_RE = re.compile(r'"r34ics_nonce":"([a-f0-9]+)"')
BEBOERNESHUS_ARGS_RE = re.compile(r'data-args="([a-f0-9]+)"')
BEBOERNESHUS_EVENT_RE = re.compile(
    r'<li class="event d(?P<date>\d{8}) t(?P<time>\d{6})" data-feed-key="\d"[^>]*>(?P<body>.*?)</li>',
    re.S,
)
BEBOERNESHUS_TIME_RE = re.compile(r'<span class="time">\s*(?P<start>\d{1,2}:\d{2})')
BEBOERNESHUS_END_RE = re.compile(r'end_time show_on_hover">(?:&#8211;|&ndash;|-|–)\s*(?P<end>\d{1,2}:\d{2})')
BEBOERNESHUS_TITLE_RE = re.compile(r'<span tabindex="0" class="title[^"]*">(?P<title>[^<]*)</span>')
BEBOERNESHUS_LINK_RE = re.compile(r'<div class="eventdesc">.*?<a href="(?P<href>https?://[^"]+)"', re.S)
BEBOERNESHUS_CALENDAR_URL = "https://beboerneshus.dk/kalender/"


def fetch_beboerneshus(cutoff: datetime) -> list:
    try:
        page = http_get("https://beboerneshus.dk/kalender/")
        nonce_m = BEBOERNESHUS_NONCE_RE.search(page)
        args_m = BEBOERNESHUS_ARGS_RE.search(page)
        if not nonce_m or not args_m:
            raise ValueError("fandt ikke nonce/args til ICS Calendar-pluginet")
        body = urllib.parse.urlencode({
            "action": "r34ics_ajax",
            "r34ics_nonce": nonce_m.group(1),
            "subaction": "display_calendar",
            "args": args_m.group(1),
            "js_args": json.dumps({"debug": False}),
        }).encode()
        req = urllib.request.Request(
            "https://beboerneshus.dk/wp-admin/admin-ajax.php",
            data=body,
            headers={"User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            calendar_html = resp.read().decode("utf-8")
    except Exception as e:
        print(f"  ADVARSEL: kunne ikke hente Beboernes Hus: {e}", file=sys.stderr)
        return []

    events = []
    seen = set()
    for m in BEBOERNESHUS_EVENT_RE.finditer(calendar_html):
        body_html = m.group("body")
        title_m = BEBOERNESHUS_TITLE_RE.search(body_html)
        time_m = BEBOERNESHUS_TIME_RE.search(body_html)
        if not title_m or not time_m:
            continue
        title = html.unescape(title_m.group("title")).strip()

        y, mo, d = int(m.group("date")[:4]), int(m.group("date")[4:6]), int(m.group("date")[6:8])
        sh, sm_ = int(time_m.group("start").split(":")[0]), int(time_m.group("start").split(":")[1])
        try:
            start = datetime(y, mo, d, sh, sm_)
        except ValueError:
            continue
        if start > cutoff or start.date() < datetime.now().date():
            continue

        end_m = BEBOERNESHUS_END_RE.search(body_html)
        if end_m:
            eh, em_ = int(end_m.group("end").split(":")[0]), int(end_m.group("end").split(":")[1])
            end = start.replace(hour=eh, minute=em_)
            if end <= start:
                end += timedelta(days=1)
        else:
            end = start + timedelta(hours=2)

        key = (title.lower(), start)
        if key in seen:
            continue
        seen.add(key)

        is_recurring = "gentages hver uge" in body_html.lower()

        link_m = BEBOERNESHUS_LINK_RE.search(body_html)
        event_url = BEBOERNESHUS_CALENDAR_URL
        if link_m:
            candidate = link_m.group("href")
            if not any(domain in candidate for domain in SOCIAL_MEDIA_DOMAINS):
                event_url = candidate

        events.append(Event(
            uid=f"bh-{slugify(title)}-{m.group('date')}-{m.group('time')}",
            title=title,
            start=start,
            end=end,
            all_day=False,
            location="Beboernes Hus",
            url=event_url,
            source="Beboernes Hus",
            category=classify_text(title),
            is_free="gratis" in body_html.lower(),
            is_recurring=is_recurring,
        ))

    print(f"  Beboernes Hus: {len(events)} events", file=sys.stderr)
    return events


# ---------------------------------------------------------------------------
# AROS (kunstmuseum) - kalenderen er JS-renderet, men henter sine data fra et
# simpelt JSON-API (POST /api/events med tom body giver alle events, ca. et
# år frem), så vi kan kalde det direkte uden at udføre JavaScript.
# ---------------------------------------------------------------------------

AROS_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})")


def fetch_aros(cutoff: datetime) -> list:
    try:
        req = urllib.request.Request(
            "https://www.aros.dk/api/events",
            data=b"{}",
            headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  ADVARSEL: kunne ikke hente AROS: {e}", file=sys.stderr)
        return []

    events = []
    for item in data.get("events", []):
        title = html.unescape(item.get("title") or "").strip()
        if not title:
            continue
        try:
            day = datetime.strptime(item["date"][:10], "%Y-%m-%d")
        except (KeyError, ValueError):
            continue

        time_m = AROS_TIME_RE.search(item.get("timeLabel") or "")
        if time_m:
            sh, sm_, eh, em_ = (int(g) for g in time_m.groups())
            start = day.replace(hour=sh, minute=sm_)
            end = day.replace(hour=eh, minute=em_)
            if end <= start:
                end += timedelta(days=1)
            all_day = False
        else:
            start = day
            end = day + timedelta(days=1)
            all_day = True

        if start > cutoff or start.date() < datetime.now().date():
            continue

        category_labels = item.get("categoryLabels") or []
        desc = f"{title} {' '.join(category_labels)}"
        image_url = (item.get("image") or {}).get("url") or ""

        events.append(Event(
            uid=f"aros-{slugify(item.get('url') or title)}",
            title=title,
            start=start,
            end=end,
            all_day=all_day,
            location="AROS",
            url=urllib.parse.urljoin("https://www.aros.dk/", item.get("url") or "/kalender/"),
            source="AROS",
            category=classify_text(desc),
            image=urllib.parse.urljoin("https://www.aros.dk/", image_url) if image_url else "",
            is_free="gratis" in desc.lower(),
        ))

    print(f"  AROS: {len(events)} events", file=sys.stderr)
    return events


# ---------------------------------------------------------------------------
# Kunsthal Aarhus (events vises via deres webshop-side, serverrenderet)
# ---------------------------------------------------------------------------

KUNSTHAL_CARD_RE = re.compile(
    r'<a class="event-card" href="(?P<url>[^"]+)">.*?'
    r'image-backfill:\s*url\((?P<img>[^)]+)\);.*?'
    r'<p class="event-card__date date-time">(?P<date>[^<]*)</p>'
    r'<p class="event-card__time date-time">(?P<time>[^<]*)</p>'
    r'<h3 class="event-card__title"[^>]*>(?P<title>[^<]*)</h3>'
    r'<p class="event-card__category">(?P<category>[^<]*)</p>',
    re.S,
)


def fetch_kunsthal_aarhus(cutoff: datetime) -> list:
    try:
        page = http_get("https://kunsthal.dk/pages/events")
    except Exception as e:
        print(f"  ADVARSEL: kunne ikke hente Kunsthal Aarhus: {e}", file=sys.stderr)
        return []

    events = []
    for m in KUNSTHAL_CARD_RE.finditer(page):
        title = html.unescape(m.group("title")).strip()
        try:
            day = datetime.strptime(m.group("date").strip(), "%d.%m.%Y")
        except ValueError:
            continue

        time_m = AROS_TIME_RE.search(m.group("time"))
        if time_m:
            sh, sm_, eh, em_ = (int(g) for g in time_m.groups())
            start = day.replace(hour=sh, minute=sm_)
            end = day.replace(hour=eh, minute=em_)
            if end <= start:
                end += timedelta(days=1)
            all_day = False
        else:
            start = day
            end = day + timedelta(days=1)
            all_day = True

        if start > cutoff or start.date() < datetime.now().date():
            continue

        category = html.unescape(m.group("category")).strip()
        desc = f"{title} {category}"
        img = m.group("img").strip().strip("'\"")
        img_url = urllib.parse.urljoin("https://kunsthal.dk/", img) if img.startswith("/") else img
        if img_url.startswith("//"):
            img_url = "https:" + img_url

        events.append(Event(
            uid=f"kunsthal-{slugify(m.group('url'))}",
            title=title,
            start=start,
            end=end,
            all_day=all_day,
            location="Kunsthal Aarhus",
            url=urllib.parse.urljoin("https://kunsthal.dk/", m.group("url")),
            source="Kunsthal Aarhus",
            category=classify_text(desc),
            image=img_url,
            is_free="gratis" in desc.lower(),
        ))

    print(f"  Kunsthal Aarhus: {len(events)} events", file=sys.stderr)
    return events

    print(f"  Beboernes Hus: {len(events)} events", file=sys.stderr)
    return events


# ---------------------------------------------------------------------------
# Heromkring (ugentligt lokalt nyhedsbrev) - vi henter KUN titel, undertekst,
# forsidebillede og link for hver udgave, aldrig selve artiklens indhold.
# Det er en kreditering/henvisning til deres index af udgaver - ikke en
# genudgivelse af deres redaktionelle research/tekst.
# ---------------------------------------------------------------------------

def _decode_json_string(raw: str) -> str:
    # den fangede tekst er råt JSON-strengindhold (kan indeholde & o.l.),
    # så vi json-afkoder den rigtigt i stedet for kun at håndtere HTML-entities
    try:
        return json.loads(f'"{raw}"')
    except json.JSONDecodeError:
        return html.unescape(raw)


HEROMKRING_POST_RE = re.compile(
    r'"web_title":"([^"]+)","parameterized_web_title":"[^"]*","web_subtitle":"([^"]*)"'
    r'.*?"slug":"([^"]+)","is_premium":(?:true|false),"image_url":"([^"]*)"',
    re.S,
)


def fetch_heromkring_posts(limit: int = 4) -> list:
    # Hvert post-objekt på siden indeholder (ud over titel/undertekst/billede/slug)
    # også hele artiklens styling-data og formentlig selve brødteksten - det vil vi
    # ikke bruge eller gemme noget af. Vi matcher derfor kun de fire konkrete felter
    # direkte i rækkefølge, hen over hele siden, og ignorerer resten af hvert objekt.
    try:
        page = http_get("https://heromkring.beehiiv.com/")
    except Exception as e:
        print(f"  ADVARSEL: kunne ikke hente Heromkring: {e}", file=sys.stderr)
        return []

    posts = []
    seen_slugs = set()
    for m in HEROMKRING_POST_RE.finditer(page):
        title, subtitle, slug, image = m.groups()
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        posts.append({
            "title": _decode_json_string(title),
            "subtitle": _decode_json_string(subtitle),
            "image": _decode_json_string(image) if image else "",
            "url": f"https://heromkring.beehiiv.com/p/{slug}",
        })
        if len(posts) >= limit:
            break
    return posts


# ---------------------------------------------------------------------------
# Denne funktion trækker konkrete FAKTA (navn, sted, tidspunkt, pris) ud af
# Heromkrings ugentlige "ugens events"-liste - aldrig Simons egne sætninger/
# kommentarer/vurderinger. Fakta er ikke ophavsretligt beskyttede, men det er
# hans udvalg og sammensætning af dem; at trække det meste eller det hele ud
# systematisk er en gråzone, selv når det omskrives. Holdt bevidst med i det
# offentlige site (ejerens beslutning, okt. 2026) - siden fungerer som et
# samlingssted/aggregator på tværs af kilder, hver event linker direkte
# tilbage til sin oprindelige kilde, og hvert Heromkring-event er mærket
# "via Heromkring". Hvis Heromkring selv beder om at blive fjernet, skal det
# ske uden diskussion.
# ---------------------------------------------------------------------------

HEROMKRING_DAY_RE = re.compile(
    r'^(\d{1,2})\.\s*(' + "|".join(DANSKE_MAANEDER.keys()) + r')$', re.I,
)
HEROMKRING_EVENT_LINE_RE = re.compile(
    r'^(?P<desc>.+?)\s*\((?P<meta>(?:fra\s+)?\d{1,2}[.:]\d{2}[^)]*)\)\s*$'
)


def _strip_tags_to_text(fragment: str) -> str:
    text = re.sub(r'<[^>]+>', '', fragment)
    text = html.unescape(text)
    return re.sub(r'\s+', ' ', text).strip()


HEROMKRING_SOURCE_LINK_RE = re.compile(
    r'<a class="link" href="([^"]+)"[^>]*>\s*(?:fra\s+)?\d{1,2}[.:]\d{2}[^<]*</a>'
)


def fetch_heromkring_events(post_url: str, cutoff: datetime) -> list:
    # Hvert events tid/pris-tekst i nyhedsbrevet er selv et link til den
    # oprindelige arrangør/billetside (fx billetlugen.dk, kultunaut.dk, en
    # biografs egen side). Vi bruger DET link som eventets URL og henter evt.
    # billede derfra - ikke fra Heromkring - så det peger på den primære kilde.
    try:
        page = http_get(post_url)
    except Exception as e:
        print(f"  ADVARSEL: kunne ikke hente Heromkring-udgave: {e}", file=sys.stderr)
        return []

    raw_paragraphs = re.findall(r'<p[^>]*>(.*?)</p>', page, re.S)
    paragraphs = [(_strip_tags_to_text(p), p) for p in raw_paragraphs]
    paragraphs = [(text, raw) for text, raw in paragraphs if text]

    today = datetime.now()
    year = today.year
    last_month = today.month
    current_date = None
    started = False
    seen_desc = set()
    events = []

    for text, raw in paragraphs:
        day_m = HEROMKRING_DAY_RE.match(text)
        if day_m:
            started = True
            day = int(day_m.group(1))
            month = DANSKE_MAANEDER[day_m.group(2).lower()]
            if month < last_month:
                year += 1
            last_month = month
            current_date = (year, month, day)
            continue

        if not started or current_date is None:
            continue

        m = HEROMKRING_EVENT_LINE_RE.match(text)
        if not m:
            continue  # det er en kommentar-/uddybningsparagraf, ikke en event-linje

        # Hvis Simons egen uddybning er smeltet sammen i samme linje (adskilt af et
        # kolon), beholder vi kun den korte, faktuelle del før kolonnet.
        desc = strip_emoji(m.group("desc").strip())
        if ":" in desc:
            head, rest = desc.split(":", 1)
            if len(rest.strip()) > 15:
                desc = head.strip()
        if len(desc) > 90:
            desc = desc[:88].rsplit(" ", 1)[0] + " …"

        key = (current_date, desc.lower())
        if key in seen_desc:
            continue
        seen_desc.add(key)

        meta = m.group("meta").strip()
        is_free = "gratis" in meta.lower()
        time_m = re.search(r'(\d{1,2})[.:](\d{2})', meta)

        link_m = HEROMKRING_SOURCE_LINK_RE.search(raw)
        source_url = html.unescape(link_m.group(1)) if link_m else post_url

        try:
            if time_m:
                start = datetime(*current_date, int(time_m.group(1)), int(time_m.group(2)))
                all_day = False
                end = start + timedelta(hours=2)
            else:
                start = datetime(*current_date)
                all_day = True
                end = start + timedelta(days=1)
        except ValueError:
            continue

        if start > cutoff:
            continue

        events.append(Event(
            uid=f"hk-{start.strftime('%Y%m%d')}-{abs(hash(desc)) % 10**8}",
            title=desc,
            start=start,
            end=end,
            all_day=all_day,
            location="Aarhus",
            url=source_url,
            source="Heromkring",
            category=classify_text(desc),
            is_free=is_free,
        ))

    return events


# ---------------------------------------------------------------------------
# Dedupe (samme koncert optræder ofte både hos VisitAarhus og på spillestedet)
# ---------------------------------------------------------------------------

def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9æøå]+", "", title.lower())


EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FFFF"
    "\U00002600-\U000027BF"
    "\U00002190-\U000021FF"
    "\U00002B00-\U00002BFF"
    "️"
    "]+"
)


def strip_emoji(text: str) -> str:
    return re.sub(r"\s+", " ", EMOJI_RE.sub("", text)).strip()


# Heromkring skal aldrig "vinde" over vores egne, mere pålidelige kilder -
# både fordi de har bedre data (billeder, præcise tider), og fordi det ikke
# skal se ud som om alt stammer fra Heromkring, når vi faktisk selv har fundet det.
SOURCE_PRIORITY = {"Heromkring": 0}
DEFAULT_SOURCE_PRIORITY = 1


def _source_priority(ev) -> int:
    return SOURCE_PRIORITY.get(ev.source, DEFAULT_SOURCE_PRIORITY)


def dedupe(events: list) -> list:
    # Pas 1: Heromkrings titler er ofte længere/anderledes formuleret end vores
    # egne kilders (fx "🎙️ Malk De koijn spiller hos Voxhall" vs. "Malk De
    # Koijn"), så et præcist titel-match fanger ikke altid overlap. Vi fjerner
    # derfor Heromkring-events, hvis en anden kildes event samme dato har en
    # normaliseret titel, der går igen som en del af Heromkrings titel.
    by_date = {}
    for ev in events:
        by_date.setdefault(ev.start.date(), []).append(ev)

    dropped_ids = set()
    for day_events in by_date.values():
        others = [e for e in day_events if e.source != "Heromkring"]
        other_norms = [(normalize_title(e.title), e) for e in others]
        for ev in day_events:
            if ev.source != "Heromkring":
                continue
            hk_norm = normalize_title(ev.title)
            for other_norm, other_ev in other_norms:
                if len(other_norm) >= 5 and other_norm in hk_norm:
                    dropped_ids.add(id(ev))
                    break

    events = [ev for ev in events if id(ev) not in dropped_ids]

    # Pas 2: almindeligt dedupe på eksakt (titel, dato) - med præference for
    # vores egne kilder frem for Heromkring, og ellers versionen med klokkeslæt.
    # For events med klokkeslæt (fx film med flere visninger samme dag) indgår
    # tidspunktet også i nøglen, så vi ikke slår flere visninger sammen til én.
    best = {}
    for ev in events:
        key = (
            normalize_title(ev.title),
            ev.start.date(),
            None if ev.all_day else ev.start.time(),
        )
        existing = best.get(key)
        if existing is None:
            best[key] = ev
            continue
        if _source_priority(ev) > _source_priority(existing):
            best[key] = ev
        elif _source_priority(ev) == _source_priority(existing) and existing.all_day and not ev.all_day:
            best[key] = ev
    return list(best.values())


# ---------------------------------------------------------------------------
# ICS-generering
# ---------------------------------------------------------------------------

VTIMEZONE = """BEGIN:VTIMEZONE
TZID:Europe/Copenhagen
BEGIN:DAYLIGHT
TZOFFSETFROM:+0100
TZOFFSETTO:+0200
TZNAME:CEST
DTSTART:19700329T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:+0200
TZOFFSETTO:+0100
TZNAME:CET
DTSTART:19701025T030000
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU
END:STANDARD
END:VTIMEZONE""".replace("\n", "\r\n")


def escape_ics_text(text: str) -> str:
    text = text.replace("\\", "\\\\")
    text = text.replace(";", "\\;")
    text = text.replace(",", "\\,")
    text = text.replace("\n", "\\n")
    return text


def fold_line(line: str) -> str:
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line
    parts = []
    start = 0
    limit = 75
    while start < len(encoded):
        chunk = encoded[start:start + limit]
        while True:
            try:
                chunk.decode("utf-8")
                break
            except UnicodeDecodeError:
                chunk = chunk[:-1]
        parts.append(chunk.decode("utf-8"))
        start += len(chunk)
        limit = 74
    return ("\r\n ").join(parts)


PERIOD_MIN_DAYS = 6  # events der spænder over mere end en uge regnes som "perioder" (udstillinger, skøjtebane osv.)


def is_period_event(ev) -> bool:
    return (ev.end.date() - ev.start.date()).days >= PERIOD_MIN_DAYS



# "Gentagende/langvarigt" er - ligesom "Gratis" og "Dates" - et tværgående
# filter: events der IKKE er engangsoplevelser, enten fordi de strækker sig
# over en lang periode (se is_period_event) eller fordi teksten selv nævner
# en fast gentagelse (fx "hver mandag", "ugentlig", en residency).
RECURRING_KEYWORDS = (
    "hver mandag", "hver tirsdag", "hver onsdag", "hver torsdag", "hver fredag",
    "hver lørdag", "hver søndag", "ugentlig", "hver uge", "hver måned",
    "månedlig", "residency", "fast hver",
)


def is_recurring_or_long_event(ev) -> bool:
    if ev.is_recurring or is_period_event(ev):
        return True
    lowered = ev.title.lower()
    return any(kw in lowered for kw in RECURRING_KEYWORDS)


def build_ics(events: list, calname: str, caldesc: str) -> str:
    now_utc = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//aarhus-kalender//scrape_aarhus_events//DA",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape_ics_text(calname)}",
        f"X-WR-CALDESC:{escape_ics_text(caldesc)}",
        "X-WR-TIMEZONE:Europe/Copenhagen",
    ]
    lines.extend(VTIMEZONE.split("\r\n"))

    for ev in sorted(events, key=lambda e: e.start):
        title = escape_ics_text(ev.title)
        location = escape_ics_text(ev.location)
        cat_parts = [ev.category, ev.source] + (["Gratis"] if ev.is_free else [])
        categories = escape_ics_text(",".join(cat_parts))

        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{ev.uid}@aarhus-kalender")
        lines.append(f"DTSTAMP:{now_utc}")
        if ev.all_day:
            lines.append(f"DTSTART;VALUE=DATE:{ev.start.strftime('%Y%m%d')}")
            lines.append(f"DTEND;VALUE=DATE:{ev.end.strftime('%Y%m%d')}")
        else:
            lines.append(f"DTSTART;TZID=Europe/Copenhagen:{ev.start.strftime('%Y%m%dT%H%M%S')}")
            lines.append(f"DTEND;TZID=Europe/Copenhagen:{ev.end.strftime('%Y%m%dT%H%M%S')}")
        lines.append(f"SUMMARY:{title}")
        if location:
            lines.append(f"LOCATION:{location}")
        lines.append(f"CATEGORIES:{categories}")
        if ev.url:
            lines.append(f"URL:{ev.url}")
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")

    folded = [fold_line(line) for line in lines]
    return "\r\n".join(folded) + "\r\n"


# ---------------------------------------------------------------------------

SOURCES = [
    ("VisitAarhus", fetch_visitaarhus),
    ("Musikhuset Aarhus", fetch_musikhuset),
    ("VoxHall", fetch_voxhall),
    ("Train", fetch_train),
    ("Øst for Paradis", fetch_oestforparadis),
    ("Nordisk Film Biografer (Aarhus Bio + Trøjborg)", fetch_nfbio),
    ("Volume Village", fetch_volumevillage),
    ("Godsbanen", fetch_godsbanen),
    ("Beboernes Hus", fetch_beboerneshus),
    ("AROS", fetch_aros),
    ("Kunsthal Aarhus", fetch_kunsthal_aarhus),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=180, help="Hent events frem til N dage fra i dag (default: 180)")
    parser.add_argument("--out-dir", default=".", help="Mappe hvor .ics-filerne skrives (default: nuværende mappe)")
    parser.add_argument(
        "--no-image-enrichment", action="store_true",
        help="Spring hentning af og:image for events uden billede over (hurtigere, til lokal test)",
    )
    args = parser.parse_args()

    cutoff = datetime.now() + timedelta(days=args.days)
    print(f"Henter events i Aarhus frem til {cutoff.date()} ...", file=sys.stderr)

    all_events = []
    for name, fetcher in SOURCES:
        try:
            all_events.extend(fetcher(cutoff))
        except Exception as e:
            print(f"  ADVARSEL: kunne ikke hente fra {name}: {e}", file=sys.stderr)

    print("Henter seneste Heromkring-udgaver (kun titel + link pr. udgave) ...", file=sys.stderr)
    heromkring_posts = fetch_heromkring_posts(limit=4)
    print(f"  Heromkring: {len(heromkring_posts)} udgaver", file=sys.stderr)

    if heromkring_posts:
        try:
            heromkring_events = fetch_heromkring_events(heromkring_posts[0]["url"], cutoff)
            print(f"  Heromkring (fakta-udtræk, kun til privat brug): {len(heromkring_events)} events", file=sys.stderr)
            all_events.extend(heromkring_events)
        except Exception as e:
            print(f"  ADVARSEL: kunne ikke udtrække Heromkring-events: {e}", file=sys.stderr)

    before = len(all_events)
    deduped = dedupe(all_events)
    print(f"I alt {before} events hentet, {len(deduped)} tilbage efter dedupe", file=sys.stderr)

    if not args.no_image_enrichment:
        enrich_missing_images(deduped)

    def write(filename, events, calname, caldesc):
        ics = build_ics(events, calname, caldesc)
        path = f"{args.out_dir.rstrip('/')}/{filename}"
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(ics)
        print(f"  {filename}: {len(events)} events", file=sys.stderr)

    period_events = [ev for ev in deduped if is_period_event(ev)]
    single_events = [ev for ev in deduped if not is_period_event(ev)]

    write("alt.ics", single_events, "Det sker i Aarhus", "Enkeltstående begivenheder i Aarhus (lange perioder ligger i perioder.ics)")
    write("perioder.ics", period_events, "Aarhus – Perioder & Udstillinger", "Langvarige begivenheder i Aarhus (udstillinger, skøjtebane m.m.) der strækker sig over mere end en uge")
    print(f"  perioder.ics: {len(period_events)} events", file=sys.stderr)

    by_category = {}
    for ev in deduped:
        by_category.setdefault(ev.category, []).append(ev)

    categories_meta = []
    for category, evs in sorted(by_category.items()):
        slug = slugify(category)
        write(f"{slug}.ics", evs, f"Aarhus – {category}", f"{category} i Aarhus")
        categories_meta.append({"name": category, "slug": slug, "count": len(evs)})

    print("\nKategorier:", file=sys.stderr)
    for category, evs in sorted(by_category.items(), key=lambda kv: -len(kv[1])):
        print(f"  {category}: {len(evs)}", file=sys.stderr)

    free_events = [ev for ev in deduped if ev.is_free]
    write("gratis.ics", free_events, "Aarhus – Gratis", "Gratis begivenheder i Aarhus, på tværs af kategorier")
    print(f"  Gratis (på tværs af kategorier): {len(free_events)}", file=sys.stderr)

    recurring_events = [ev for ev in deduped if is_recurring_or_long_event(ev)]
    write("gentagende-langvarigt.ics", recurring_events, "Aarhus – Gentagende/langvarigt", "Begivenheder i Aarhus der gentager sig eller strækker sig over lang tid, på tværs af kategorier")
    print(f"  Gentagende/langvarigt (på tværs af kategorier): {len(recurring_events)}", file=sys.stderr)

    # JSON til hjemmesiden (events.html), så den kan vise/filtrere listen i browseren.
    json_path = f"{args.out_dir.rstrip('/')}/events.json"

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "categories": categories_meta,
        "events": [
            {
                "uid": ev.uid,
                "title": ev.title,
                "start": ev.start.isoformat(),
                "end": ev.end.isoformat(),
                "all_day": ev.all_day,
                "location": ev.location,
                "url": ev.url,
                "source": ev.source,
                "category": ev.category,
                "category_slug": slugify(ev.category),
                "image": ev.image,
                "is_free": ev.is_free,
                "is_period": is_period_event(ev),
                "is_recurring_or_long": is_recurring_or_long_event(ev),
            }
            for ev in sorted(deduped, key=lambda e: e.start)
        ],
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"  events.json: {len(payload['events'])} events", file=sys.stderr)


if __name__ == "__main__":
    main()
