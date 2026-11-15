(() => {
  "use strict";

  /* Data source chosen once at boot: "live" when the local API answers `api/health`, else
     "static" (prebuilt JSON under reports/). All URLs are relative so one origin serves both. */
  const API_DIR = "api/";
  const SITE_API_DIR = "site-api/";
  const REPORT_DIR = "reports/en-CA/";
  const HEALTH_TIMEOUT_MS = 1500;
  const REQUEST_TIMEOUT_MS = 120000;
  const INTAKE_LOCALE = "en-CA";
  const INTAKE_MANIFEST = "intake.json";
  const SOURCE_LIVE = "live";
  const SOURCE_FALLBACK = "fallback";
  const SOURCE_PREBUILT = "prebuilt";
  const MODE_LIVE = "live";
  const MODE_STATIC = "static";
  const STATIC_CARD_KEYS = {
    "risk.declared": "risk_declared",
    "objectives.horizon_years": "horizon_years"
  };

  let mode = MODE_STATIC;
  let staticIndex = null;
  let livePersonas = [];
  const reportCache = new Map();
  let intakeManifest = null;

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

  /* Throws for a persona the prebuilt index does not list, instead of serving another one's files. */
  async function requireKnownPersona(personaId) {
    await loadStaticIndex();
    if (!staticEntries(personaId).length) {
      throw new Error("Unknown persona \"" + personaId + "\": it is not listed in " + REPORT_DIR + "index.json");
    }
  }

  /* Records where a report came from on the object itself (not serialised), so overlapping
     calls for different personas cannot overwrite each other's stamp. */
  function stamp(report, source) {
    Object.defineProperty(report, "_source", {value: source, enumerable: false, configurable: true});
    return report;
  }

  function isFallback(report) {
    return Boolean(report) && report._source === SOURCE_FALLBACK;
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
    await requireKnownPersona(personaId);
    const target = file || staticEntries(personaId)[0].file;
    return stamp(await request(REPORT_DIR + target), SOURCE_PREBUILT);
  }

  async function runLiveReview(personaId) {
    const report = await post(SITE_API_DIR + "profiles/" + encodeURIComponent(personaId) + "/review");
    reportCache.set(personaId, stamp(report, SOURCE_LIVE));
    return report;
  }

  /* A live persona whose review fails still shows its prebuilt report, and the stamp says so. */
  async function liveOrFallback(personaId, file, produce) {
    try {
      return await produce();
    } catch (error) {
      console.error("[data] live analysis failed for " + personaId, error);
      await loadStaticIndex();
      if (!staticEntries(personaId).length) throw error;
      return stamp(await staticReport(personaId, file), SOURCE_FALLBACK);
    }
  }

  async function report(personaId, file) {
    if (mode === MODE_STATIC) return staticReport(personaId, file);
    if (reportCache.has(personaId)) return reportCache.get(personaId);
    return liveOrFallback(personaId, file, () => runLiveReview(personaId));
  }

  async function rerun(personaId, file) {
    if (mode === MODE_STATIC) return staticReport(personaId, file);
    return liveOrFallback(personaId, file, () => runLiveReview(personaId));
  }

  /* ---------- universe and what-if ---------- */

  async function personaFile(prefix, personaId) {
    await requireKnownPersona(personaId);
    return optionalJson(REPORT_DIR + prefix + "-" + personaId + ".json");
  }

  function universe(personaId) {
    return personaFile("universe", personaId);
  }

  function whatif(personaId) {
    return personaFile("whatif", personaId);
  }

  function whatifLive(personaId, tickers) {
    return requireLive("compute live what-if scenarios")
      || post(SITE_API_DIR + "profiles/" + encodeURIComponent(personaId) + "/whatif", {tickers});
  }

  /* ---------- personas ---------- */

  async function personas() {
    if (mode === MODE_STATIC) return groupStatic(await loadStaticIndex());
    return livePersonas;
  }

  /* ---------- intake ---------- */

  /* The required questions (paths and en-CA texts) come from the build, not a copy kept here. */
  async function loadIntakeManifest() {
    if (intakeManifest === null) {
      let manifest;
      try {
        manifest = await request(REPORT_DIR + INTAKE_MANIFEST);
      } catch (error) {
        throw new Error("The question list (" + REPORT_DIR + INTAKE_MANIFEST + ") could not be loaded: " + error.message);
      }
      if (!manifest || !Array.isArray(manifest.questions) || typeof manifest.skipped_by !== "string") {
        throw new Error("The question list (" + REPORT_DIR + INTAKE_MANIFEST + ") has an unexpected shape.");
      }
      intakeManifest = manifest;
    }
    return intakeManifest;
  }

  /* {current, skipped} for one path of a profile; a skipped answer reads back as skipped. */
  function lookup(root, path, skippedBy) {
    let node = root;
    for (const part of path.split(".")) {
      if (node === null || typeof node !== "object") return {current: null, skipped: false};
      node = node[part];
    }
    if (node && typeof node === "object" && !Array.isArray(node) && "value" in node) {
      return node.by === skippedBy ? {current: null, skipped: true} : {current: node.value, skipped: false};
    }
    return {current: node === undefined ? null : node, skipped: false};
  }

  async function intake(personaId, currentReport) {
    const manifest = await loadIntakeManifest();
    let read;
    if (mode === MODE_LIVE) {
      const profile = await request(API_DIR + "profiles/" + encodeURIComponent(personaId));
      read = path => lookup(profile, path, manifest.skipped_by);
    } else {
      const card = currentReport && currentReport.site ? currentReport.site.profile_card : null;
      read = path => {
        const key = STATIC_CARD_KEYS[path];
        return {current: card && key && card[key] !== undefined ? card[key] : null, skipped: false};
      };
    }
    return {questions: manifest.questions.map(item => ({...item, ...read(item.path)}))};
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

  /* The first persona listed in the prebuilt index is the default one; move it first. */
  function defaultFirst(list) {
    const defaultId = (staticIndex || []).map(entry => entry.persona_id).find(Boolean);
    const preferred = list.find(item => item.id === defaultId);
    return preferred ? [preferred, ...list.filter(item => item !== preferred)] : list;
  }

  async function init() {
    await loadStaticIndex();
    try {
      await request(API_DIR + "health", undefined, HEALTH_TIMEOUT_MS);
      const profiles = await request(API_DIR + "profiles");
      livePersonas = defaultFirst(profiles.map(item => ({id: item.profile_id, name: item.name, entries: []})));
      mode = livePersonas.length ? MODE_LIVE : MODE_STATIC;
    } catch (error) {
      console.info("[data] live API unavailable, using prebuilt reports", error);
      mode = MODE_STATIC;
    }
    App.source.mode = mode;
    return mode;
  }

  App.source = {
    mode,
    init,
    personas,
    report,
    rerun,
    intake,
    answer,
    skip,
    universe,
    whatif,
    whatifLive,
    /* True when this report is the prebuilt stand-in for a failed live analysis. */
    isFallback,
    staticIndex: () => staticIndex
  };
})();
