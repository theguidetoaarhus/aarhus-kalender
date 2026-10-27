const STORAGE_KEY = "aarhus-kalender:valgte-kategorier";
const FREE_STORAGE_KEY = "aarhus-kalender:kun-gratis";
const RECURRING_STORAGE_KEY = "aarhus-kalender:kun-gentagende";


// Tom markering betyder "intet kategori-filter aktivt" => vis alt. Det er
// derfor IKKE det samme som at vælge alle kategorier fra (se renderEvents) -
// en valgt kategori smalner ned (OR mellem de valgte), ingen valgte betyder
// ingen begrænsning.
function loadSelection(allNames) {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    // Førstegangsbesøg: alt er slået til undtagen Film, da der er så mange
    // filmvisninger hele tiden, at de ellers fylder det meste af listen.
    if (!raw) return new Set(allNames.filter((name) => name !== "Film"));
    const saved = JSON.parse(raw);
    return new Set(saved.filter((name) => allNames.includes(name)));
  } catch (e) {
    return new Set(allNames.filter((name) => name !== "Film"));
  }
}

function saveSelection(selected) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([...selected]));
  } catch (e) {
    // ignorer - fx private browsing
  }
}

function loadOnlyFree() {
  try {
    return localStorage.getItem(FREE_STORAGE_KEY) === "1";
  } catch (e) {
    return false;
  }
}

function saveOnlyFree(value) {
  try {
    localStorage.setItem(FREE_STORAGE_KEY, value ? "1" : "0");
  } catch (e) {
    // ignorer
  }
}


function loadOnlyRecurring() {
  try {
    return localStorage.getItem(RECURRING_STORAGE_KEY) === "1";
  } catch (e) {
    return false;
  }
}

function saveOnlyRecurring(value) {
  try {
    localStorage.setItem(RECURRING_STORAGE_KEY, value ? "1" : "0");
  } catch (e) {
    // ignorer
  }
}

function formatDayHeading(dateStr) {
  const d = new Date(dateStr + "T00:00:00");
  const weekday = d.toLocaleDateString("da-DK", { weekday: "long" });
  const rest = d.toLocaleDateString("da-DK", { day: "numeric", month: "long" });
  return `${weekday.charAt(0).toUpperCase() + weekday.slice(1)} ${rest}`;
}

function formatTime(iso) {
  const d = new Date(iso);
  return d.toLocaleTimeString("da-DK", { hour: "2-digit", minute: "2-digit" });
}

function formatShortDate(iso) {
  const d = new Date(iso.slice(0, 10) + "T00:00:00");
  return d.toLocaleDateString("da-DK", { day: "numeric", month: "short" });
}

function formatPeriodRange(ev) {
  // Perioder er "slut-eksklusive" (DTEND i ics er dagen efter sidste dag),
  // så vi trækker en dag fra for at vise den faktiske sidste dag.
  const endInclusive = new Date(ev.end.slice(0, 10) + "T00:00:00");
  endInclusive.setDate(endInclusive.getDate() - 1);
  return `${formatShortDate(ev.start)} – ${endInclusive.toLocaleDateString("da-DK", { day: "numeric", month: "short" })}`;
}

// Mandag i ugen der indeholder `d`.
function startOfWeek(d) {
  const date = new Date(d);
  const day = date.getDay(); // 0 = søndag
  const diff = day === 0 ? -6 : 1 - day;
  date.setDate(date.getDate() + diff);
  date.setHours(0, 0, 0, 0);
  return date;
}

function isoWeekNumber(d) {
  const date = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  const day = date.getUTCDay() || 7;
  date.setUTCDate(date.getUTCDate() + 4 - day);
  const yearStart = new Date(Date.UTC(date.getUTCFullYear(), 0, 1));
  return Math.ceil(((date - yearStart) / 86400000 + 1) / 7);
}

