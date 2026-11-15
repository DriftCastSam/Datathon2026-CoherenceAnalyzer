(() => {
  "use strict";

  const ROUTES = ["summary", "objectives", "risk", "constraints", "preferences", "coherence", "search", "whatif", "conversation", "trade"];
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

  /* Every check id the report can contain (SPEC §7.3/§7.4, docs/spec-factors.md §20.9 for C1-C5).
     A screen's own CHECK_NAMES (passed as `names` to checkRow) still wins; this is only the fallback,
     so no row ever shows a bare id like "H11". */
  const SHARED_CHECK_NAMES = {
    H1: "Excluded asset type", H2: "Crypto weight", H3: "Single-stock weight", H4: "Fixed income weight",
    H5: "Foreign currency weight", H6: "Employer sector weight", H7: "Annual volatility", H8: "Max drawdown",
    H9: "Pessimistic value at the horizon", H10: "Sector risk concentration", H11: "Excluded activity",
    H12: "Fees", H13: "Theme weight", H14: "Stated reaction vs history", S1: "Behaviour vs effective risk",
    S2: "Knowledge vs complexity", S3: "Hype pattern in the trade", S4: "Reminder from your notes",
    G1: "Goal probability", O1: "Objective vs portfolio",
    C1: "Goal beyond horizon", C2: "Max drawdown vs risk level", C3: "Growth objective vs low risk",
    C4: "Asset type in both allowed and refused", C5: "Reaction to a drop vs risk level"
  };

  /* Mirrors coherence_analyzer/explain.py's _EN template table (en-CA): the same deterministic fallback
     sentence the backend itself falls back to when narration fails, filled from the check's own fields.
     Kept in sync by hand; never invent new wording here. */
  const CHECK_TEMPLATES = {
    H1: "{subject} is an asset type you exclude ({value} of the portfolio).",
    H2: "Crypto assets weigh {value}, above the maximum of {limit}.",
    H3: "{subject} weighs {value}, above the maximum of {limit} per stock.",
    H4: "Fixed income weighs {value}, below the minimum of {limit}.",
    H5: "Foreign-currency assets weigh {value}, above the maximum of {limit}.",
    H6: "Your employer's sector ({subject}) weighs {value}, above the maximum of {limit}.",
    H7: "Historical volatility is {value}, above the band limit of {limit}.",
    "H7/info": "Historical volatility is {value}, below the lower bound of {limit} of the band for your profile.",
    H8: "The maximum historical drawdown is {value}, deeper than the limit of {limit}.",
    H9: "The low scenario (5th percentile) at the horizon is {value}, below the floor of {limit}.",
    H10: "The {subject} sector accounts for {value} of the risk, above the maximum of {limit}.",
    H11: "{subject} is linked to an activity you exclude.",
    H12: "The fees of {subject} are {value}, above the maximum of {limit}.",
    H13: "The {subject} theme weighs {value}, above the maximum of {limit}.",
    H14: "The historical drawdown is {value}, deeper than {limit}, while you say you would sell after a large drop.",
    S1: "Your observed behaviour exceeds your effective risk level by {value} level(s).",
    S2: "Your knowledge score is low for the complexity of some holdings.",
    S3: "The request looks like a purchase driven by current hype.",
    S4: "A rule written in your notes cannot be checked automatically.",
    G1: "Goal probabilities are indicative and rest on stated assumptions.",
    O1: "Your goal is capital preservation; the historical drawdown is {value}.",
    "H6/unknown": "H6: the ETF share ({value}) has no sector breakdown; the {limit} cap for your employer's "
      + "sector ({subject}) cannot be verified.",
    "H10/unknown": "H10: the “{subject}” share ({value} of the risk) has no sector breakdown; the "
      + "{limit} cap cannot be verified for it.",
    "?/unknown": "Check {id} cannot be verified{on}.",
    "?": "Check {id} has status {status}{on}."
  };

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

  function derivedFromHtml(check) {
    const chain = (check.derived_from || []).filter(Boolean);
    if (!chain.length) return "";
    return '<div class="chk__trace"><p class="t-small label">Comes from</p>'
      + '<ul class="chk__trace-list">' + chain.map(path => '<li>' + esc(path) + "</li>").join("") + "</ul></div>";
  }

  /* Fills a template string with a check's own fields, same placeholders as explain.py's _line(). */
  function fillTemplate(template, check) {
    const subject = check.subject || "";
    const fields = {
      id: check.id, status: check.status, subject,
      value: formatValue(check.value, check.unit), limit: formatValue(check.limit, check.unit),
      on: subject ? " (" + subject + ")" : ""
    };
    return template.replace(/\{(\w+)\}/g, (match, key) => (key in fields ? String(fields[key]) : match));
  }

  function templateFor(check) {
    const candidates = check.status === "unknown" ? [check.id + "/unknown", "?/unknown"]
      : [check.id + "/" + check.status, check.id, "?"];
    const key = candidates.find(candidate => Object.prototype.hasOwnProperty.call(CHECK_TEMPLATES, candidate));
    return fillTemplate(CHECK_TEMPLATES[key || "?"], check);
  }

  /* An AI narrative segment already said something about this check (its refs include the check id),
     use that gated, number-checked text; otherwise fall back to the same deterministic template the
     backend itself falls back to (explain.py), filled from the check's own fields. Never invented here. */
  function explanationFor(check, segments) {
    const matched = (segments || []).find(segment => (segment.refs || []).indexOf(check.id) >= 0);
    return matched ? matched.text : templateFor(check);
  }

  function checkRow(check, names, segments) {
    const name = (names && names[check.id]) || SHARED_CHECK_NAMES[check.id] || "Check";
    const subject = check.subject ? ' <span class="muted">· ' + esc(check.subject) + "</span>" : "";
    const limit = check.limit === null || check.limit === undefined ? ""
      : ' <span class="muted">/ limit ' + esc(formatValue(check.limit, check.unit)) + "</span>";
    const trace = derivedFromHtml(check);
    const explanation = '<span class="chk__explain t-small muted">' + esc(explanationFor(check, segments)) + "</span>";
    const row = '<span class="chk__id">' + esc(check.id) + "</span>"
      + '<span class="chk__body">' + esc(name) + subject + explanation + "</span>"
      + '<span class="chk__figs">' + esc(formatValue(check.value, check.unit)) + limit + "</span>"
      + '<span class="chk__st">' + chip(check.status) + "</span>";
    if (!trace) return '<li class="chk">' + row + "</li>";
    return '<li class="chk chk--traced"><details><summary>' + row + "</summary>" + trace + "</details></li>";
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

  function editButtonHtml(label, path) {
    if (App.source.mode !== "live") return "";
    return '<button class="btn btn--outline-dark" type="button" data-edit-path="' + esc(path) + '" '
      + 'aria-label="' + esc(label) + '">' + esc(label.replace(/^Edit /, "Edit")) + "</button>";
  }

  function segmentsHtml(section) {
    const segments = (section.narrative && section.narrative.segments) || [];
    if (!segments.length) return '<p class="t-small muted">No written explanation is attached to this section.</p>';
    return '<ul class="segs">' + segments.map(segment => '<li class="seg">'
      + '<span class="seg__kind seg__kind--' + esc(segment.kind || "unknown") + '">'
      + esc(SEGMENT_KIND_LABEL[segment.kind] || segment.kind) + "</span>"
      + '<div class="seg__body"><p class="t-body">' + esc(segment.text) + "</p>"
      + ((segment.refs || []).length
        ? '<p class="t-small seg__refs">' + esc((segment.refs || []).join(", ")) + "</p>"
        : "")
      + "</div></li>").join("") + "</ul>";
  }

  /* A disclosed list of plain-text assumption labels (ported from index.html's assumptionBadge, SPEC §12):
     every number shown by `stress`/`goals` is information-only and rests on team-labelled assumptions, so the
     reader can always open the list behind the figure instead of taking it as a verified fact. Shared by the
     Summary screen's stress/known-limitations cards and the objectives screen's goal probabilities. */
  function assumptionsHtml(items, label) {
    const list = (items || []).filter(Boolean);
    if (!list.length) return "";
    return '<details class="abadge"><summary>'
      + '<span class="seg__kind seg__kind--assumption">' + esc(SEGMENT_KIND_LABEL.assumption) + "</span>"
      + esc(label) + ' <span class="t-small">(' + list.length + ")</span></summary>"
      + "<ul>" + list.map(item => "<li>" + esc(item) + "</li>").join("") + "</ul></details>";
  }

  /* ---------- navigation ---------- */

  function defaultNavSpec() {
    return {
      tabs: NAV_TABS.map((label, index) => ({label, href: index === 1 ? undefined : "#", active: index === 1})),
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
    checkNames: SHARED_CHECK_NAMES,
    widthOf,
    meterHtml,
    holdsHtml,
    ruleCardHtml,
    humanizeValue,
    factsHtml,
    editButtonHtml,
    segmentsHtml,
    assumptionsHtml,
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
