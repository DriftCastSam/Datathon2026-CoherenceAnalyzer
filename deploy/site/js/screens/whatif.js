(function () {
  "use strict";

  App.navSpec.whatif = {
    chrome: "full",
    tabs: [
      {label: "Accounts", href: "#/summary", active: true},
      {label: "Markets", href: "#", active: false},
      {label: "Activities", href: "#", active: false},
      {label: "Documents", href: "#", active: false},
      {label: "Research", href: "#", active: false}
    ]
  };

  const state = {ticker: null, target: null, funding: null};
  const VERDICT_STATUS = {coherent: "pass", coherent_with_warnings: "warning", incoherent: "violation"};
  /* What-if ranks the worst status first (0 = worst), the inverse of App.statusRank. */
  const WORST_FIRST_RANK = {violation: 0, warning: 1, unknown: 2, info: 3, pass: 4};
  const PILLARS = [
    {id: "objectives", label: "Objectives and horizon", factors: ["objectif", "horizon"]},
    {id: "risk", label: "Risk", factors: ["risque"]},
    {id: "constraints", label: "Constraints", factors: ["contraintes"]},
    {id: "preferences", label: "Preferences", factors: ["preferences"]}
  ];
  const SAMPLE_PILLAR_FINDING = {
    objectives: "Median and optimistic scenarios still preserve the capital. The pessimistic scenario ends 13,000 lower than today.",
    risk: "Volatility rises from 16.8% to 19.1%. Bad-scenario loss becomes 2.9\u00d7 the tolerance, up from 2.4\u00d7.",
    constraints: "At 10% the new position respects the single-stock limit. The existing 22% position still fails it.",
    preferences: "The company is established, so no new warning. Bonds and Canadian equities fall from 60% to 50%."
  };
  const SAMPLE_FINDING = "Adding this stock raises the median projection slightly, but it moves the portfolio further from Robert's low-to-moderate risk level. The estimated bad-scenario loss grows from 60,000 to 72,500, against a tolerance of 25,000. The position itself stays within the 10% single-stock limit. This is an analysis of the asset you chose, not a suggestion to buy or avoid it.";
  /* A scenario that exists but lacks some metrics gets this neutral line; the sample sentences
     above are only used when no scenario data exists at all (sampleView). */
  const NEUTRAL_FINDING = "Precomputed finding unavailable for this scenario.";
  const SAMPLE = {
    assets: [{value: "SAMPLE", label: "US semiconductor company A [TICKER]"}],
    ticker: "SAMPLE",
    meta: "1 of 2 selected assets",
    targets: [{value: 10, label: "25,000.00 \u00b7 10% of portfolio"}],
    target: 10,
    fundings: [{id: "sample:bonds", label: "Aggregate bond ETF (35% to 25%)"}],
    funding: "sample:bonds",
    profile: "Robert \u00b7 low to moderate risk \u00b7 3 to 5 years",
    asOf: "5 years, CAD, as of 2026-11-12",
    verdict: "violation",
    todayVerdict: "violation",
    withVerdict: "violation",
    finding: SAMPLE_FINDING,
    chart: {optimistic: "Optimistic 392k", median: "Median 309k", pessimistic: "Pessimistic 201k"},
    tiles: [
      {id: "p5", label: "Pessimistic (P5)", value: "$201,000", tone: "bad", delta: "\u2212$13,000 vs current portfolio"},
      {id: "p50", label: "Median", value: "$309,000", tone: "mid", delta: "+$11,000 vs current portfolio"},
      {id: "p95", label: "Optimistic (P95)", value: "$392,000", tone: "good", delta: "+$21,000 vs current portfolio"}
    ],
    rows: [
      {label: "Median value, year 5", today: "298,000", withAsset: "309,000"},
      {label: "Pessimistic value, year 5", today: "214,000", withAsset: "201,000", worse: true},
      {label: "Annual volatility", today: "16.8%", withAsset: "19.1%", worse: true},
      {label: "Max drawdown", today: "\u221224%", withAsset: "\u221229%", worse: true},
      {label: "Bad-scenario loss", today: "60,000", withAsset: "72,500", worse: true},
      {label: "Share in single stocks", today: "22%", withAsset: "32%"},
      {label: "Share in bonds", today: "35%", withAsset: "25%"}
    ],
    pillars: PILLARS.map(pillar => ({
      label: pillar.label,
      today: {objectives: "pass", risk: "violation", constraints: "violation", preferences: "warning"}[pillar.id],
      withAsset: {objectives: "pass", risk: "violation", constraints: "violation", preferences: "warning"}[pillar.id],
      finding: SAMPLE_PILLAR_FINDING[pillar.id]
    }))
  };

  function hasNumber(value) {
    return value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value));
  }

  function money(value, digits) {
    return hasNumber(value) ? minus("$" + App.fmtCad(value, digits || 0)) : "\u2014";
  }

  function minus(value) {
    return String(value).replace(/^-/, "\u2212");
  }

  function kilo(value) {
    if (!hasNumber(value)) return "\u2014";
    const number = Number(value);
    if (Math.abs(number) >= 1000000) return (number / 1000000).toFixed(1) + "M";
    return Math.round(number / 1000) + "k";
  }

  function deltaText(before, after) {
    if (!hasNumber(before) || !hasNumber(after)) return "\u2014";
    const diff = Number(after) - Number(before);
    if (Math.round(Math.abs(diff)) === 0) return "no change vs current portfolio";
    return (diff < 0 ? "\u2212" : "+") + "$" + App.fmtCad(Math.abs(diff), 0) + " vs current portfolio";
  }

  function verdictStatus(verdict) {
    return VERDICT_STATUS[verdict] || "unknown";
  }

  function keyOf(target, funding) {
    return Math.trunc(Number(target)) + "|" + String(funding);
  }

  function amountLabel(target, value) {
    if (!hasNumber(value)) return App.fmtPct(target, 0) + " of portfolio";
    return App.fmtCad(Number(value) * Number(target) / 100, 2) + " \u00b7 " + App.fmtPct(target, 0) + " of portfolio";
  }

  function assetLabel(asset) {
    const name = asset && asset.name ? String(asset.name) : "";
    const ticker = asset && asset.ticker ? String(asset.ticker) : "";
    if (name && ticker) return name + " [" + ticker + "]";
    return name || ticker;
  }

  function fundingLabel(funding, assets) {
    const id = funding && funding.id !== undefined && funding.id !== null ? String(funding.id) : "";
    if (id === "pro_rata_all") return "Pro rata across all holdings";
    const specific = /^specific:(.+)$/.exec(id);
    if (specific) {
      const ticker = specific[1];
      const asset = (assets || []).find(item => item && item.ticker === ticker);
      return (asset && asset.name ? String(asset.name) : ticker) + " (" + ticker + ")";
    }
    return funding && funding.label ? String(funding.label) : id;
  }

  /* Statuses are shown in prose, so the shared table is lowercased rather than copied. */
  function riskWord(key) {
    return App.riskLevels[key] ? App.riskLevels[key].toLowerCase() : "";
  }

  function profileLine(report) {
    const card = report && report.site ? report.site.profile_card : null;
    if (!card) return SAMPLE.profile;
    const name = card.name ? String(card.name) : "This profile";
    const riskKey = card.risk_effective || card.risk_declared;
    const risk = riskWord(riskKey) || String(riskKey || "unspecified").replace(/_/g, " ");
    const horizon = hasNumber(card.horizon_years) ? Math.round(Number(card.horizon_years)) : null;
    return name + " \u00b7 " + risk + " risk" + (horizon ? " \u00b7 " + horizon + "-year horizon" : "");
  }

  function asOfLine(base) {
    const cone = base.cone && Array.isArray(base.cone.p50) ? base.cone.p50 : [];
    const years = cone.length > 1 ? Math.max(1, Math.round(cone.length / 12)) : 5;
    const date = base.as_of ? String(base.as_of).slice(0, 10) : "2026-11-12";
    return years + " years, CAD, as of " + date;
  }

  function pickScenario(entry, target, funding) {
    const scenarios = entry && entry.scenarios ? entry.scenarios : null;
    if (!scenarios) return null;
    const wanted = keyOf(target, funding);
    if (scenarios[wanted] && !scenarios[wanted].error) return {key: wanted, scenario: scenarios[wanted]};
    const keys = Object.keys(scenarios);
    for (let index = 0; index < keys.length; index += 1) {
      if (scenarios[keys[index]] && !scenarios[keys[index]].error) {
        return {key: keys[index], scenario: scenarios[keys[index]]};
      }
    }
    return null;
  }

  function pillarStatus(statuses, pillar) {
    const found = pillar.factors.map(factor => (statuses || {})[factor]).filter(status => status);
    if (!found.length) return "unknown";
    return found.reduce((worst, status) => (WORST_FIRST_RANK[status] < WORST_FIRST_RANK[worst] ? status : worst), found[0]);
  }

  function pillarFinding(pillarId, context) {
    const before = context.baseMetrics || {};
    const after = context.metrics || {};
    const beforeShares = context.baseShares || {};
    const afterShares = context.shares || {};
    if (pillarId === "objectives") {
      if (!hasNumber(before.p50_T) || !hasNumber(after.p50_T) || !hasNumber(before.p95_T) || !hasNumber(after.p95_T)) {
        return NEUTRAL_FINDING;
      }
      return "The median value at year 5 moves from " + App.fmtCad(before.p50_T) + " to " + App.fmtCad(after.p50_T)
        + " and the optimistic value from " + App.fmtCad(before.p95_T) + " to " + App.fmtCad(after.p95_T) + ".";
    }
    if (pillarId === "risk") {
      if (!hasNumber(before.vol) || !hasNumber(after.vol) || !hasNumber(context.baseLoss) || !hasNumber(context.scenLoss)) {
        return NEUTRAL_FINDING;
      }
      return "Annual volatility moves from " + App.fmtPct(before.vol) + " to " + App.fmtPct(after.vol)
        + " and the bad-scenario loss from " + App.fmtCad(context.baseLoss) + " to " + App.fmtCad(context.scenLoss) + ".";
    }
    if (pillarId === "constraints") {
      if (!hasNumber(beforeShares.single_stocks) || !hasNumber(afterShares.single_stocks)
        || !hasNumber(beforeShares.bonds) || !hasNumber(afterShares.bonds)) {
        return NEUTRAL_FINDING;
      }
      return "Single stocks move from " + App.fmtPct(beforeShares.single_stocks) + " to "
        + App.fmtPct(afterShares.single_stocks) + " of the portfolio and bonds from "
        + App.fmtPct(beforeShares.bonds) + " to " + App.fmtPct(afterShares.bonds) + ".";
    }
    if (context.today === context.withAsset) {
      return "The preference checks stay at the same status (" + (App.statusLabel[context.today] || "Unknown")
        + ") with this asset.";
    }
    return "The preference checks move from " + (App.statusLabel[context.today] || "Unknown") + " to "
      + (App.statusLabel[context.withAsset] || "Unknown") + " with this asset.";
  }

  function findingText(context) {
    const before = context.baseMetrics || {};
    const after = context.metrics || {};
    if (!hasNumber(before.p50_T) || !hasNumber(after.p50_T) || !hasNumber(before.p5_T) || !hasNumber(after.p5_T)
      || !hasNumber(before.vol) || !hasNumber(after.vol) || !hasNumber(context.baseLoss)
      || !hasNumber(context.scenLoss) || !hasNumber(context.amount)) {
      return NEUTRAL_FINDING;
    }
    const worse = context.worsePillars;
    const pillarSentence = worse === 0
      ? "No pillar is worse with this asset."
      : worse + " of the four pillars " + (worse === 1 ? "is" : "are") + " worse with this asset.";
    return "At " + money(context.amount, 2) + " (" + App.fmtPct(context.target, 0) + " of the portfolio) funded from "
      + context.fundingLabel + ", the median value at year 5 moves from " + App.fmtCad(before.p50_T) + " to "
      + App.fmtCad(after.p50_T) + " and the pessimistic value from " + App.fmtCad(before.p5_T) + " to "
      + App.fmtCad(after.p5_T) + ". Annual volatility moves from " + App.fmtPct(before.vol) + " to "
      + App.fmtPct(after.vol) + ". The estimated bad-scenario loss moves from " + App.fmtCad(context.baseLoss)
      + " to " + App.fmtCad(context.scenLoss) + ". " + pillarSentence
      + " This is an analysis of the asset you chose, not a suggestion to buy or avoid it.";
  }

  function sampleView() {
    return SAMPLE;
  }

  function buildView() {
    const data = App.data || {};
    const whatif = data.whatif && typeof data.whatif === "object" ? data.whatif : null;
    const universe = data.universe && data.universe.base ? data.universe : null;
    if (!whatif || !universe) return sampleView();
    const base = universe.base;
    const assets = Array.isArray(universe.assets) ? universe.assets : [];
    const tickers = Object.keys(whatif).filter(ticker => whatif[ticker] && whatif[ticker].asset);
    if (!tickers.length) return sampleView();
    const scenarioCount = Object.keys(whatif).length;
    if (!state.ticker || tickers.indexOf(state.ticker) < 0) state.ticker = tickers[0];

    let fundings = (Array.isArray(base.fundings) ? base.fundings : [])
      .map(funding => ({id: String(funding.id), label: fundingLabel(funding, assets)}));
    if (!fundings.length) fundings = [{id: "pro_rata_all", label: "Pro rata across all holdings"}];
    if (!state.funding || !fundings.some(funding => funding.id === state.funding)) {
      state.funding = fundings[fundings.length - 1].id;
    }

    const value = hasNumber(base.value_cad) ? Number(base.value_cad) : null;
    const targetValues = (Array.isArray(base.targets) ? base.targets : [])
      .map(target => Number(target)).filter(target => Number.isFinite(target));
    if (!targetValues.length) targetValues.push(10);
    if (!targetValues.some(target => target === Number(state.target))) {
      state.target = targetValues[targetValues.length - 1];
    }

    const picked = pickScenario(whatif[state.ticker], state.target, state.funding);
    if (!picked) return sampleView();
    if (picked.key !== keyOf(state.target, state.funding)) {
      const cut = picked.key.indexOf("|");
      state.target = Number(picked.key.slice(0, cut));
      state.funding = picked.key.slice(cut + 1);
      if (!targetValues.some(target => target === Number(state.target))) targetValues.push(Number(state.target));
      if (!fundings.some(funding => funding.id === state.funding)) {
        fundings.push({id: state.funding, label: fundingLabel({id: state.funding}, assets)});
      }
    }

    const scenario = picked.scenario;
    const baseMetrics = base.metrics || {};
    const metrics = scenario.metrics || {};
    const baseShares = base.shares || {};
    const shares = scenario.shares || {};
    const baseLoss = hasNumber(value) && hasNumber(baseMetrics.p5_T) ? value - Number(baseMetrics.p5_T) : null;
    const scenLoss = hasNumber(value) && hasNumber(metrics.p5_T) ? value - Number(metrics.p5_T) : null;
    const beforeStatuses = base.sections || {};
    const afterStatuses = {};
    (scenario.sections || []).forEach(item => {
      if (item && item.factor) afterStatuses[item.factor] = item.after;
    });

    const pillars = PILLARS.map(pillar => {
      const today = pillarStatus(beforeStatuses, pillar);
      const withAsset = pillarStatus(afterStatuses, pillar);
      return {label: pillar.label, today, withAsset, finding: pillarFinding(pillar.id, {
        baseMetrics, metrics, baseShares, shares, baseLoss, scenLoss, today, withAsset
      })};
    });
    const worsePillars = pillars.filter(pillar => WORST_FIRST_RANK[pillar.withAsset] < WORST_FIRST_RANK[pillar.today]).length;
    const funding = fundings.find(item => item.id === state.funding);
    const fundingText = funding ? funding.label : state.funding;

    return {
      assets: tickers.map(ticker => ({value: ticker, label: assetLabel(whatif[ticker].asset)})),
      ticker: state.ticker,
      meta: "Scenarios precomputed offline for " + scenarioCount
        + (scenarioCount === 1 ? " asset" : " assets"),
      targets: targetValues.map(target => ({value: target, label: amountLabel(target, value)})),
      target: state.target,
      fundings,
      funding: state.funding,
      profile: profileLine(data.report),
      asOf: asOfLine(base),
      verdict: verdictStatus(scenario.verdict),
      todayVerdict: verdictStatus(base.verdict),
      withVerdict: verdictStatus(scenario.verdict),
      finding: findingText({
        amount: hasNumber(value) && hasNumber(state.target) ? Number(value) * Number(state.target) / 100 : null,
        target: state.target, fundingLabel: fundingText, baseMetrics, metrics, baseLoss, scenLoss, worsePillars
      }),
      tiles: [
        {id: "p5", label: "Pessimistic (P5)", value: money(metrics.p5_T), tone: "bad", delta: deltaText(baseMetrics.p5_T, metrics.p5_T)},
        {id: "p50", label: "Median", value: money(metrics.p50_T), tone: "mid", delta: deltaText(baseMetrics.p50_T, metrics.p50_T)},
        {id: "p95", label: "Optimistic (P95)", value: money(metrics.p95_T), tone: "good", delta: deltaText(baseMetrics.p95_T, metrics.p95_T)}
      ],
      chart: {
        optimistic: "Optimistic " + kilo(metrics.p95_T),
        median: "Median " + kilo(metrics.p50_T),
        pessimistic: "Pessimistic " + kilo(metrics.p5_T)
      },
      rows: [
        {label: "Median value, year 5", today: App.fmtCad(baseMetrics.p50_T), withAsset: App.fmtCad(metrics.p50_T)},
        {label: "Pessimistic value, year 5", today: App.fmtCad(baseMetrics.p5_T), withAsset: App.fmtCad(metrics.p5_T),
          worse: hasNumber(baseMetrics.p5_T) && hasNumber(metrics.p5_T) && Number(metrics.p5_T) < Number(baseMetrics.p5_T)},
        {label: "Annual volatility", today: App.fmtPct(baseMetrics.vol), withAsset: App.fmtPct(metrics.vol),
          worse: hasNumber(baseMetrics.vol) && hasNumber(metrics.vol) && Number(metrics.vol) > Number(baseMetrics.vol)},
        {label: "Max drawdown", today: App.fmtPct(baseMetrics.mdd), withAsset: App.fmtPct(metrics.mdd),
          worse: hasNumber(baseMetrics.mdd) && hasNumber(metrics.mdd) && Number(metrics.mdd) < Number(baseMetrics.mdd)},
        {label: "Bad-scenario loss", today: App.fmtCad(baseLoss), withAsset: App.fmtCad(scenLoss),
          worse: hasNumber(baseLoss) && hasNumber(scenLoss) && scenLoss > baseLoss},
        {label: "Share in single stocks", today: App.fmtPct(baseShares.single_stocks), withAsset: App.fmtPct(shares.single_stocks)},
        {label: "Share in bonds", today: App.fmtPct(baseShares.bonds), withAsset: App.fmtPct(shares.bonds)}
      ],
      pillars
    };
  }

  function text(mount, key, value) {
    const node = mount.querySelector('[data-wf="' + key + '"]');
    if (node) node.textContent = value;
  }

  function html(mount, key, value) {
    const node = mount.querySelector('[data-wf="' + key + '"]');
    if (node) node.innerHTML = value;
  }

  function fillSelect(select, options, selected) {
    if (!select) return;
    select.innerHTML = options.map(option =>
      '<option value="' + App.esc(String(option.value)) + '">' + App.esc(option.label) + "</option>").join("");
    select.value = String(selected);
  }

  function rowsMarkup(view) {
    return view.rows.map(row =>
      '<tr><td>' + App.esc(row.label) + '</td><td class="r">' + App.esc(minus(row.today)) + '</td>'
      + '<td class="r' + (row.worse ? " wf-bad" : "") + '"><strong>' + App.esc(minus(row.withAsset)) + "</strong></td></tr>"
    ).join("");
  }

  function pillarRowsMarkup(view) {
    return view.pillars.map(pillar =>
      '<tr><td class="wf-pillar">' + App.esc(pillar.label) + "</td>"
      + "<td>" + App.chip(pillar.today) + "</td>"
      + "<td>" + App.chip(pillar.withAsset) + "</td>"
      + "<td>" + App.esc(pillar.finding) + "</td></tr>"
    ).join("");
  }

  /* The selects live in the screen's static markup and survive re-renders, so bind them once
     per element; the handler reads the current state and re-renders the screen. */
  function bindSelect(mount, section, selector, apply) {
    const select = mount.querySelector(selector);
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
    const view = buildView();

    fillSelect(mount.querySelector("#whatif-asset"), view.assets, view.ticker);
    fillSelect(mount.querySelector("#whatif-amount"), view.targets, view.target);
    fillSelect(mount.querySelector("#whatif-funding"), view.fundings.map(funding => ({value: funding.id, label: funding.label})), view.funding);

    text(mount, "meta", view.meta);
    text(mount, "profile", view.profile);
    text(mount, "as-of", view.asOf);
    text(mount, "finding", view.finding);
    html(mount, "verdict-chip", App.chip(view.verdict));
    html(mount, "today-verdict", App.chip(view.todayVerdict));
    html(mount, "with-verdict", App.chip(view.withVerdict));

    text(mount, "fan-optimistic", view.chart.optimistic);
    text(mount, "fan-median", view.chart.median);
    text(mount, "fan-pessimistic", view.chart.pessimistic);

    view.tiles.forEach(tile => {
      const value = mount.querySelector('[data-wf="tile-' + tile.id + '"]');
      if (value) {
        value.textContent = tile.value;
        value.className = "wf-tile__value wf-tile__value--" + tile.tone;
      }
      text(mount, "tile-" + tile.id + "-delta", tile.delta);
    });

    html(mount, "ba-rows", rowsMarkup(view));
    html(mount, "coh-rows", pillarRowsMarkup(view));

    bindSelect(mount, section, "#whatif-asset", value => { state.ticker = value; });
    bindSelect(mount, section, "#whatif-amount", value => { state.target = Number(value); });
    bindSelect(mount, section, "#whatif-funding", value => { state.funding = value; });
  }

  App.renderers.whatif = renderWhatIf;
})();
