(function () {
  "use strict";

  App.navSpec.whatif = App.navTabs();

  const view = App.whatifView;
  const {num} = App.format;
  const ASSETS_PARAM = "assets";
  const TICKER_SEPARATOR = ",";
  const LOADING_TITLE = "Loading scenarios…";
  const LOADING_TEXT = "Computing what-if scenarios from the current profile.";
  const EMPTY_NONE = {
    title: "No assets selected",
    text: "Choose one or more assets in the search results to see how the portfolio changes with each of them."
  };
  const EMPTY_UNAVAILABLE = {
    title: "Scenarios unavailable",
    text: "No precomputed scenarios exist for the selected assets under this profile. Go back to search to change the selection."
  };
  const EMPTY_NO_DATA = {
    title: "What-if data unavailable",
    text: "The what-if data for this profile could not be loaded. Reload the page or choose another profile."
  };
  const MISSING_SCENARIO = "No precomputed scenario exists for this amount and funding source.";
  const CHART_UNAVAILABLE = "Chart unavailable";
  const MODE_LIVE = "live";
  const BASE_PROFILE_NOTE = "What-if scenarios are precomputed from the persona's original profile; "
    + "edits made in this session are not reflected here.";
  const LIVE_LOADING = "loading";
  const LIVE_OK = "ok";
  const LIVE_FAILED = "failed";

  const AMOUNT_COMPUTING = "Computing the scenario for this amount…";
  const AMOUNT_STATIC = "Custom amounts are computed by the local app (serve_local). Precomputed amounts for this asset: ";
  const AMOUNT_FAILED = "This amount could not be computed";
  const AMOUNT_DEBOUNCE_MS = 350;
  const MAX_CACHED_AMOUNTS = 50;
  const EDITED_CAD = "cad";
  const EDITED_PCT = "pct";

  /* Choices persist while the screen re-renders; a persona change starts them over.
     amountCad and pct mirror each other (both relative to the current portfolio value); lastEdited says which
     one the user typed last, and that one is authoritative. */
  const choice = {persona: null, ticker: null, amountCad: null, pct: null, lastEdited: EDITED_CAD, funding: null};

  /* Live scenarios for typed amounts: key -> {status, scenario?, message?}. */
  const amountLive = new Map();
  let amountTimer = null;

  /* Live what-if loading: screen-local cache keyed by requestKey -> {status, data?}.
     Bound the cache by clearing entries for other personas. */
  const MAX_CACHED_ENTRIES = 10;
  const live = new Map();

  function requestedTickers() {
    const raw = App.routeParams().get(ASSETS_PARAM);
    const fromRoute = raw ? raw.split(TICKER_SEPARATOR).filter(Boolean) : [];
    return fromRoute.length ? fromRoute : (App.state.selection || []);
  }

  /* Returns {context} when there is something to show, else {empty}.
     Accepts optional liveData = {universe, whatif} from the live cache. */
  function resolve(liveData) {
    const universe = liveData ? liveData.universe : App.state.universe;
    const whatif = liveData ? liveData.whatif : App.state.whatif;
    if (!universe || !universe.base || !whatif) return {empty: EMPTY_NO_DATA};
    const requested = requestedTickers();
    if (!requested.length) return {empty: EMPTY_NONE};
    const tickers = requested.filter((ticker, at) => whatif[ticker] && whatif[ticker].asset
      && requested.indexOf(ticker) === at);
    if (!tickers.length) return {empty: EMPTY_UNAVAILABLE};
    return {context: {base: universe.base, whatif, tickers}};
  }

  function cacheKey(personaId, tickers, report) {
    const reportId = report ? report.run_id || "" : "";
    return personaId + "|" + tickers.slice().sort().join(",") + "|" + reportId;
  }

  function boundCache(currentKey) {
    if (live.size <= MAX_CACHED_ENTRIES) return;
    const keys = Array.from(live.keys());
    for (const key of keys) {
      if (key !== currentKey) {
        live.delete(key);
        if (live.size <= MAX_CACHED_ENTRIES) break;
      }
    }
  }

  function settle(requestKey, entry, section) {
    live.set(requestKey, entry);
    const currentKey = cacheKey(App.state.persona, requestedTickers(), App.state.report);
    if (requestKey === currentKey && App.route === "whatif") {
      App.renderers.whatif(section);
    }
  }

  function loadLiveWhatif(requestKey, personaId, tickers, section) {
    live.set(requestKey, {status: LIVE_LOADING});
    App.source.whatifLive(personaId, tickers).then(data => {
      settle(requestKey, {status: LIVE_OK, data}, section);
    }).catch(error => {
      console.error("[whatif] live what-if failed", error);
      settle(requestKey, {status: LIVE_FAILED}, section);
    });
    boundCache(requestKey);
  }

  function scenarioFloor() {
    const limits = App.state.report && App.state.report.site ? App.state.report.site.limits : null;
    return limits ? num(limits.floor_at_horizon, undefined) : undefined;
  }

  function profileDisplay(liveDataPresent) {
    const line = view.profileLine(App.state.report);
    const note = App.source.mode !== MODE_LIVE || !liveDataPresent;
    return {line, note};
  }

  function syncChoice(context) {
    const {base, whatif, tickers} = context;
    if (choice.persona !== App.state.persona) {
      Object.assign(choice, {persona: App.state.persona, ticker: null, amountCad: null, pct: null,
        lastEdited: EDITED_CAD, funding: null});
    }
    const targets = (base.targets || []).map(Number).filter(Number.isFinite);
    const liveFundings = App.source.mode === MODE_LIVE ? (base.live_fundings || []) : [];
    const fundings = (base.fundings || []).concat(liveFundings);
    if (tickers.indexOf(choice.ticker) < 0) choice.ticker = tickers[0];
    const held = view.heldPct(whatif[choice.ticker].asset);
    if (choice.amountCad === null && choice.pct === null) {
      const first = targets.map(target => view.presetAmount(target, base.value_cad, held)).find(amount => amount > 0);
      choice.amountCad = first === undefined ? null : first;
      choice.lastEdited = EDITED_CAD;
    }
    if (choice.lastEdited === EDITED_PCT) choice.amountCad = view.amountFromPct(choice.pct, base.value_cad);
    else choice.pct = view.pctFromAmount(choice.amountCad, base.value_cad);
    if (!fundings.some(item => String(item.id) === choice.funding)) {
      choice.funding = fundings.length ? String(fundings[0].id) : null;
    }
    return {targets, fundings, held};
  }

  function text(mount, key, value) {
    const node = mount.querySelector('[data-wf="' + key + '"]');
    if (node) node.textContent = value;
  }

  function html(mount, key, value) {
    const node = mount.querySelector('[data-wf="' + key + '"]');
    if (node) node.innerHTML = value;
  }

  function show(mount, key, isVisible) {
    const node = mount.querySelector('[data-wf="' + key + '"]');
    if (node) node.hidden = !isVisible;
  }

  function fillSelect(select, options, selected) {
    if (!select) return;
    select.innerHTML = options.map(option =>
      '<option value="' + App.esc(String(option.value)) + '">' + App.esc(option.label) + "</option>").join("");
    if (selected !== null && selected !== undefined) select.value = String(selected);
  }

  function rowsMarkup(rows) {
    return rows.map(row =>
      '<tr><td>' + App.esc(row.label) + '</td><td class="r">' + App.esc(view.minus(row.today)) + '</td>'
      + '<td class="r' + (row.worse ? " wf-bad" : "") + '"><strong>' + App.esc(view.minus(row.withAsset)) + "</strong></td></tr>"
    ).join("");
  }

  function pillarRowsMarkup(pillars) {
    return pillars.map(pillar =>
      '<tr><td class="wf-pillar">' + App.esc(pillar.label) + "</td>"
      + "<td>" + App.chip(pillar.today) + "</td>"
      + "<td>" + App.chip(pillar.withAsset) + "</td>"
      + "<td>" + App.esc(pillar.finding) + "</td></tr>"
    ).join("");
  }

  function drawChart(mount, base, scenario, years) {
    const host = mount.querySelector('[data-chart="whatif"]');
    if (!host) return;
    const floor = scenarioFloor();
    const drawn = App.charts && App.charts.fan
      && App.charts.fan(host, {cone: scenario.cone, compareMedian: (base.cone || {}).p50, floor, years, asOf: base.as_of});
    if (!drawn) host.innerHTML = '<p class="t-small muted">' + CHART_UNAVAILABLE + "</p>";
  }

  function paintScenario(mount, context, scenario) {
    const {base} = context;
    const years = view.yearsOf(base.cone);
    const funding = (base.fundings || []).concat(base.live_fundings || [])
      .find(item => String(item.id) === choice.funding);
    const derived = view.scenarioView({
      base, scenario, amountCad: choice.amountCad, funding: choice.funding,
      fundingLabel: funding ? funding.label : choice.funding, years
    });
    text(mount, "finding", derived.finding);
    html(mount, "verdict-chip", App.chip(derived.verdict));
    html(mount, "today-verdict", App.chip(derived.todayVerdict));
    html(mount, "with-verdict", App.chip(derived.verdict));
    derived.tiles.forEach(tile => {
      const value = mount.querySelector('[data-wf="tile-' + tile.id + '"]');
      if (value) {
        value.textContent = tile.value;
        value.className = "wf-tile__value wf-tile__value--" + tile.tone;
      }
      text(mount, "tile-" + tile.id + "-delta", tile.delta);
    });
    html(mount, "ba-rows", rowsMarkup(derived.rows));
    html(mount, "coh-rows", pillarRowsMarkup(derived.pillars));
    drawChart(mount, base, scenario, years);
  }

  function paintEmpty(mount, empty) {
    text(mount, "empty-title", empty.title);
    text(mount, "empty-text", empty.text);
    show(mount, "empty", true);
    show(mount, "content", false);
  }

  /* Write a number into an input unless the user is typing in it (that would move the caret). */
  function setInput(input, value) {
    if (!input || input === document.activeElement) return;
    input.value = value === null || value === undefined ? "" : String(value);
  }

  function amountKey(ticker) {
    const reportId = App.state.report ? App.state.report.run_id || "" : "";
    return [App.state.persona, ticker, choice.amountCad, choice.funding, reportId].join("|");
  }

  function loadAmount(key, ticker, section) {
    amountLive.set(key, {status: LIVE_LOADING});
    if (amountLive.size > MAX_CACHED_AMOUNTS) amountLive.delete(amountLive.keys().next().value);
    App.source.whatifAmount(App.state.persona, ticker, choice.amountCad, choice.funding).then(data => {
      amountLive.set(key, {status: LIVE_OK, scenario: data.scenario});
    }).catch(error => {
      const detail = String(error && error.message || "").replace(/^HTTP \d+ for \S+: ?/, "");
      amountLive.set(key, {status: LIVE_FAILED, message: detail ? AMOUNT_FAILED + ": " + detail : AMOUNT_FAILED + "."});
    }).then(() => {
      if (App.route === "whatif") App.renderers.whatif(section);
    });
  }

  /* {scenario} | {pending: text} | {failure: text}: the precomputed scenario when the amount matches one,
     else the live one (local app), else why there is none. */
  function amountScenario(context, held, targets, section) {
    const {base, whatif} = context;
    const problem = view.amountProblem(choice.amountCad,
      view.capacityCad(choice.funding, held, base.value_cad, base.weights_pct));
    if (problem) return {failure: problem};
    const preset = view.presetFor(choice.amountCad, held, base.value_cad, targets, choice.funding);
    const scenarios = whatif[choice.ticker].scenarios || {};
    if (preset !== null && scenarios[view.keyOf(preset, choice.funding)]) {
      return {scenario: scenarios[view.keyOf(preset, choice.funding)]};
    }
    if (App.source.mode !== MODE_LIVE) {
      const presets = targets.map(target => view.presetAmount(target, base.value_cad, held)).filter(amount => amount > 0);
      return {failure: AMOUNT_STATIC + (presets.map(amount => "$" + App.fmtCad(amount, 2)).join(", ") || "none") + "."};
    }
    const key = amountKey(choice.ticker);
    const entry = amountLive.get(key);
    if (!entry) loadAmount(key, choice.ticker, section);
    if (!entry || entry.status === LIVE_LOADING) return {pending: AMOUNT_COMPUTING};
    if (entry.status === LIVE_FAILED) return {failure: entry.message};
    return {scenario: entry.scenario};
  }

  function paintContext(mount, context, section, liveDataPresent) {
    const {targets, fundings, held} = syncChoice(context);
    const {base, whatif, tickers} = context;
    show(mount, "empty", false);
    show(mount, "content", true);
    fillSelect(mount.querySelector("#whatif-asset"),
      tickers.map(ticker => ({value: ticker, label: view.assetLabel(whatif[ticker].asset)})), choice.ticker);
    setInput(mount.querySelector("#whatif-amount-cad"), choice.amountCad);
    setInput(mount.querySelector("#whatif-amount-pct"), choice.pct);
    const presets = mount.querySelector("#whatif-amount-presets");
    if (presets) {
      presets.innerHTML = targets.map(target => ({amount: view.presetAmount(target, base.value_cad, held), target}))
        .filter(item => item.amount > 0)
        .map(item => '<option value="' + App.esc(String(item.amount)) + '">'
          + App.esc(view.amountLabel(item.target, base.value_cad, held)) + "</option>").join("");
    }
    fillSelect(mount.querySelector("#whatif-funding"),
      fundings.map(item => ({value: item.id, label: item.label})), choice.funding);
    text(mount, "meta", (tickers.indexOf(choice.ticker) + 1) + " of " + tickers.length
      + (tickers.length === 1 ? " selected asset" : " selected assets"));
    const profile = profileDisplay(liveDataPresent);
    text(mount, "profile", profile.line);
    text(mount, "profile-note", profile.note ? BASE_PROFILE_NOTE : "");
    show(mount, "profile-note", profile.note);
    text(mount, "as-of", view.asOfLine(base));

    const resolved = amountScenario(context, held, targets, section);
    const scenario = resolved.scenario;
    text(mount, "amount-note", resolved.pending || "");
    const failure = resolved.pending ? "" : resolved.failure || (!scenario ? MISSING_SCENARIO : scenario.error);
    text(mount, "error", failure ? String(failure) : "");
    show(mount, "error", Boolean(failure));
    show(mount, "results", Boolean(scenario) && !failure);
    if (scenario && !failure) paintScenario(mount, context, scenario);
  }

  /* The selects live in the screen's static markup and survive re-renders, so each is bound
     once; the handler stores the choice and re-renders the screen. */
  function bindSelect(section, selector, apply) {
    const select = section.querySelector(selector);
    if (!select || select.dataset.wiBound === "1") return;
    select.dataset.wiBound = "1";
    select.addEventListener("change", event => {
      apply(event.target.value);
      App.renderers.whatif(section);
      const refreshed = section.querySelector(selector);
      if (refreshed) refreshed.focus();
    });
  }

  /* Typing in either amount field updates the other at once, then re-renders after a short pause. */
  function bindAmount(section, selector, edited) {
    const input = section.querySelector(selector);
    if (!input || input.dataset.wiBound === "1") return;
    input.dataset.wiBound = "1";
    input.addEventListener("input", () => {
      const value = input.value === "" ? null : Number(input.value);
      const base = (resolve().context || {}).base || {};
      const liveEntry = live.get(cacheKey(App.state.persona, requestedTickers(), App.state.report));
      const portfolioValue = liveEntry && liveEntry.data ? liveEntry.data.universe.base.value_cad : base.value_cad;
      choice.lastEdited = edited;
      if (edited === EDITED_CAD) {
        choice.amountCad = value;
        choice.pct = view.pctFromAmount(value, portfolioValue);
        setInput(section.querySelector("#whatif-amount-pct"), choice.pct);
      } else {
        choice.pct = value;
        choice.amountCad = view.amountFromPct(value, portfolioValue);
        setInput(section.querySelector("#whatif-amount-cad"), choice.amountCad);
      }
      clearTimeout(amountTimer);
      amountTimer = setTimeout(() => App.renderers.whatif(section), AMOUNT_DEBOUNCE_MS);
    });
  }

  function bindSelects(section) {
    bindSelect(section, "#whatif-asset", value => { choice.ticker = value; });
    bindSelect(section, "#whatif-funding", value => { choice.funding = value; });
    bindAmount(section, "#whatif-amount-cad", EDITED_CAD);
    bindAmount(section, "#whatif-amount-pct", EDITED_PCT);
  }

  function paint(mount, resolved, section, noteHidden) {
    if (resolved.empty) {
      paintEmpty(mount, resolved.empty);
    } else {
      paintContext(mount, resolved.context, section, noteHidden);
    }
  }

  function renderWhatIf(section) {
    const mount = section.querySelector(".scr");
    if (!mount) return;

    const personaId = App.state.persona;
    const tickers = requestedTickers();
    const currentKey = cacheKey(personaId, tickers, App.state.report);

    /* In live mode, check the cache and start a load if needed. */
    if (App.source.mode === MODE_LIVE && tickers.length > 0) {
      const entry = live.get(currentKey);
      if (!entry) {
        loadLiveWhatif(currentKey, personaId, tickers, section);
        paintEmpty(mount, {title: LOADING_TITLE, text: LOADING_TEXT});
        bindSelects(section);
        return;
      }
      if (entry.status === LIVE_LOADING) {
        paintEmpty(mount, {title: LOADING_TITLE, text: LOADING_TEXT});
        bindSelects(section);
        return;
      }
      if (entry.status === LIVE_OK) {
        paint(mount, resolve(entry.data), section, true);
        bindSelects(section);
        return;
      }
      /* entry.status === LIVE_FAILED: fall through to render from App.state with note shown. */
    }

    /* Static mode or failed live load: render from App.state. */
    paint(mount, resolve(), section, false);
    bindSelects(section);
  }

  App.renderers.whatif = renderWhatIf;
})();
