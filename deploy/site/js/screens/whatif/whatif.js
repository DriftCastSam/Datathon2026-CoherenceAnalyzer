(function () {
  "use strict";

  App.navSpec.whatif = {
    chrome: "full",
    tabs: [
      {label: "Accounts", href: "#", active: false},
      {label: "Analysis", href: "#/summary", active: true},
      {label: "Markets", href: "#", active: false},
      {label: "Activities", href: "#", active: false},
      {label: "Documents", href: "#", active: false},
      {label: "Research", href: "#", active: false}
    ]
  };

  const view = App.whatifView;
  const {num} = App.format;
  const ASSETS_PARAM = "assets";
  const TICKER_SEPARATOR = ",";
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

  /* Choices persist while the screen re-renders; a persona change starts them over. */
  const choice = {persona: null, ticker: null, target: null, funding: null};

  /* Live what-if loading: guard against stale results and avoid refetching unchanged selections. */
  let loadSeq = 0;
  let loadedKey = null;
  let liveLoadSuccess = false;
  let loading = false;

  function requestedTickers() {
    const raw = App.routeParams().get(ASSETS_PARAM);
    const fromRoute = raw ? raw.split(TICKER_SEPARATOR).filter(Boolean) : [];
    return fromRoute.length ? fromRoute : (App.state.selection || []);
  }

  /* Returns {context} when there is something to show, else {empty}. */
  function resolve() {
    const universe = App.state.universe;
    const whatif = App.state.whatif;
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

  function loadLiveWhatif(personaId, tickers, section) {
    loadSeq += 1;
    const token = loadSeq;
    loading = true;
    liveLoadSuccess = false;
    App.source.whatifLive(personaId, tickers).then(data => {
      if (token !== loadSeq) return;
      App.state.universe = data.universe;
      App.state.whatif = data.whatif;
      liveLoadSuccess = true;
      loading = false;
      loadedKey = cacheKey(personaId, tickers, App.state.report);
      if (App.state.persona === personaId) App.renderers.whatif(section);
    }).catch(error => {
      if (token !== loadSeq) return;
      console.error("[whatif] live what-if failed, using static files", error);
      loading = false;
      liveLoadSuccess = false;
      Promise.all([App.source.universe(personaId), App.source.whatif(personaId)]).then(([universe, whatif]) => {
        if (token !== loadSeq) return;
        App.state.universe = universe;
        App.state.whatif = whatif;
        loadedKey = cacheKey(personaId, tickers, App.state.report);
        if (App.state.persona === personaId) App.renderers.whatif(section);
      }).catch(fallbackError => {
        if (token !== loadSeq) return;
        console.error("[whatif] fallback to static files also failed", fallbackError);
        loading = false;
      });
    });
  }

  function scenarioFloor() {
    const limits = App.state.report && App.state.report.site ? App.state.report.site.limits : null;
    return limits ? num(limits.floor_at_horizon, undefined) : undefined;
  }

  function profileDisplay() {
    const live = view.profileLine(App.state.report);
    const note = App.source.mode !== MODE_LIVE || !liveLoadSuccess;
    return {line: live, note};
  }

  function syncChoice(context) {
    const {base, tickers} = context;
    if (choice.persona !== App.state.persona) {
      Object.assign(choice, {persona: App.state.persona, ticker: null, target: null, funding: null});
    }
    const targets = (base.targets || []).map(Number).filter(Number.isFinite);
    const fundings = base.fundings || [];
    if (tickers.indexOf(choice.ticker) < 0) choice.ticker = tickers[0];
    if (targets.indexOf(Number(choice.target)) < 0) choice.target = targets[0];
    if (!fundings.some(item => String(item.id) === choice.funding)) {
      choice.funding = fundings.length ? String(fundings[0].id) : null;
    }
    return {targets, fundings};
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
    const funding = (base.fundings || []).find(item => String(item.id) === choice.funding);
    const derived = view.scenarioView({
      base, scenario, target: choice.target, fundingLabel: funding ? funding.label : choice.funding, years
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

  function paintContext(mount, context, section) {
    const {targets, fundings} = syncChoice(context);
    const {base, whatif, tickers} = context;
    show(mount, "empty", false);
    show(mount, "content", true);
    fillSelect(mount.querySelector("#whatif-asset"),
      tickers.map(ticker => ({value: ticker, label: view.assetLabel(whatif[ticker].asset)})), choice.ticker);
    fillSelect(mount.querySelector("#whatif-amount"),
      targets.map(target => ({value: target, label: view.amountLabel(target, base.value_cad)})), choice.target);
    fillSelect(mount.querySelector("#whatif-funding"),
      fundings.map(item => ({value: item.id, label: item.label})), choice.funding);
    text(mount, "meta", (tickers.indexOf(choice.ticker) + 1) + " of " + tickers.length
      + (tickers.length === 1 ? " selected asset" : " selected assets"));
    const profile = profileDisplay();
    text(mount, "profile", profile.line);
    text(mount, "profile-note", profile.note ? BASE_PROFILE_NOTE : "");
    show(mount, "profile-note", profile.note);
    text(mount, "as-of", view.asOfLine(base));

    const scenario = whatif[choice.ticker].scenarios[view.keyOf(choice.target, choice.funding)];
    const failure = !scenario ? MISSING_SCENARIO : scenario.error;
    text(mount, "error", failure ? String(failure) : "");
    show(mount, "error", Boolean(failure));
    show(mount, "results", !failure);
    if (!failure) paintScenario(mount, context, scenario);
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

  function renderWhatIf(section) {
    const mount = section.querySelector(".scr");
    if (!mount) return;

    const personaId = App.state.persona;
    const tickers = requestedTickers();
    if (App.source.mode === MODE_LIVE && tickers.length > 0) {
      const currentKey = cacheKey(personaId, tickers, App.state.report);
      if (currentKey !== loadedKey && !loading) {
        loadLiveWhatif(personaId, tickers, section);
      }
    }

    if (loading) {
      paintEmpty(mount, {title: "Loading scenarios…", text: "Computing what-if scenarios from the current profile."});
      return;
    }

    const resolved = resolve();
    if (resolved.empty) {
      paintEmpty(mount, resolved.empty);
    } else {
      paintContext(mount, resolved.context, section);
    }
    bindSelect(section, "#whatif-asset", value => { choice.ticker = value; });
    bindSelect(section, "#whatif-amount", value => { choice.target = Number(value); });
    bindSelect(section, "#whatif-funding", value => { choice.funding = value; });
  }

  App.renderers.whatif = renderWhatIf;
})();
