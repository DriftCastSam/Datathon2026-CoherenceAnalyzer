(function () {
  "use strict";

  /* Figma sample content (node 7:494): used only when reports/ is unavailable. */
  const SAMPLE = {
    goal: 250000, start: 250000, months: 60, floor: 250000,
    p5: 214000, p25: 261000, p50: 298000, p75: 331000, p95: 371000
  };
  /* The design's 21-point cone, read back from its plot paths (values in CAD). */
  const SAMPLE_CONE = {
    p5: [250000, 242000, 236000, 230000, 227000, 228000, 223000, 217000, 217000, 219000, 217000,
         215000, 218000, 218000, 213000, 211000, 215000, 216000, 213000, 214000, 214000],
    p25: [250000, 251000, 252000, 252000, 250000, 253000, 257000, 255000, 254000, 256000, 257000,
          255000, 256000, 261000, 261000, 258000, 259000, 261000, 260000, 260000, 260000],
    p50: [250000, 253000, 256000, 259000, 260000, 261000, 265000, 269000, 269000, 273000, 277000,
          277000, 277000, 281000, 285000, 287000, 288000, 292000, 294000, 294000, 298000],
    p75: [250000, 254000, 258000, 265000, 269000, 271000, 275000, 281000, 283000, 285000, 293000,
          299000, 300000, 302000, 308000, 312000, 313000, 319000, 326000, 329000, 331000],
    p95: [250000, 257000, 262000, 269000, 278000, 283000, 287000, 297000, 304000, 304000, 309000,
          320000, 327000, 329000, 337000, 345000, 347000, 349000, 359000, 369000, 371000]
  };
  const SCENARIOS = [
    {key: "p5", label: "Pessimistic (P5)", short: "pessimistic", tone: "bad"},
    {key: "p50", label: "Median", short: "median", tone: "ink"},
    {key: "p95", label: "Optimistic (P95)", short: "optimistic", tone: "ok"}
  ];
  const SECTION_TITLES = {objectif: "Objective", horizon: "Horizon"};
  const DECLARED_LABELS = {
    "objectives.primary": "Primary goal",
    "objectives.secondary": "Secondary goals",
    "objectives.goals": "Goals",
    "identity.life_stage": "Life stage",
    "objectives.horizon_years": "Declared horizon",
    "objectives.effective_horizon_years": "Effective horizon",
    "situation.planned_withdrawals": "Planned withdrawals",
    "identity.retirement_target_age": "Retirement target age"
  };
  const CHECK_NAMES = {G1: "Goal probability", O1: "Objective vs portfolio", H9: "Pessimistic value at the horizon"};
  const FIGMA_LEAD = "The median and optimistic scenarios keep the capital intact over 3 to 5 years. "
    + "Only the pessimistic one ends below the goal.";
  const FIGMA_NOTE = "12-month block simulation on monthly returns, 2022-01-31 to 2026-10-31, 5,000 paths, "
    + "expected returns set by the team. This is not a forecast. Figures on this draft are illustrative sample data.";
  const SAMPLE_FACTS = [
    {label: "Primary goal", value: "Capital preservation with growth"},
    {label: "Declared horizon", value: "3 to 5 years"},
    {label: "Effective horizon", value: "3.6 years"}
  ];

  /* ---------- formatting ---------- */

  const {num, amount} = App.format;

  function signedPct(value, base) {
    if (!base) return App.fmtPct(value);
    const percent = Math.round((value / base - 1) * 100);
    if (percent === 0) return App.fmtPct(0);
    return (percent > 0 ? "+" : "\u2212") + App.fmtPct(Math.abs(percent));
  }

  function compact(value) {
    const abs = Math.abs(value);
    if (abs >= 1e6) {
      const millions = value / 1e6;
      return String(Math.round(millions * 100) / 100) + "M";
    }
    if (abs >= 1e3) return App.fmtCad(Math.round(value / 1e3)) + "k";
    return App.fmtCad(value);
  }

  function span(years) {
    return years === 1 ? "1 year" : years + " years";
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

  function readState() {
    const report = App.data && App.data.report;
    const site = report && report.site;
    const metrics = report && report.metrics_current;
    const goals = report && report.goals;
    const cone = metrics && metrics.cone;
    const live = !!(cone && Array.isArray(cone.p50) && cone.p50.length > 1);
    const probs = goals && Array.isArray(goals.goal_probs) ? goals.goal_probs : [];
    const limits = (site && site.limits) || null;
    const sections = (report && Array.isArray(report.sections)) ? report.sections : [];
    const months = live ? cone.p50.length
      : num(goals && goals.horizon_months, SAMPLE.months);
    return {
      live: live,
      months: months,
      start: num(site && site.value_cad, live ? num(cone.p50[0], SAMPLE.start) : SAMPLE.start),
      goal: live ? (probs.length ? num(probs[0].amount, null) : null) : SAMPLE.goal,
      p5: num(metrics && metrics.p5_T, SAMPLE.p5),
      p25: live ? num(cone.p25 && cone.p25[cone.p25.length - 1], SAMPLE.p25) : SAMPLE.p25,
      p50: num(metrics && metrics.p50_T, SAMPLE.p50),
      p75: live ? num(cone.p75 && cone.p75[cone.p75.length - 1], SAMPLE.p75) : SAMPLE.p75,
      p95: num(metrics && metrics.p95_T, SAMPLE.p95),
      floor: num(limits && limits.floor_at_horizon, live ? null : SAMPLE.floor),
      cone: live ? cone : SAMPLE_CONE,
      probs: probs,
      goalChecks: (goals && Array.isArray(goals.checks)) ? goals.checks : [],
      checks: (report && Array.isArray(report.checks_current)) ? report.checks_current : [],
      sections: sections,
      window: (report && Array.isArray(report.data_window)) ? report.data_window : null,
      asOf: (site && site.as_of) || null,
      status: worstStatus(sections, ["objectif", "horizon"], "pass")
    };
  }

  /* ---------- lead paragraph and range plot ---------- */

  function lead(state, years) {
    if (!state.live) return FIGMA_LEAD;
    if (state.goal === null) {
      return "No declared goal falls inside the " + span(years) + " simulation horizon, "
        + "so the scenarios are compared with today's value.";
    }
    const reaching = SCENARIOS.filter(item => state[item.key] >= state.goal);
    const missing = SCENARIOS.filter(item => state[item.key] < state.goal);
    if (reaching.length === 3) return "All three scenarios keep the capital intact over " + span(years) + ".";
    if (reaching.length === 2) {
      return "The " + reaching[0].short + " and " + reaching[1].short + " scenarios keep the capital intact over "
        + span(years) + ". Only the " + missing[0].short + " one ends below the goal.";
    }
    if (reaching.length === 1) {
      return "Only the " + reaching[0].short + " scenario keeps the capital intact over " + span(years)
        + "; the other two end below the goal.";
    }
    return "None of the three scenarios keeps the capital intact over " + span(years) + ".";
  }

  function rangePercent(value, state) {
    const low = state.p5;
    const high = state.p95;
    const width = high - low;
    const x = width > 0 ? 107.2 + ((value - low) / width) * 904.6 : 594;
    const clamped = Math.min(1180, Math.max(20, x));
    return clamped / 12;
  }

  function label(text, percent, variant, position, align) {
    const alignClass = align === "before" ? " range__lab--before" : align === "after" ? " range__lab--after" : "";
    return '<span class="range__lab range__lab--' + position + (variant ? " range__lab--" + variant : "")
      + alignClass + '" style="left:' + percent.toFixed(2) + '%">' + App.esc(text) + "</span>";
  }

  function rangeCard(state, years) {
    const p5 = rangePercent(state.p5, state);
    const p95 = rangePercent(state.p95, state);
    const p25 = rangePercent(state.p25, state);
    const p75 = rangePercent(state.p75, state);
    const median = rangePercent(state.p50, state);
    const goal = state.goal === null ? null : rangePercent(state.goal, state);
    const marks = [
      '<div class="range__track" style="left:1.67%;width:96.67%"></div>',
      '<div class="range__band range__band--outer" style="left:' + p5.toFixed(2) + "%;width:"
        + (p95 - p5).toFixed(2) + '%"></div>',
      '<div class="range__band range__band--inner" style="left:' + Math.min(p25, p75).toFixed(2)
        + "%;width:" + Math.abs(p75 - p25).toFixed(2) + '%"></div>',
      goal === null ? "" : '<div class="range__tick" style="left:' + goal.toFixed(2) + '%"></div>',
      '<div class="range__dot" style="left:' + median.toFixed(2) + '%"></div>'
    ].join("");
    const tightTop = goal !== null && Math.abs(goal - median) < 12;
    const tightLow = Math.abs(p5 - median) < 12;
    const tightHigh = Math.abs(p95 - median) < 12;
    const labels = [
      goal === null
        ? '<span class="range__lab range__lab--top range__lab--goal" style="left:1.67%">Goal \u2014</span>'
        : label("Goal " + amount(state.goal), goal, "goal", "top", tightTop ? "before" : ""),
      label("Median " + amount(state.p50), median, "median", "top", tightTop ? "after" : ""),
      label("P5 " + amount(state.p5), p5, "", "bottom", tightLow ? "before" : ""),
      label("P25 to P75", median, "", "bottom", tightLow ? "after" : tightHigh ? "before" : ""),
      label("P95 " + amount(state.p95), p95, "", "bottom", tightHigh ? "after" : "")
    ].join("");
    return '<section class="card">'
      + '<h2 class="t-h3">Goal against projected range, year ' + App.esc(years) + "</h2>"
      + '<div class="range" role="img" aria-label="Projected value at year ' + App.esc(years) + ": "
      + (goal === null ? "no goal inside the horizon" : "goal " + App.esc(amount(state.goal)))
      + ", median " + App.esc(amount(state.p50))
      + ", pessimistic " + App.esc(amount(state.p5)) + ", optimistic " + App.esc(amount(state.p95)) + '.">'
      + marks + labels
      + "</div></section>";
  }

  /* ---------- scenario cards ---------- */

  function scenarioTile(state, item) {
    const value = state[item.key];
    const reached = state.goal !== null && value >= state.goal;
    const icon = reached ? "icon-ok" : "icon-bad";
    const tone = state.goal === null ? "" : reached ? "ok" : "bad";
    const note = state.goal === null
      ? signedPct(value, state.start) + " vs today"
      : reached ? amount(value - state.goal) + " above the goal" : amount(state.goal - value) + " below the goal";
    return '<article class="card tile">'
      + '<div class="tile__head">'
      + '<img class="tile__icon" src="assets/' + icon + '.svg" alt="" width="28" height="28">'
      + '<p class="t-body muted">' + App.esc(item.label) + "</p></div>"
      + '<p class="t-num-l-strong">' + App.esc(amount(value)) + "</p>"
      + '<p class="t-small-strong ' + tone + '">' + App.esc(note) + "</p>"
      + "</article>";
  }

  function scoreTile(state) {
    const met = state.goal === null ? null
      : SCENARIOS.filter(item => state[item.key] >= state.goal).length;
    const dots = [];
    if (met === null) {
      dots.push('<span class="dot"></span><span class="dot"></span><span class="dot"></span>');
    } else {
      for (let index = 0; index < 3; index += 1) {
        dots.push('<span class="dot ' + (index < met ? "dot--ok" : "dot--bad") + '"></span>');
      }
    }
    return '<article class="card tile">'
      + '<p class="t-body muted">Scenarios meeting the goal</p>'
      + '<p class="t-num-xl">' + (met === null ? "\u2014" : met + " of 3") + "</p>"
      + '<div class="dots">' + dots.join("") + "</div>"
      + "</article>";
  }

  function scenariosRow(state) {
    return '<div class="scenarios">' + SCENARIOS.map(item => scenarioTile(state, item)).join("")
      + scoreTile(state) + "</div>";
  }

  function legend() {
    const items = [
      {cls: "sw", style: "background:rgba(23,43,64,.08)", label: "P5 to P95"},
      {cls: "sw", style: "background:rgba(23,43,64,.22)", label: "P25 to P75"},
      {cls: "sw sw--line", style: "", label: "Median"},
      {cls: "sw sw--line sw--red", style: "", label: "Pessimistic scenario (P5)"},
      {cls: "sw sw--line sw--red sw--dash", style: "", label: "Floor"}
    ];
    return '<div class="legend">' + items.map(item =>
      '<span class="legend__item"><span class="' + item.cls + '"' + (item.style ? ' style="' + item.style + '"' : "")
      + "></span><span class=\"legend__txt\">" + App.esc(item.label) + "</span></span>").join("") + "</div>";
  }

  function projectionTiles(state) {
    return '<div class="proj-tiles">' + SCENARIOS.map(item => {
      const value = state[item.key];
      const tone = item.tone === "bad" ? "bad" : item.tone === "ok" ? "ok" : "";
      return '<div class="proj-tile">'
        + '<p class="proj-tile__k">' + App.esc(item.label) + "</p>"
        + '<p class="proj-tile__v ' + tone + '">' + App.esc(amount(value)) + "</p>"
        + '<p class="proj-tile__d">' + App.esc(signedPct(value, state.start)) + " vs today</p>"
        + "</div>";
    }).join("") + "</div>";
  }

  function projectionNote(state) {
    if (!state.live) return FIGMA_NOTE;
    const window = state.window && state.window.length === 2 ? state.window : null;
    const from = window ? state.window[0] : null;
    const to = window ? state.window[1] : null;
    const dates = from && to ? ", " + from + " to " + to : "";
    return "12-month block simulation on monthly returns" + dates + ", 5,000 paths, expected returns set by the team. "
      + "This is not a forecast. Figures on this draft are illustrative sample data.";
  }

  function projectedCard(state, years) {
    const heading = years + " years, CAD, as of " + (state.asOf || "2026-11-12");
    return '<section class="card card--tight">'
      + '<div class="fan-head"><h2 class="t-h3">Projected portfolio value</h2>'
      + '<p class="t-small label">' + App.esc(heading) + "</p></div>"
      + '<div data-chart="objectives"><p class="t-small muted">Chart unavailable</p></div>'
      + legend()
      + projectionTiles(state)
      + '<p class="t-small muted">' + App.esc(projectionNote(state)) + "</p>"
      + "</section>";
  }

  /* ---------- detail: declared values, goal probabilities, checks, narrative ---------- */

  function factRows(state) {
    if (!state.live) return SAMPLE_FACTS;
    const rows = [];
    const seen = {};
    ["objectif", "horizon"].forEach(factor => {
      const section = state.sections.find(item => item && item.factor === factor);
      if (!section || !section.facts) return;
      const facts = section.facts;
      const declared = facts.declared || {};
      Object.keys(declared).forEach(path => {
        if (path === "objectives.goals") return;           // the goal table below lists them
        const row = {label: DECLARED_LABELS[path] || path, value: App.factText(declared[path])};
        const key = row.label + "|" + row.value;
        if (seen[key]) return;
        seen[key] = true;
        rows.push(row);
      });
      const derived = facts.derived || {};
      Object.keys(derived).forEach(path => {
        const node = derived[path] || {};
        const row = {label: DECLARED_LABELS[path] || path, value: App.factText(node.value), by: node.by || ""};
        const key = row.label + "|" + row.value;
        if (seen[key]) return;
        seen[key] = true;
        rows.push(row);
      });
    });
    return rows;
  }

  function goalsTable(state) {
    const rows = state.probs.map(item => {
      return '<tr><td>' + App.esc(item.name) + "</td><td>" + App.esc(item.date) + '</td><td class="r">'
        + App.esc(amount(item.amount)) + '</td><td class="r">' + App.fmtPct(item.prob) + "</td></tr>";
    });
    state.goalChecks.forEach(check => {
      if (check.value !== null && check.value !== undefined) return;
      rows.push('<tr><td>' + App.esc(check.subject || "Goal") + "</td><td>" + App.esc(check.date || "\u2014")
        + '</td><td class="r">' + App.esc(amount(check.amount)) + '</td><td class="r">\u2014</td></tr>');
    });
    if (!rows.length) {
      /* Live report without goals: say so instead of showing the sample goal. */
      if (state.live) {
        rows.push('<tr><td class="muted">No goals recorded</td><td class="muted">\u2014</td>'
          + '<td class="r muted">\u2014</td><td class="r muted">\u2014</td></tr>');
      } else {
        rows.push('<tr><td>Goal</td><td>Year ' + App.esc(Math.max(1, Math.round(state.months / 12)))
          + '</td><td class="r">' + App.esc(amount(state.goal || SAMPLE.goal)) + '</td><td class="r">\u2014</td></tr>');
      }
    }
    return '<div class="tbl"><table class="table"><thead><tr><th>Goal</th><th>Date</th><th class="r">Amount</th>'
      + '<th class="r">Probability</th></tr></thead><tbody>' + rows.join("") + "</tbody></table></div>";
  }

  function checksFor(state) {
    const related = state.checks.filter(check => check && (check.factor === "objectif" || check.factor === "horizon"));
    return related.concat(state.goalChecks.filter(check => check && typeof check === "object"));
  }

  function narrativeBlocks(state) {
    return ["objectif", "horizon"].map(factor => {
      const section = state.sections.find(item => item && item.factor === factor);
      const narrative = section && section.narrative;
      const segments = narrative && Array.isArray(narrative.segments) ? narrative.segments : [];
      if (!segments.length) return "";
      return '<div class="nar"><p class="nar__sec">' + App.esc(SECTION_TITLES[factor]) + "</p>"
        + segments.map(segment => '<p class="t-body">' + App.esc(segment.text || "") + "</p>").join("")
        + "</div>";
    }).join("");
  }

  function allSegments(state) {
    return ["objectif", "horizon"].reduce((all, factor) => {
      const section = state.sections.find(item => item && item.factor === factor);
      const segments = section && section.narrative && Array.isArray(section.narrative.segments)
        ? section.narrative.segments : [];
      return all.concat(segments);
    }, []);
  }

  function detailCard(state) {
    const rows = factRows(state);
    const checks = checksFor(state);
    const segments = allSegments(state);
    const narrative = narrativeBlocks(state);
    const facts = rows.length
      ? '<div class="facts">' + rows.map(row => '<div class="fact"><p class="fact__k">' + App.esc(row.label)
        + '</p><p class="fact__v">' + App.esc(row.value) + "</p>"
        + (row.by ? '<p class="fact__by">' + App.esc(row.by) + "</p>" : "") + "</div>").join("") + "</div>"
      : "";
    const checkList = checks.length
      ? '<h3 class="detail__sub">Checks</h3><ul class="checks">'
        + checks.map(check => App.checkRow(check, CHECK_NAMES, segments)).join("") + "</ul>"
      : "";
    return '<section class="card">'
      + '<h2 class="t-h3">Objectives and horizon detail</h2>'
      + facts
      + '<h3 class="detail__sub">Goal probabilities</h3>'
      + goalsTable(state)
      + checkList
      + (narrative ? '<h3 class="detail__sub">What the analysis says</h3>' + narrative : "")
      + "</section>";
  }

  /* ---------- render ---------- */

  App.renderers.objectives = function (section) {
    const root = section.querySelector(".scr");
    if (!root) return;
    const state = readState();
    const years = Math.max(1, Math.round(state.months / 12));
    root.innerHTML = [
      '<a class="back" href="#/summary">'
        + '<img class="i16" src="assets/icon-back.svg" alt="" width="16" height="16">'
        + "<span>Accounts summary</span></a>",
      '<div class="title-row"><h1 class="t-title">Objectives and horizon</h1>'
        + App.chip(state.status) + "</div>",
      '<p class="t-body muted">' + App.esc(lead(state, years)) + "</p>",
      rangeCard(state, years),
      scenariosRow(state),
      projectedCard(state, years),
      detailCard(state),
      '<p class="t-small muted">Figures are computed first, then explained by a language model. '
        + "Illustrative sample data. Simulation only, not financial advice.</p>"
    ].join("");
    App.charts.fan(root.querySelector('[data-chart="objectives"]'), {
      cone: state.cone, floor: state.floor, years, asOf: state.asOf || undefined
    });
  };
})();