function startOfDay(d) {
  const x = new Date(d);
  x.setHours(0, 0, 0, 0);
  return x;
}
function addDays(d, n) {
  const x = new Date(d);
  x.setDate(x.getDate() + n);
  return x;
}
function addMonths(d, n) {
  const x = new Date(d);
  x.setMonth(x.getMonth() + n);
  return x;
}
function toDateKey(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function shortDate(d) {
  return d.toLocaleDateString("da-DK", { day: "numeric", month: "short" }).toUpperCase();
}

const VIEW_MODES = [
  { key: "day", label: "Dag" },
  { key: "week", label: "Uge" },
  { key: "month", label: "Måned" },
  { key: "year", label: "År" },
];

// Beregner [start, slut[ for det valgte visningsniveau + offset fra i dag.
function getDateRange(mode, offset) {
  const today = startOfDay(new Date());
  if (mode === "day") {
    const start = addDays(today, offset);
    return [start, addDays(start, 1)];
  }
  if (mode === "week") {
    const start = addDays(startOfWeek(today), offset * 7);
    return [start, addDays(start, 7)];
  }
  if (mode === "month") {
    const base = addMonths(new Date(today.getFullYear(), today.getMonth(), 1), offset);
    const start = new Date(base.getFullYear(), base.getMonth(), 1);
    const end = new Date(base.getFullYear(), base.getMonth() + 1, 1);
    return [start, end];
  }
  const y = today.getFullYear() + offset;
  return [new Date(y, 0, 1), new Date(y + 1, 0, 1)];
}

function formatDateRangeLabel(mode, offset) {
  const [start, end] = getDateRange(mode, offset);
  if (mode === "day") {
    const weekday = start.toLocaleDateString("da-DK", { weekday: "long" }).toUpperCase();
    return `${weekday} · ${shortDate(start)}`;
  }
  if (mode === "week") {
    const lastDay = addDays(end, -1);
    return `UGE ${isoWeekNumber(start)} · ${shortDate(start)}–${shortDate(lastDay)}`;
  }
  if (mode === "month") {
    return start.toLocaleDateString("da-DK", { month: "long", year: "numeric" }).toUpperCase();
  }
  return `${start.getFullYear()}`;
}

async function main() {
  const categoriesEl = document.getElementById("categories");
  const categoriesToggleEl = document.getElementById("categoriesToggle");
  const viewSwitcherEl = document.getElementById("viewSwitcher");
  const dateStepperEl = document.getElementById("dateStepper");
  const subscribeEl = document.getElementById("subscribeLinks");
  const subscribeToggleEl = document.getElementById("subscribeToggle");
  const listEl = document.getElementById("eventList");
  const footerEl = document.getElementById("footer");

  let data;
  try {
    const res = await fetch("events.json", { cache: "no-store" });
    data = await res.json();
  } catch (e) {
    categoriesEl.textContent = "Kunne ikke hente events.json.";
    return;
  }

  const allNames = data.categories.map((c) => c.name);
  let selected = loadSelection(allNames);
  let onlyFree = loadOnlyFree();
  let onlyRecurring = loadOnlyRecurring();

  // Visningsniveau (dag/uge/måned/år) + hvor mange enheder frem/tilbage fra i dag.
  let dateMode = "day";
  let dateOffset = 0;

  // Events der overlapper det aktuelt valgte dag/uge/måned/år-interval - bruges
  // både til selve listen og til optællingerne ved hvert filter, så tallene
  // altid viser hvor mange muligheder der faktisk er i den valgte periode.
  function eventsInDateRange() {
    const [rangeStart, rangeEnd] = getDateRange(dateMode, dateOffset);
    return data.events.filter((ev) => {
      const evStart = new Date(ev.start);
      const evEnd = new Date(ev.end);
      return !(evEnd <= rangeStart || evStart >= rangeEnd);
    });
  }

  function renderViewSwitcher() {
    viewSwitcherEl.innerHTML = "";
    VIEW_MODES.forEach((mode) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = mode.key === dateMode ? "active" : "";
      btn.textContent = mode.label;
      btn.addEventListener("click", () => {
        if (dateMode === mode.key) return;
        dateMode = mode.key;
        dateOffset = 0;
        renderViewSwitcher();
        renderDateStepper();
        renderCategories();
        renderEvents();
      });
      viewSwitcherEl.appendChild(btn);
    });
  }

  function renderDateStepper() {
    dateStepperEl.innerHTML = "";

    const prevBtn = document.createElement("button");
    prevBtn.type = "button";
    prevBtn.textContent = "‹";
    prevBtn.addEventListener("click", () => {
      dateOffset -= 1;
      renderDateStepper();
      renderCategories();
      renderEvents();
    });

    const label = document.createElement("span");
    label.className = "date-label";
    label.textContent = formatDateRangeLabel(dateMode, dateOffset);

    const nextBtn = document.createElement("button");
    nextBtn.type = "button";
    nextBtn.textContent = "›";
    nextBtn.addEventListener("click", () => {
      dateOffset += 1;
      renderDateStepper();
      renderCategories();
      renderEvents();
    });

    dateStepperEl.append(prevBtn, label, nextBtn);
  }

  function renderCategories() {
    categoriesEl.innerHTML = "";
    const rangeEvents = eventsInDateRange();

    // "Gratis" er ikke en rigtig kategori (en gratis koncert er stadig Musik &
    // Koncerter), men vises her på linje med de andre, fordi det opleves som
    // et naturligt filter-valg. Den virker på tværs af alle valgte kategorier.
    const freeCount = rangeEvents.filter((e) => e.is_free).length;
    const freeLabel = document.createElement("label");
    freeLabel.className = "cat-toggle" + (onlyFree ? " active" : "");
    const freeDot = document.createElement("span");
    freeDot.className = "dot";
    const freeCheckbox = document.createElement("input");
    freeCheckbox.type = "checkbox";
    freeCheckbox.checked = onlyFree;
    freeCheckbox.addEventListener("change", () => {
      onlyFree = freeCheckbox.checked;
      saveOnlyFree(onlyFree);
      renderCategories();
      renderSubscribeLinks();
      renderEvents();
    });
    const freeText = document.createElement("span");
    freeText.textContent = "Gratis";
    const freeCountEl = document.createElement("span");
    freeCountEl.className = "count";
    freeCountEl.textContent = freeCount;
    freeLabel.append(freeDot, freeCheckbox, freeText, freeCountEl);
    categoriesEl.appendChild(freeLabel);

    // "Gentagende/langvarigt" er heller ikke en rigtig kategori - det er et
    // tværgående filter for events der ikke er engangsoplevelser (lange
    // perioder eller faste gentagelser som fx "hver mandag").
    const recurringCount = rangeEvents.filter((e) => e.is_recurring_or_long).length;
    const recurringLabel = document.createElement("label");
    recurringLabel.className = "cat-toggle" + (onlyRecurring ? " active" : "");
    const recurringDot = document.createElement("span");
    recurringDot.className = "dot";
    const recurringCheckbox = document.createElement("input");
    recurringCheckbox.type = "checkbox";
    recurringCheckbox.checked = onlyRecurring;
    recurringCheckbox.addEventListener("change", () => {
      onlyRecurring = recurringCheckbox.checked;
      saveOnlyRecurring(onlyRecurring);
      renderCategories();
      renderSubscribeLinks();
      renderEvents();
    });
    const recurringText = document.createElement("span");
    recurringText.textContent = "Gentagende/langvarigt";
    const recurringCountEl = document.createElement("span");
    recurringCountEl.className = "count";
    recurringCountEl.textContent = recurringCount;
    recurringLabel.append(recurringDot, recurringCheckbox, recurringText, recurringCountEl);
    categoriesEl.appendChild(recurringLabel);

    data.categories.forEach((cat) => {
      const isActive = selected.has(cat.name);
      const label = document.createElement("label");
      label.className = "cat-toggle" + (isActive ? " active" : "");

      const dot = document.createElement("span");
      dot.className = "dot";

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = isActive;
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) selected.add(cat.name);
        else selected.delete(cat.name);
        saveSelection(selected);
        renderCategories();
        renderSubscribeLinks();
        renderEvents();
      });
      const text = document.createElement("span");
      text.textContent = cat.name;
      const count = document.createElement("span");
      count.className = "count";
      count.textContent = rangeEvents.filter((e) => e.category === cat.name).length;
      label.append(dot, checkbox, text, count);
      categoriesEl.appendChild(label);
    });
  }

  function renderSubscribeLinks() {
    subscribeEl.innerHTML = "";
    const base = new URL(".", window.location.href);

    const allHttpsUrl = new URL("alt.ics", base).href;
    const allA = document.createElement("a");
    allA.className = "subscribe-all";
    allA.href = allHttpsUrl.replace(/^https?:\/\//, "webcal://");
    allA.textContent = "Abonnér på alt";
    allA.title = allHttpsUrl + " (enkeltstående begivenheder, uden lange perioder)";
    subscribeEl.appendChild(allA);

    const periodHttpsUrl = new URL("perioder.ics", base).href;
    const periodA = document.createElement("a");
    periodA.href = periodHttpsUrl.replace(/^https?:\/\//, "webcal://");
    periodA.textContent = "Kun perioder (udstillinger mv.)";
    periodA.title = periodHttpsUrl;
    subscribeEl.appendChild(periodA);

    if (onlyFree) {
      const freeHttpsUrl = new URL("gratis.ics", base).href;
      const freeA = document.createElement("a");
      freeA.href = freeHttpsUrl.replace(/^https?:\/\//, "webcal://");
      freeA.textContent = "Kun gratis";
      freeA.title = freeHttpsUrl;
      subscribeEl.appendChild(freeA);
    }

    if (onlyRecurring) {
      const recurringHttpsUrl = new URL("gentagende-langvarigt.ics", base).href;
      const recurringA = document.createElement("a");
      recurringA.href = recurringHttpsUrl.replace(/^https?:\/\//, "webcal://");
      recurringA.textContent = "Kun gentagende/langvarigt";
      recurringA.title = recurringHttpsUrl;
      subscribeEl.appendChild(recurringA);
    }

    data.categories
      .filter((cat) => selected.size === 0 || selected.has(cat.name))
      .forEach((cat) => {
        const httpsUrl = new URL(`${cat.slug}.ics`, base).href;
        const webcalUrl = httpsUrl.replace(/^https?:\/\//, "webcal://");
        const a = document.createElement("a");
        a.href = webcalUrl;
        a.textContent = cat.name;
        a.title = httpsUrl;
        subscribeEl.appendChild(a);
      });
  }

  subscribeToggleEl.addEventListener("click", (e) => {
    e.stopPropagation();
    subscribeEl.classList.toggle("open");
  });
  categoriesToggleEl.addEventListener("click", (e) => {
    e.stopPropagation();
    categoriesEl.classList.toggle("open");
  });
  document.addEventListener("click", (e) => {
    if (!subscribeEl.contains(e.target) && e.target !== subscribeToggleEl) {
      subscribeEl.classList.remove("open");
    }
    if (!categoriesEl.contains(e.target) && e.target !== categoriesToggleEl) {
      categoriesEl.classList.remove("open");
    }
  });

  function renderEvents() {
    listEl.innerHTML = "";

    const [rangeStart] = getDateRange(dateMode, dateOffset);

    const events = eventsInDateRange().filter((ev) => {
      if ((selected.size > 0 && !selected.has(ev.category)) || (onlyFree && !ev.is_free) || (onlyRecurring && !ev.is_recurring_or_long)) return false;
      return true;
    });

    if (events.length === 0) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.textContent = "Ingen begivenheder matcher dine valg.";
      listEl.appendChild(empty);
      return;
    }

    const rangeStartKey = toDateKey(rangeStart);
    const byDay = new Map();
    events.forEach((ev) => {
      // En periode der startede før det valgte interval, skal stadig vises
      // under intervallets første dag (ellers ville den slet ikke dukke op).
      let day = ev.start.slice(0, 10);
      if (new Date(ev.start) < rangeStart) {
        day = rangeStartKey;
      }
      if (!byDay.has(day)) byDay.set(day, []);
      byDay.get(day).push(ev);
    });

    [...byDay.keys()].sort().forEach((day) => {
      const group = document.createElement("div");
      group.className = "day-group";

      const heading = document.createElement("div");
      heading.className = "day-heading";
      heading.textContent = formatDayHeading(day);
      group.appendChild(heading);

      const grid = document.createElement("div");
      grid.className = "card-grid";

      byDay.get(day).forEach((ev) => {
        const card = document.createElement("a");
        card.className = "card";
        card.href = ev.url || "#";
        card.target = "_blank";
        card.rel = "noopener";

        const media = document.createElement("div");
        media.className = "card-media";
        const mediaImg = document.createElement("div");
        mediaImg.className = "card-media-img";
        if (ev.image) {
          mediaImg.style.backgroundImage = `url("${ev.image}")`;
        } else {
          const icon = document.createElement("div");
          icon.className = "placeholder-icon mono";
          icon.textContent = ev.category;
          mediaImg.appendChild(icon);
        }
        media.appendChild(mediaImg);

        const timeBadge = document.createElement("div");
        if (ev.is_period) {
          timeBadge.className = "card-time period";
          timeBadge.textContent = formatPeriodRange(ev);
          media.classList.add("has-period");
        } else {
          timeBadge.className = "card-time";
          timeBadge.textContent = ev.all_day ? "Hele dagen" : formatTime(ev.start);
        }
        media.appendChild(timeBadge);

        if (ev.is_free) {
          const freeBadge = document.createElement("div");
          freeBadge.className = "card-free-badge";
          freeBadge.textContent = "Gratis";
          media.appendChild(freeBadge);
        }

        card.append(media);

        const body = document.createElement("div");
        body.className = "card-body";
        const title = document.createElement("div");
        title.className = "card-title";
        title.textContent = ev.title;
        body.append(title);
        if (ev.source === "Heromkring" || ev.source === "Aarhus Inside") {
          const sourceTag = document.createElement("div");
          sourceTag.className = "card-source mono";
          sourceTag.textContent = `via ${ev.source}`;
          body.append(sourceTag);
        }
        card.append(body);

        grid.appendChild(card);
      });

      group.appendChild(grid);
      listEl.appendChild(group);
    });
  }

  renderCategories();
  renderViewSwitcher();
  renderDateStepper();
  renderSubscribeLinks();
  renderEvents();

  if (data.generated_at) {
    const d = new Date(data.generated_at);
    footerEl.textContent = `Opdateret ${d.toLocaleString("da-DK")} · ${data.events.length} begivenheder i alt`;
  }
}

main();
