(() => {
  "use strict";

  const ROUTES = ["summary", "objectives", "risk", "constraints", "preferences", "coherence", "search", "whatif", "conversation"];
  const DEFAULT_ROUTE = "summary";
  const SAMPLE_STAMP = "Sample data";
  const SAMPLE_PHRASE = "illustrative sample data";
  const REAL_PHRASE = "fictional persona data";
  const NAV_TABS = ["Accounts", "Analysis", "Markets", "Activities", "Documents", "Research"];
  const ANALYSIS_TAB_LABEL = "Analysis";
  const ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"};
  const STATUS_LABEL = {pass: "Aligned", warning: "Warning", violation: "Misaligned", info: "Info", unknown: "Unknown"};
  const STATUS_ICON = {pass: "icon-check", warning: "icon-warn", violation: "icon-x", info: "icon-info", unknown: "icon-info"};
  const NUMBER_FORMATS = new Map();

  /* ---------- formatting ---------- */

  function esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, char => ESCAPES[char]);
  }

  function localNumber(value, digits) {
    const decimals = Math.min(20, Math.max(0, Number(digits) || 0));
    if (!NUMBER_FORMATS.has(decimals)) {
      NUMBER_FORMATS.set(decimals, new Intl.NumberFormat("en-CA", {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals
      }));
    }
    return NUMBER_FORMATS.get(decimals).format(value);
  }

  function isMissing(value) {
    return value === null || value === undefined || value === "" || !Number.isFinite(Number(value));
  }

  function fmtCad(value, digits = 0) {
    if (isMissing(value)) return "—";
    return localNumber(Number(value), digits);
  }

  /* Fractions below 1 are multiplied by 100; values of 1 or more are already percentages.
     digits is the number of decimals; with the default 0 a non-whole percentage keeps one
     decimal (0.168 -> "16.8%"), so whole percentages stay clean (22 -> "22%").
     Inputs are fractions (vol, drivers, shares) or already-percent values (weights_pct,
     max_drawdown_pct, targets); the `<1` test assumes a fraction, so a percent-unit value
     that falls below 1 would be scaled twice. */
  function fmtPct(value, digits = 0) {
    if (isMissing(value)) return "—";
    const number = Number(value);
    const percent = Math.abs(number) < 1 ? number * 100 : number;
    const wanted = Math.max(0, Number(digits) || 0);
    const isWhole = Math.abs(percent - Number(percent.toFixed(0))) < 1e-9;
    const places = wanted === 0 && !isWhole ? 1 : wanted;
    return localNumber(percent, places) + "%";
  }

  function chip(status) {
    const key = Object.prototype.hasOwnProperty.call(STATUS_LABEL, status) ? status : "unknown";
    return '<span class="st st--' + key + '">'
      + '<img class="st__icon" src="assets/' + STATUS_ICON[key] + '.svg" alt="" width="16" height="16">'
      + '<span class="st__label">' + esc(STATUS_LABEL[key]) + '</span></span>';
  }

  /* ---------- shared screen helpers ---------- */

  /* Status severity, shared by every screen: higher is worse, and `unknown` ranks above
     `pass` so an unverifiable figure never reads as aligned. */
  const STATUS_RANK = {pass: 0, info: 1, unknown: 2, warning: 3, violation: 4};

  function worstStatus(statuses) {
    return (statuses || []).reduce(
      (worst, status) => ((STATUS_RANK[status] ?? 0) > (STATUS_RANK[worst] ?? 0) ? status : worst),
      "pass"
    );
  }

  /* Enum labels shared by the screens; screens that show prose derive lowercase forms. */
  const RISK_LEVELS = {
    low: "Low", low_moderate: "Low to moderate", moderate: "Moderate",
    moderate_high: "Moderate to high", high: "High"
  };
  const OBJECTIVE_LABELS = {
    capital_preservation_with_growth: "Preserve capital, some growth",
    capital_preservation_with_income: "Preserve capital, income",
    capital_preservation: "Preserve capital",
    balanced_growth: "Balanced growth",
    growth: "Growth",
    aggressive_growth: "Aggressive growth",
    income: "Income",
    education_funding: "Education funding"
  };

  /* Declared fact values: numbers become CAD amounts, enums go through the screen's label
     function (default: underscores to spaces). */
  function factText(value, label) {
    const format = typeof label === "function" ? label : text => text.replace(/_/g, " ");
    if (value === null || value === undefined || value === "") return "—";
    if (Array.isArray(value)) {
      const parts = value.map(item => factText(item, label)).filter(part => part && part !== "—");
      return parts.length ? parts.join("; ") : "—";
    }
    if (typeof value === "object") {
      const parts = Object.keys(value).map(key => factText(value[key], label)).filter(Boolean);
      return parts.length ? parts.join(" · ") : "—";
    }
    if (typeof value === "number") return fmtCad(value, 2).replace(/\.00$/, "").replace("-", "−");
    return format(String(value));
  }

  function formatValue(value, unit) {
    if (value === null || value === undefined) return "—";
    if (typeof value === "string") return value;
    if (!Number.isFinite(Number(value))) return "—";
    const numeric = Number(value);
    if (unit === "cad") return "$" + fmtCad(numeric);
    if (unit === "weight" || unit === "annual_vol" || unit === "drawdown" || unit === "risk_share"
      || unit === "probability" || unit === "pct") {
      return fmtPct(numeric).replace(/^-/, "−");
    }
    return fmtCad(numeric, 0);
  }

  function checkRow(check, names) {
    const name = (names && names[check.id]) || check.id;
    const subject = check.subject ? ' <span class="muted">· ' + esc(check.subject) + "</span>" : "";
    const limit = check.limit === null || check.limit === undefined ? ""
      : ' <span class="muted">/ limit ' + esc(formatValue(check.limit, check.unit)) + "</span>";
    return '<li class="chk">'
      + '<span class="chk__id">' + esc(check.id) + "</span>"
      + '<span class="chk__body">' + esc(name) + subject + "</span>"
      + '<span class="chk__figs">' + esc(formatValue(check.value, check.unit)) + limit + "</span>"
      + '<span class="chk__st">' + chip(check.status) + "</span></li>";
  }

  /* Constraint and preference cards share one card builder each; the screen supplies its
     own status icons, labels and intro copy. */
  function widthOf(part, whole) {
    if (!Number.isFinite(part) || !Number.isFinite(whole) || whole <= 0) return "0%";
    const ratio = Math.min(1, Math.max(0, part / whole));
    return (ratio * 100).toFixed(1) + "%";
  }

  function meterHtml(rule) {
    const scale = Number(rule.scale);
    const limitWidth = rule.limit === null || rule.limit === undefined ? "" : widthOf(rule.limit, scale);
    return '<div class="meter meter--' + esc(rule.status) + '">'
      + '<div class="meter__plot">'
      + (limitWidth
        ? '<span class="meter__limit" style="left:' + limitWidth + '">Limit ' + fmtPct(rule.limit) + "</span>"
        : "")
      + '<div class="meter__track"><span class="meter__fill" style="width:' + widthOf(rule.value, scale) + '"></span></div>'
      + (limitWidth ? '<span class="meter__tick" style="left:' + limitWidth + '"></span>' : "")
      + "</div>"
      + '<p class="meter__caption">' + esc(rule.caption) + "</p>"
      + "</div>";
  }

  function holdsHtml(rule) {
    let squares = "";
    for (let index = 0; index < rule.total; index += 1) {
      squares += '<span class="holds__sq' + (index < rule.flagged ? " holds__sq--flag" : "") + '"></span>';
    }
    return '<div class="holds" aria-hidden="true">' + squares + "</div>"
      + '<p class="t-small muted">' + esc(rule.caption) + "</p>"
      + (rule.note ? '<p class="t-small muted">' + esc(rule.note) + "</p>" : "");
  }

  function figureHtml(rule) {
    return '<p class="figure num">' + esc(rule.figure) + "</p>"
      + '<p class="t-small muted">' + esc(rule.caption) + "</p>";
  }

  function ruleCardHtml(rule, iconFor) {
    const body = rule.kind === "meter" ? meterHtml(rule)
      : rule.kind === "holds" ? holdsHtml(rule) : figureHtml(rule);
    return '<article class="card rule-card">'
      + '<div class="rule-head">'
      + '<img class="i28" src="assets/' + iconFor(rule.status) + '.svg" alt="" width="28" height="28">'
      + '<p class="t-body-strong rule-head__title">' + esc(rule.title) + "</p>"
      + "</div>"
      + chip(rule.status)
      + body
      + "</article>";
  }

  const SEGMENT_KIND_LABEL = {fact: "Fact", assumption: "Assumption", inference: "Inference", unknown: "Unknown"};

  function humanizeValue(value, labels) {
    if (value === true) return "Yes";
    if (value === false) return "No";
    if (Array.isArray(value)) {
      return value.length ? value.map(item => humanizeValue(item, labels)).join(", ") : "None";
    }
    if (value && typeof value === "object") {
      return Object.keys(value).map(key => humanizeValue(key, labels) + ": " + humanizeValue(value[key], labels)).join(", ");
    }
    if (value === null || value === undefined || value === "") return "—";
    const text = String(value);
    if (labels && Object.prototype.hasOwnProperty.call(labels, text)) return labels[text];
    return text.replace(/_/g, " ").replace(/^./, char => char.toUpperCase());
  }

  function factsHtml(declared, fieldLabels, valueLabels, emptyText) {
    const paths = Object.keys(declared || {});
    if (!paths.length) return '<p class="t-small muted">' + esc(emptyText) + "</p>";
    return '<dl class="facts">' + paths.map(path => '<div class="facts__row">'
      + '<dt class="t-small label">' + esc((fieldLabels && fieldLabels[path]) || path) + "</dt>"
      + '<dd class="num">' + esc(humanizeValue(declared[path], valueLabels)) + "</dd>"
      + "</div>").join("") + "</dl>";
  }

  function segmentsHtml(section) {
    const segments = (section.narrative && section.narrative.segments) || [];
    if (!segments.length) return '<p class="t-small muted">No written explanation is attached to this section.</p>';
    return '<ul class="segs">' + segments.map(segment => '<li class="seg">'
      + '<span class="seg__kind">' + esc(SEGMENT_KIND_LABEL[segment.kind] || segment.kind) + "</span>"
      + '<div class="seg__body"><p class="t-body">' + esc(segment.text) + "</p>"
      + ((segment.refs || []).length
        ? '<p class="t-small seg__refs">' + esc((segment.refs || []).join(", ")) + "</p>"
        : "")
      + "</div></li>").join("") + "</ul>";
  }

  /* ---------- navigation ---------- */

  function defaultNavSpec() {
    return {
      tabs: NAV_TABS.map((label, index) => ({label, href: "#", active: index === 1})),
      chrome: "full"
    };
  }

  function tabMarkup(tab) {
    const active = tab.active === true;
    const href = tab.href || (active ? "#/" + DEFAULT_ROUTE : "#");
    const icon = active ? "icon-chev-active" : "icon-chev";
    const attributes = active ? ' aria-current="page"' : ' aria-disabled="true" tabindex="-1"';
    return '<a class="tab' + (active ? " tab--active" : "") + '" href="' + esc(href) + '"' + attributes + '>'
      + '<span class="tab__label">' + esc(tab.label || "") + '</span>'
      + '<img class="tab__chev" src="assets/' + icon + '.svg" alt="" width="14" height="14"></a>';
  }

  function setNav(route) {
    const spec = App.navSpec[route] || defaultNavSpec();
    const nav = document.getElementById("app-nav");
    const tabs = document.getElementById("app-tabs");
    if (!nav || !tabs) return;
    nav.hidden = spec.chrome === "bare";
    tabs.innerHTML = (spec.tabs || []).map(tabMarkup).join("");
  }

  function onTabClick(event) {
    const inert = event.target.closest('a[aria-disabled="true"]');
    if (inert) event.preventDefault();
  }

  /* ---------- router ---------- */

  function routeFromHash() {
    const raw = location.hash.replace(/^#\/?/, "").split("?")[0];
    if (ROUTES.indexOf(raw) >= 0) return raw;
    if (raw) {
      try {
        history.replaceState(null, "", "#/" + DEFAULT_ROUTE);
      } catch (error) {
        /* Some file:// contexts refuse history updates; the summary screen still renders. */
      }
    }
    return DEFAULT_ROUTE;
  }

  function routeParams() {
    const at = location.hash.indexOf("?");
    return new URLSearchParams(at >= 0 ? location.hash.slice(at + 1) : "");
  }

  function showRenderError(section, error) {
    const screen = section.querySelector(".scr");
    const message = error && error.message ? error.message : String(error);
    if (screen) {
      screen.innerHTML = '<div class="card"><p class="t-body-strong">This screen could not be rendered.</p>'
        + '<p class="t-small muted">' + esc(message) + '</p></div>';
    }
    console.error("[app] renderer failed for route", App.route, error);
  }

  function render() {
    App.route = routeFromHash();
    const sections = document.querySelectorAll("section[data-route]");
    sections.forEach(section => { section.hidden = section.dataset.route !== App.route; });
    setNav(App.route);
    updateChrome();
    const section = document.querySelector('section[data-route="' + App.route + '"]');
    const renderer = App.renderers[App.route];
    if (!section || typeof renderer !== "function") return;
    try {
      renderer(section);
    } catch (error) {
      showRenderError(section, error);
    }
  }

  function go(hash) {
    const target = String(hash ?? "");
    const next = target.startsWith("#") ? target : "#/" + target.replace(/^\/+/, "");
    if (location.hash === next) {
      render();
    } else {
      location.hash = next;
    }
    window.scrollTo(0, 0);
  }

  /* ---------- mode stamps and user chip ---------- */

  function stampText(original) {
    if (original.trim() === SAMPLE_STAMP) {
      const origin = App.state.origin === "live" ? "live" : "prebuilt";
      const suffix = App.state.fellBack ? " (live analysis unavailable)" : "";
      return "Fictional persona · " + origin + " analysis" + suffix;
    }
    return original.replace(SAMPLE_PHRASE, REAL_PHRASE);
  }

  function updateChrome() {
    const hasData = Boolean(App.state && App.state.report);
    document.querySelectorAll("[data-mode-stamp]").forEach(element => {
      if (element.dataset.stampOriginal === undefined) element.dataset.stampOriginal = element.textContent;
      element.textContent = hasData ? stampText(element.dataset.stampOriginal) : element.dataset.stampOriginal;
    });
    const chip = document.querySelector(".user-chip span");
    const person = App.state.personas.find(item => item.id === App.state.persona);
    if (chip && person) chip.textContent = person.name;
  }

  /* ---------- data and state ---------- */

  function applyState(next) {
    Object.assign(App.state, next);
    App.data = {
      index: App.source.staticIndex(),
      report: App.state.report,
      universe: App.state.universe,
      whatif: App.state.whatif
    };
  }

  function firstEntryFile(persona) {
    return persona && persona.entries.length ? persona.entries[0].file : null;
  }

  /* Only the most recent persona load may apply its result. */
  let personaLoadSeq = 0;

  /* Loads report, universe and what-if for one persona without rendering. Throws on a
     report failure; universe and what-if may be null (screens fall back to sample data).
     Resolves false when a newer load started meanwhile and this result was dropped. */
  async function loadPersona(personaId, file, rerun) {
    personaLoadSeq += 1;
    const token = personaLoadSeq;
    const person = App.state.personas.find(item => item.id === personaId);
    const entryFile = file || firstEntryFile(person);
    const [report, universe, whatif] = await Promise.all([
      rerun ? App.source.rerun(personaId, entryFile) : App.source.report(personaId, entryFile),
      App.source.universe(personaId),
      App.source.whatif(personaId)
    ]);
    if (token !== personaLoadSeq) return false;
    applyState({
      persona: personaId, entryFile, report, universe, whatif, selection: [],
      origin: App.source.mode, fellBack: App.source.isFallback(report)
    });
    return true;
  }

  async function setPersona(personaId, file) {
    if (await loadPersona(personaId, file, false)) render();
  }

  function setReport(report, origin) {
    applyState({
      report,
      origin: origin || App.source.mode,
      fellBack: origin ? false : App.source.isFallback(report)
    });
    render();
  }

  function setSelection(tickers) {
    App.state.selection = Array.isArray(tickers) ? tickers.slice() : [];
    render();
  }

  async function rerunAnalysis() {
    const personaId = App.state.persona;
    const report = await App.source.rerun(personaId, App.state.entryFile);
    if (App.state.persona === personaId) setReport(report);
    return report;
  }

  async function loadData() {
    await App.source.init();
    const personas = await App.source.personas();
    App.state.personas = personas;
    if (personas.length) {
      try {
        await loadPersona(personas[0].id, null, false);
      } catch (error) {
        console.error("[app] could not load the first persona", error);
      }
    }
    if (!App.data) applyState({});
    render();
    return App.data;
  }

  /* ---------- public contract ---------- */

  const App = window.App = {
    data: null,
    state: {persona: null, entryFile: null, report: null, universe: null, whatif: null, selection: [],
      personas: [], origin: null, fellBack: false},
    source: null,
    route: DEFAULT_ROUTE,
    renderers: {},
    navSpec: {},
    esc,
    fmtCad,
    fmtPct,
    chip,
    statusLabel: STATUS_LABEL,
    statusRank: STATUS_RANK,
    worstStatus,
    factText,
    formatValue,
    checkRow,
    riskLevels: RISK_LEVELS,
    objectiveLabels: OBJECTIVE_LABELS,
    widthOf,
    meterHtml,
    holdsHtml,
    ruleCardHtml,
    humanizeValue,
    factsHtml,
    segmentsHtml,
    go,
    routeParams,
    setPersona,
    setReport,
    setSelection,
    rerunAnalysis,
    setNav,
    reload: loadData,
    ANALYSIS_TAB_LABEL,
    navTabs: defaultNavSpec
  };

  /* ---------- start ---------- */

  document.getElementById("app-tabs").addEventListener("click", onTabClick);
  window.addEventListener("hashchange", () => {
    render();
    window.scrollTo(0, 0);
  });
  render();
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", loadData);
  } else {
    loadData();
  }
})();
