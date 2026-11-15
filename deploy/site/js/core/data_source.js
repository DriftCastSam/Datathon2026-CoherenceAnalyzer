(() => {
  "use strict";

  /* Data source chosen once at boot: "live" when the local API answers `api/health`, else
     "static" (prebuilt JSON under reports/). All URLs are relative so one origin serves both. */
  const API_DIR = "api/";
  const SITE_API_DIR = "site-api/";
  const REPORT_DIR = "reports/";
  const HEALTH_TIMEOUT_MS = 1500;
  const REQUEST_TIMEOUT_MS = 120000;
  const INTAKE_LOCALE = "en-CA";
  const LEGACY_UNIVERSE = "universe.json";
  const LEGACY_WHATIF = "whatif.json";
  const MODE_LIVE = "live";
  const MODE_STATIC = "static";
  const SKIPPED_BY = "user:skipped";
  const DEFAULT_PERSONA_ID = "persona-robert";
  const STATIC_CARD_KEYS = {
    "risk.declared": "risk_declared",
    "objectives.horizon_years": "horizon_years"
  };

  /* Required intake paths and en-CA question texts, mirrored from coherence_analyzer/intake.py
     (REQUIRED, QUESTIONS). */
  const INTAKE_PATHS = [
    {factor: "objectif", path: "objectives.primary", text: "What is your main goal for this portfolio (for example preserving capital, earning income or growing capital)?"},
    {factor: "objectif", path: "objectives.goals", text: "What are your dated goals (name, date, amount in CAD), for example retirement or a down payment?"},
    {factor: "horizon", path: "objectives.horizon_years", text: "In how many years do you expect to need this money?"},
    {factor: "horizon", path: "identity.retirement_target_age", text: "At what age do you plan to retire?"},
    {factor: "horizon", path: "situation.planned_withdrawals", text: "Do you plan any large withdrawals (date, amount, purpose)? Answer with an empty list if there are none."},
    {factor: "risque", path: "risk.declared", text: "How would you describe your risk tolerance: low, low-moderate, moderate, moderate-high or high?"},
    {factor: "risque", path: "risk.max_acceptable_drawdown_pct", text: "What is the largest drop in your portfolio you could accept, as a percentage (for example -12)?"},
    {factor: "risque", path: "risk.reaction_to_20pct_drop", text: "If your portfolio lost 20%, what would you do: sell everything, sell some, do nothing or buy more?"},
    {factor: "contraintes", path: "situation.emergency_fund", text: "How much do you hold as an emergency fund, in CAD?"},
    {factor: "contraintes", path: "situation.monthly_expenses", text: "What are your monthly expenses, in CAD?"},
    {factor: "contraintes", path: "identity.employer_sector", text: "Which sector does your employer operate in?"},
    {factor: "contraintes", path: "preferences.asset_types_refused", text: "Which product types do you refuse to hold (for example crypto, options, margin)? Answer with an empty list if none."},
    {factor: "preferences", path: "preferences.prefers_dividends", text: "Do you prefer dividend-paying holdings (yes or no)?"},
    {factor: "preferences", path: "preferences.prefers_large_caps", text: "Do you prefer large-cap companies (yes or no)?"},
    {factor: "preferences", path: "preferences.home_bias_ok", text: "Are you comfortable with an overweight in Canadian holdings (yes or no)?"}
  ];

  let mode = MODE_STATIC;
  let staticIndex = null;
  let livePersonas = [];
  const reportCache = new Map();
  const state = {fallback: false};

  /* ---------- transport ---------- */

  async function request(url, options, timeoutMs) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs || REQUEST_TIMEOUT_MS);
    try {
      const response = await fetch(url, {cache: "no-store", ...options, signal: controller.signal});
      if (!response.ok) {
        let detail = "";
        try {
          const body = await response.json();
          detail = body && body.detail ? ": " + (typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail)) : "";
        } catch (parseError) {
          detail = "";
        }
        throw new Error("HTTP " + response.status + " for " + url + detail);
      }
      return await response.json();
    } finally {
      clearTimeout(timer);
    }
  }

  function post(url, body) {
    return request(url, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: body === undefined ? undefined : JSON.stringify(body)
    });
  }

  async function optionalJson(url) {
    try {
      return await request(url);
    } catch (error) {
      console.warn("[data] unavailable: " + url, error);
      return null;
    }
  }

  /* ---------- static index ---------- */

  async function loadStaticIndex() {
    if (staticIndex === null) {
      const index = await optionalJson(REPORT_DIR + "index.json");
      staticIndex = Array.isArray(index) ? index.filter(entry => entry && entry.file) : [];
    }
    return staticIndex;
  }

  function staticEntries(personaId) {
    return (staticIndex || []).filter(entry => (entry.persona_id || "") === personaId);
  }

  function groupStatic(index) {
    const groups = new Map();
    for (const entry of index) {
      const id = entry.persona_id || entry.file;
      if (!groups.has(id)) groups.set(id, {id, name: entry.name || id, entries: []});
      groups.get(id).entries.push({file: entry.file, label: entry.label || entry.file});
    }
    return Array.from(groups.values());
  }

  /* ---------- reports ---------- */

  async function staticReport(personaId, file) {
    await loadStaticIndex();
    const target = file || (staticEntries(personaId)[0] || (staticIndex || [])[0] || {}).file;
    if (!target) throw new Error("No prebuilt report is listed for " + personaId + " in " + REPORT_DIR + "index.json");
    return request(REPORT_DIR + target);
  }

  async function runLiveReview(personaId) {
    const report = await post(SITE_API_DIR + "profiles/" + encodeURIComponent(personaId) + "/review");
    reportCache.set(personaId, report);
    return report;
  }

  /* A live persona whose review fails still shows its prebuilt report, and the stamp says so. */
  async function liveOrFallback(personaId, file, produce) {
    state.fallback = false;
    try {
      return await produce();
    } catch (error) {
      console.error("[data] live analysis failed for " + personaId, error);
      await loadStaticIndex();
      if (!staticEntries(personaId).length) throw error;
      state.fallback = true;
      return staticReport(personaId, file);
    }
  }

  async function report(personaId, file) {
    if (mode === MODE_STATIC) return staticReport(personaId, file);
    if (reportCache.has(personaId)) {
      state.fallback = false;
      return reportCache.get(personaId);
    }
    return liveOrFallback(personaId, file, () => runLiveReview(personaId));
  }

  async function rerun(personaId, file) {
    if (mode === MODE_STATIC) return staticReport(personaId, file);
    return liveOrFallback(personaId, file, () => runLiveReview(personaId));
  }

  /* ---------- universe and what-if ---------- */

  async function personaFile(prefix, personaId, legacy) {
    const specific = await optionalJson(REPORT_DIR + prefix + "-" + personaId + ".json");
    return specific || optionalJson(REPORT_DIR + legacy);
  }

  function universe(personaId) {
    return personaFile("universe", personaId, LEGACY_UNIVERSE);
  }

  function whatif(personaId) {
    return personaFile("whatif", personaId, LEGACY_WHATIF);
  }

  /* ---------- personas ---------- */

  async function personas() {
    if (mode === MODE_STATIC) return groupStatic(await loadStaticIndex());
    return livePersonas;
  }

  /* ---------- intake ---------- */

  function lookup(root, path) {
    let node = root;
    for (const part of path.split(".")) {
      if (node === null || typeof node !== "object") return null;
      node = node[part];
    }
    if (node && typeof node === "object" && !Array.isArray(node) && "value" in node) {
      return node.by === SKIPPED_BY ? null : node.value;
    }
    return node === undefined ? null : node;
  }

  function withCurrent(read) {
    return {questions: INTAKE_PATHS.map(item => ({...item, current: read(item.path)}))};
  }

  async function intake(personaId, currentReport) {
    if (mode === MODE_LIVE) {
      const profile = await request(API_DIR + "profiles/" + encodeURIComponent(personaId));
      return withCurrent(path => lookup(profile, path));
    }
    const card = currentReport && currentReport.site ? currentReport.site.profile_card : null;
    return withCurrent(path => {
      const key = STATIC_CARD_KEYS[path];
      return card && key && card[key] !== undefined ? card[key] : null;
    });
  }

  function requireLive(action) {
    if (mode !== MODE_LIVE) {
      return Promise.reject(new Error("Cannot " + action + " in prebuilt mode: answers are only analysed when the local server is running."));
    }
    return null;
  }

  function answer(personaId, path, value) {
    return requireLive("save an answer")
      || post(API_DIR + "profiles/" + encodeURIComponent(personaId) + "/intake/answer", {path, value});
  }

  function skip(personaId, path) {
    return requireLive("skip a question")
      || post(API_DIR + "profiles/" + encodeURIComponent(personaId) + "/intake/skip", {path});
  }

  /* ---------- boot ---------- */

  /* The first persona is the default one; move the preferred persona there. */
  function defaultFirst(list) {
    const preferred = list.find(item => item.id === DEFAULT_PERSONA_ID);
    return preferred ? [preferred, ...list.filter(item => item !== preferred)] : list;
  }

  async function init() {
    try {
      await request(API_DIR + "health", undefined, HEALTH_TIMEOUT_MS);
      const profiles = await request(API_DIR + "profiles");
      livePersonas = defaultFirst(profiles.map(item => ({id: item.profile_id, name: item.name, entries: []})));
      mode = livePersonas.length ? MODE_LIVE : MODE_STATIC;
    } catch (error) {
      console.info("[data] live API unavailable, using prebuilt reports", error);
      mode = MODE_STATIC;
    }
    await loadStaticIndex();
    App.source.mode = mode;
    return mode;
  }

  App.source = {
    mode,
    init,
    personas,
    report,
    rerun,
    /* The prebuilt report for a persona, whatever the mode (the what-if base profile). */
    prebuiltReport: staticReport,
    intake,
    answer,
    skip,
    universe,
    whatif,
    intakePaths: INTAKE_PATHS,
    /* True when the last live call fell back to a prebuilt report. */
    usedFallback: () => state.fallback,
    staticIndex: () => staticIndex
  };
})();
