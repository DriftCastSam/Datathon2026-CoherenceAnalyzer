(function () {
  "use strict";

  /* Figma sample content (node 7:653): used only when reports/ is unavailable. */
  const SAMPLE = {
    start: 250000, tolerancePct: 0.10, tolerance: 25000, lossPct: 0.24, loss: 60000,
    ratio: 2.4, vol: 0.168, mdd: -0.24, profileLevel: 1, portfolioLevel: 3
  };
  const SAMPLE_CONTRIB = [
    {name: "US technology stock [TICKER]", share: 46, weight: 22},
    {name: "Canadian equities", share: 27, weight: 25},
    {name: "Bitcoin", share: 14, weight: 5},
    {name: "Aggregate bond ETF", share: 9, weight: 35},
    {name: "Gold ETF", share: 4, weight: 8},
    {name: "Cash", share: 0, weight: 5}
  ];
  const SAMPLE_FACTS = [
    {label: "Declared risk level", value: "Low to moderate"},
    {label: "Effective risk level", value: "Low to moderate"},
    {label: "Volatility band", value: "5% to 9%"},
    {label: "Max drawdown limit", value: "\u221212%"}
  ];
  const LEVELS = [
    {key: "low", band: [0.02, 0.06]},
    {key: "low_moderate", band: [0.05, 0.09]},
    {key: "moderate", band: [0.08, 0.12]},
    {key: "moderate_high", band: [0.11, 0.16]},
    {key: "high", band: [0.14, 0.22]}
  ];
  const REACTION_WORDS = {sell_all: "Sell all", sell_some: "Sell some", hold: "Hold", buy_more: "Buy more"};
  const DECLARED_LABELS = {
    "risk.declared": "Declared risk level",
    "risk.capacity": "Risk capacity",
    "risk.effective": "Effective risk level",
    "risk.behavioural": "Behavioural risk",
    "risk.max_acceptable_drawdown_pct": "Max acceptable drawdown",
    "risk.reaction_to_20pct_drop": "Reaction to a 20% drop"
  };
  const CHECK_NAMES = {
    H4: "Fixed income weight",
    H7: "Annual volatility",
    H8: "Max drawdown",
    H14: "Stated reaction to a 20% drop",
    S1: "Behaviour vs effective risk",
    S3: "Hype pattern in the trade"
  };
  const FIGMA_LEAD = "The portfolio behaves like a moderate-to-high risk mix, two steps above the profile.";
  const STEP_WORDS = ["at the profile", "one step", "two steps", "three steps", "four steps"];

  /* ---------- formatting ---------- */

  function num(value, fallback) {
    return typeof value === "number" && isFinite(value) ? value : fallback;
  }

  function amount(value) {
    return "$" + App.fmtCad(value);
  }

  function negativePct(fraction) {
    return "\u2212" + App.fmtPct(Math.abs(fraction));
  }

  function prettify(value) {
    const key = String(value);
    if (App.riskLevels[key]) return App.riskLevels[key];
    if (REACTION_WORDS[key]) return REACTION_WORDS[key];
    const words = key.replace(/_/g, " ");
    return words.charAt(0).toUpperCase() + words.slice(1);
  }

  function worstStatus(sections, factors, fallback) {
    let status = null;
    factors.forEach(factor => {
      const section = sections.find(item => item && item.factor === factor);
      if (!section || !section.status) return;
      if (status === null || App.statusRank[section.status] > App.statusRank[status]) status = section.status;
    });
    return status || fallback;
  }

  /* ---------- data ---------- */

  function levelOf(vol) {
    let best = null;
    LEVELS.forEach((level, index) => {
      const low = level.band[0];
      const high = level.band[1];
      const distance = vol < low ? low - vol : vol > high ? vol - high : 0;
      if (best === null || distance < best.distance) best = {index: index, distance: distance};
    });
    return best === null ? null : best.index;
  }

  function profileLevelOf(card) {
    const key = card && card.risk_effective;
    if (typeof key !== "string") return null;
    const index = LEVELS.findIndex(level => level.key === key);
    return index >= 0 ? index : null;
  }

  function readState() {
    const report = App.data && App.data.report;
    const site = report && report.site;
    const metrics = report && report.metrics_current;
    const limits = site && site.limits;
    const live = !!(report && metrics && limits);
    const sections = (report && Array.isArray(report.sections)) ? report.sections : [];
    const status = worstStatus(sections, ["risque"], live ? "pass" : "violation");
    if (!live) {
      return {
        live: false, status: status, start: SAMPLE.start,
        tolerancePct: SAMPLE.tolerancePct, tolerance: SAMPLE.tolerance,
        lossPct: SAMPLE.lossPct, loss: SAMPLE.loss, ratio: SAMPLE.ratio,
        vol: SAMPLE.vol, mdd: SAMPLE.mdd,
        profileLevel: SAMPLE.profileLevel, portfolioLevel: SAMPLE.portfolioLevel,
        band: [0.05, 0.09], ddLimitPct: -12, drivers: [], checks: [],
        sections: []
      };
    }
    const start = num(site.value_cad, SAMPLE.start);
    const p5 = num(metrics.p5_T, start);
    const loss = Math.max(0, start - p5);
    const ddLimit = num(limits.max_drawdown_pct, null);
    const tolerancePct = ddLimit === null ? SAMPLE.tolerancePct : Math.abs(ddLimit) / 100;
    const tolerance = tolerancePct * start;
    const vol = num(metrics.vol, null);
    return {
      live: true,
      status: status,
      start: start,
      tolerancePct: tolerancePct,
      tolerance: tolerance,
      lossPct: start > 0 ? loss / start : 0,
      loss: loss,
      ratio: tolerance > 0 ? loss / tolerance : null,
      vol: vol,
      mdd: num(metrics.mdd, null),
      profileLevel: profileLevelOf(site.profile_card),
      portfolioLevel: vol === null ? null : levelOf(vol),
      band: Array.isArray(limits.vol_band_annual) ? limits.vol_band_annual : null,
      ddLimitPct: ddLimit,
      drivers: Array.isArray(metrics.drivers) ? metrics.drivers : [],
      checks: (report && Array.isArray(report.checks_current)) ? report.checks_current : [],
      sections: sections,
      facts: null
    };
  }

  /* ---------- lead ---------- */

  function lead(state) {
    if (!state.live) return FIGMA_LEAD;
    if (state.portfolioLevel === null) {
      return state.vol === null ? "Annual volatility is not available for this portfolio."
        : "The portfolio's annual volatility is " + App.fmtPct(state.vol) + ".";
    }
    const mix = App.riskLevels[LEVELS[state.portfolioLevel].key].toLowerCase().replace(/ /g, "-");
    if (state.profileLevel === null) return "The portfolio behaves like a " + mix + " risk mix.";
    const gap = state.portfolioLevel - state.profileLevel;
    if (gap === 0) return "The portfolio behaves like a " + mix + " risk mix, in line with the profile.";
    const steps = STEP_WORDS[Math.abs(gap)] || Math.abs(gap) + " steps";
    const direction = gap > 0 ? "above" : "below";
    return "The portfolio behaves like a " + mix + " risk mix, " + steps + " " + direction + " the profile.";
  }

  /* ---------- loss comparison ---------- */

  function lossRow(state, label, value, fraction, tone) {
    const widest = Math.max(state.tolerance, state.loss) || 1;
    const width = Math.min(100, (value / widest) * 100);
    return '<div class="loss__row">'
      + '<div class="loss__label"><span>' + App.esc(label) + "</span>"
      + '<span class="loss__val' + (tone === "bad" ? " loss__val--bad" : "") + '">'
      + App.esc(negativePct(fraction) + " \u00b7 " + amount(value)) + "</span></div>"
      + '<div class="loss__bar loss__bar--' + (tone === "bad" ? "red" : "navy") + '" style="width:'
      + width.toFixed(2) + '%"></div></div>';
  }

  function lossCard(state) {
    return '<section class="card loss">'
      + '<h2 class="t-h3">Loss in a bad scenario</h2>'
      + lossRow(state, "Loss the profile tolerates", state.tolerance, state.tolerancePct, "ok")
      + lossRow(state, "Estimated loss of this portfolio", state.loss, state.lossPct, "bad")
      + "</section>";
  }

  function ratioCard(state) {
    const ratio = state.ratio === null ? "\u2014" : App.fmtCad(state.ratio, 1) + "\u00d7";
    const vol = state.vol === null ? "\u2014" : App.fmtPct(state.vol);
    const mdd = state.mdd === null ? "\u2014" : negativePct(state.mdd);
    return '<section class="card ratio">'
      + '<p class="ratio__k">Estimated loss over tolerance</p>'
      + '<p class="ratio__v">' + App.esc(ratio) + "</p>"
      + '<div class="ratio__figs">'
      + '<div class="figure"><span class="figure__k">Annual volatility</span>'
      + '<span class="figure__v">' + App.esc(vol) + "</span></div>"
      + '<div class="figure"><span class="figure__k">Max drawdown</span>'
      + '<span class="figure__v">' + App.esc(mdd) + "</span></div>"
      + "</div></section>";
  }

  /* ---------- risk level scale ---------- */

  function scale(state) {
    return '<div class="scale">' + LEVELS.map((level, index) => {
      const isProfile = state.profileLevel === index;
      const isPortfolio = state.portfolioLevel === index;
      const tags = [];
      if (isProfile) tags.push("Profile");
      if (isPortfolio) tags.push("Portfolio");
      const tone = isProfile ? "profile" : isPortfolio ? "portfolio" : "";
      return '<div class="scale__col">'
        + '<p class="scale__tag' + (tone ? " scale__tag--" + tone : "") + '">'
        + (tags.length ? App.esc(tags.join(" \u00b7 ")) : "&nbsp;") + "</p>"
        + '<div class="scale__bar' + (tone ? " scale__bar--" + tone : "") + '"></div>'
        + '<p class="scale__name">' + App.esc(App.riskLevels[level.key]) + "</p></div>";
    }).join("") + "</div>";
  }

  function levelCard(state) {
    return '<section class="card"><h2 class="t-h3">Risk level</h2>' + scale(state) + "</section>";
  }

  /* ---------- loss contribution ---------- */

  function contributionRows(state) {
    if (!state.live) return SAMPLE_CONTRIB;
    const held = state.drivers.filter(driver => driver && driver.weight > 0);
    if (!held.length) return [];
    const widest = Math.max.apply(null, held.map(driver => num(driver.risk, 0))) || 1;
    return held.map(driver => ({
      name: driver.name || driver.ticker || "",
      share: num(driver.risk, 0),
      weight: num(driver.weight, 0),
      width: num(driver.risk, 0) / widest
    }));
  }

  function contributionRowsHtml(state) {
    const rows = contributionRows(state);
    if (!rows.length) return '<p class="t-body muted">No holding weights are available for this portfolio.</p>';
    const sampleMax = 46;
    return rows.map(row => {
      const share = state.live ? row.share : row.share / 100;
      const weight = state.live ? row.weight : row.weight / 100;
      const width = state.live ? row.width : row.share / sampleMax;
      const heavy = share > weight;
      return '<div class="contrib__row">'
        + '<p class="contrib__name">' + App.esc(row.name) + "</p>"
        + '<div class="contrib__area"><div class="contrib__bar contrib__bar--' + (heavy ? "heavy" : "light")
        + '" style="width:' + Math.max(0.5, Math.min(100, width * 100)).toFixed(2) + '%"></div></div>'
        + '<p class="contrib__pct' + (heavy ? " contrib__pct--heavy" : "") + '">'
        + App.esc(App.fmtPct(share) + " of loss") + "</p>"
        + '<p class="contrib__w">' + App.esc(App.fmtPct(weight) + " weight") + "</p></div>";
    }).join("");
  }

  function contributionCard(state) {
    return '<section class="card">'
      + '<h2 class="t-h3">Where the estimated loss comes from</h2>'
      + '<p class="contrib__note">Bar: share of the estimated loss. Red when a holding weighs more in the loss '
      + "than in the portfolio.</p>"
      + contributionRowsHtml(state)
      + "</section>";
  }

  /* ---------- detail: declared values, checks, narrative ---------- */

  function factRows(state) {
    if (!state.live) return SAMPLE_FACTS;
    const rows = [];
    const seen = {};
    const section = state.sections.find(item => item && item.factor === "risque");
    if (section && section.facts) {
      const facts = section.facts;
      const add = (path, value, by) => {
        const shown = path.endsWith("_pct") && typeof value === "number" ? negativePct(value / 100) : value;
        const row = {label: DECLARED_LABELS[path] || path, value: shown, by: by || ""};
        const key = row.label + "|" + row.value;
        if (seen[key]) return;
        seen[key] = true;
        rows.push(row);
      };
      Object.keys(facts.declared || {}).forEach(path => {
        const raw = facts.declared[path];
        if (path.endsWith("_pct") && typeof raw === "number") {
          add(path, negativePct(raw / 100));
        } else {
          add(path, App.factText(raw, prettify));
        }
      });
      Object.keys(facts.derived || {}).forEach(path => {
        const node = facts.derived[path] || {};
        const raw = node.value;
        const shown = path.endsWith("_pct") && typeof raw === "number" ? negativePct(raw / 100) : App.factText(raw, prettify);
        add(path, shown, node.by);
      });
    }
    if (state.band) {
      rows.push({label: "Volatility band", value: App.fmtPct(state.band[0]) + " to " + App.fmtPct(state.band[1])});
    }
    if (state.ddLimitPct !== null) {
      rows.push({label: "Max drawdown limit", value: negativePct(state.ddLimitPct / 100)});
    }
    return rows;
  }

  function detailCard(state) {
    const rows = factRows(state);
    const checks = state.checks.filter(check => check && check.factor === "risque");
    const section = state.sections.find(item => item && item.factor === "risque");
    const narrative = section && section.narrative && Array.isArray(section.narrative.segments)
      ? section.narrative.segments : [];
    const facts = rows.length
      ? '<div class="facts">' + rows.map(row => '<div class="fact"><p class="fact__k">' + App.esc(row.label)
        + '</p><p class="fact__v">' + App.esc(row.value) + "</p>"
        + (row.by ? '<p class="fact__by">' + App.esc(row.by) + "</p>" : "") + "</div>").join("") + "</div>"
      : "";
    const checkList = checks.length
      ? '<h3 class="detail__sub">Checks</h3><ul class="checks">'
        + checks.map(check => App.checkRow(check, CHECK_NAMES)).join("") + "</ul>"
      : "";
    const text = narrative.length
      ? '<h3 class="detail__sub">What the analysis says</h3><div class="nar">'
        + narrative.map(segment => '<p class="t-body">' + App.esc(segment.text || "") + "</p>").join("")
        + "</div>"
      : "";
    return '<section class="card"><h2 class="t-h3">Risk detail</h2>'
      + facts + checkList + text + "</section>";
  }

  /* ---------- render ---------- */

  App.renderers.risk = function (section) {
    const root = section.querySelector(".scr");
    if (!root) return;
    const state = readState();
    root.innerHTML = [
      '<a class="back" href="#/summary">'
        + '<img class="i16" src="assets/icon-back.svg" alt="" width="16" height="16">'
        + "<span>Accounts summary</span></a>",
      '<div class="title-row"><h1 class="t-title">Risk</h1>' + App.chip(state.status) + "</div>",
      '<p class="t-body muted">' + App.esc(lead(state)) + "</p>",
      '<div class="loss-row">' + lossCard(state) + ratioCard(state) + "</div>",
      levelCard(state),
      contributionCard(state),
      detailCard(state),
      '<p class="t-small muted">Figures are computed first, then explained by a language model. '
        + "Illustrative sample data. Simulation only, not financial advice.</p>"
    ].join("");
  };
})();
