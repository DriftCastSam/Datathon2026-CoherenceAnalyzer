(() => {
  "use strict";

  const OPTION_SEPARATOR = "|";
  const CAD_SIGN = "$";
  const MONTHS_PER_YEAR = 12;
  const DEFAULT_HORIZON_YEARS = 5;
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const PALETTE = ["var(--navy)", "var(--alloc-steel)", "var(--brand)", "var(--alloc-sky)", "var(--blue)", "var(--alloc-grey)"];
  const LIVE_PALETTE = ["var(--navy)", "var(--alloc-steel)", "var(--alloc-sky)", "var(--blue)", "var(--alloc-grey)"];
  /* Summary rows rank `unknown` below `pass`: a status the report has not evaluated must not
     beat a real result here (App.statusRank ranks it above pass, for statuses that exist). */
  const STATUS_RANK_UNKNOWN_LAST = {unknown: -1, pass: 0, info: 1, warning: 2, violation: 3};
  const VERDICT_STATUS = {coherent: "pass", coherent_with_warnings: "warning", incoherent: "violation"};
  const EXPERIENCE_LABELS = {
    beginner: "Beginner",
    intermediate: "Intermediate",
    advanced: "Advanced",
    expert: "Expert"
  };
  const SAMPLE = {
    value: 250000,
    p5: 214000,
    p50: 298000,
    p95: 371000,
    floor: 250000,
    vol: 0.168,
    mdd: -0.24,
    asOf: "14 Nov 2026, 17:40",
    summary: "Robert's profile holds together: a short horizon, a capital-preservation goal and a low-to-moderate risk level point the same way. The portfolio does not fully match it. A 22% position in one high-volatility technology stock breaks the concentration constraint and pushes the bad-scenario loss well past what the profile tolerates.",
    holdings: [
      {name: "Aggregate bond ETF", pct: 35},
      {name: "Canadian equities", pct: 25},
      {name: "US technology stock [TICKER]", pct: 22, tag: "Flagged"},
      {name: "Gold ETF", pct: 8},
      {name: "Bitcoin", pct: 5, tag: "Warning"},
      {name: "Cash", pct: 5}
    ]
  };
  const ROWS = [
    {label: "Profile coherence", href: "#/coherence", finding: "Interest in publicized stocks pulls against a low-to-moderate risk level."},
    {label: "Objectives and horizon", href: "#/objectives", finding: "Median and optimistic scenarios preserve the capital over 3 to 5 years."},
    {label: "Risk", href: "#/risk", finding: "Bad-scenario loss is 2.4× what the profile tolerates."},
    {label: "Constraints", href: "#/constraints", finding: "One stock holds 22% of the portfolio; the limit is 10%."},
    {label: "Preferences", href: "#/preferences", finding: "Bitcoin is held, and one stock is a recent, highly publicized name."}
  ];

  /* ---------- small helpers ---------- */

  function isNum(value) {
    return typeof value === "number" && Number.isFinite(value);
  }

  function num(value, fallback) {
    if (value === null || value === undefined || value === "") return fallback;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function unwrap(value) {
    return value && typeof value === "object" && !Array.isArray(value) && "value" in value ? value.value : value;
  }

  function humanize(value) {
    const text = String(value || "").replace(/_/g, " ").trim();
    return text ? text.charAt(0).toUpperCase() + text.slice(1) : "";
  }

  function fmtAsOf(raw) {
    const text = String(raw || "");
    const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(text);
    if (!match) return text;
    const month = MONTHS[Number(match[2]) - 1];
    return month ? Number(match[3]) + " " + month + " " + match[1] : text;
  }

  function relativeTo(value, base) {
    if (!isNum(base) || base === 0 || !isNum(value)) return "";
    const change = value / base - 1;
    return (change >= 0 ? "+" : "−") + Math.round(Math.abs(change) * 100) + "% vs today";
  }

  /* ---------- report readers ---------- */

  function sections(report) {
    return report && Array.isArray(report.sections) ? report.sections : [];
  }

  function checks(report) {
    return report && Array.isArray(report.checks_current) ? report.checks_current : [];
  }

  function declaredValue(report, path) {
    for (const section of sections(report)) {
      const declared = section && section.facts ? section.facts.declared : null;
      if (!declared || !Object.prototype.hasOwnProperty.call(declared, path)) continue;
      const value = unwrap(declared[path]);
      if (value !== null && value !== undefined && value !== "") return value;
    }
    return null;
  }

  function sectionStatus(report, factor) {
    const section = sections(report).find(item => item && item.factor === factor);
    return section && typeof section.status === "string" ? section.status : "unknown";
  }

  function worstStatus(statuses, fallback) {
    if (!statuses.length) return fallback;
    return statuses.reduce((worst, status) =>
      (STATUS_RANK_UNKNOWN_LAST[status] || 0) > (STATUS_RANK_UNKNOWN_LAST[worst] || 0) ? status : worst);
  }

  function coherenceStatus(report) {
    const status = report && report.profile_coherence ? report.profile_coherence.status : null;
    if (status === "pass" || status === "info") return "pass";
    if (status === "warning") return "warning";
    return report ? "unknown" : "pass";
  }

  function verdictStatus(report) {
    const status = report ? VERDICT_STATUS[report.verdict] : null;
    return status || (report ? "unknown" : "violation");
  }

  function goalAmount(report) {
    const goals = report && report.goals && Array.isArray(report.goals.goal_probs) ? report.goals.goal_probs : [];
    const first = goals.find(goal => goal && Number.isFinite(Number(goal.amount)));
    return first ? Number(first.amount) : null;
  }

  function checksOf(report, factor, kind) {
    return checks(report).filter(check => check && check.factor === factor && (!kind || check.kind === kind));
  }

  function subjectStatus(report) {
    const byTicker = new Map();
    for (const check of checks(report)) {
      if (!check || typeof check.subject !== "string" || !check.subject) continue;
      const worst = worstStatus([check.status, byTicker.get(check.subject) || "unknown"], "unknown");
      byTicker.set(check.subject, worst);
    }
    return byTicker;
  }

  function findPersona(report) {
    const card = report && report.site ? report.site.profile_card : null;
    return card && typeof card.name === "string" && card.name ? card.name : "Robert";
  }

  /* ---------- totals ---------- */

  function horizonYears(ctx) {
    const cone = ctx.metrics && ctx.metrics.cone;
    const length = cone && Array.isArray(cone.p50) ? cone.p50.length - 1 : 0;
    return length > 0 ? Math.max(1, Math.round(length / MONTHS_PER_YEAR)) : DEFAULT_HORIZON_YEARS;
  }

  /* Fields fall back to the sample one at a time: a report without `site` shows the sample
     portfolio value beside the live median. That is acceptable for swapped reports dirs. */
  function totalsHtml(ctx) {
    const value = num(ctx.site && ctx.site.value_cad, SAMPLE.value);
    const p50 = num(ctx.metrics && ctx.metrics.p50_T, SAMPLE.p50);
    const years = horizonYears(ctx);
    const relative = relativeTo(p50, value);
    const delta = relative ? " (" + relative + ")" : (!ctx.report ? " (+19.2%)" : "");
    return '<div class="sum__figures">'
      + '<div class="sum__figure"><p class="sum__figure-label">Portfolio value (CAD)</p>'
      + '<p class="sum__figure-value num">' + App.esc(App.fmtCad(value, 2)) + '</p></div>'
      + '<div class="sum__figure"><p class="sum__figure-label sum__figure-label--blue">Median projection, year ' + years + '</p>'
      + '<p class="sum__figure-value sum__figure-value--l num">' + App.esc(App.fmtCad(p50, 2) + delta) + '</p></div>'
      + '</div>'
      + '<div class="sum__verdicts">'
      + '<div class="sum__verdict"><p class="sum__verdict-label">Is the profile coherent?</p>' + App.chip(coherenceStatus(ctx.report)) + '</div>'
      + '<div class="sum__verdict"><p class="sum__verdict-label">Does the portfolio fit the profile?</p>' + App.chip(verdictStatus(ctx.report)) + '</div>'
      + '</div>';
  }

  /* ---------- profile figures ---------- */

  function profileHtml(ctx) {
    const card = ctx.site && ctx.site.profile_card ? ctx.site.profile_card : null;
    const age = num(card && card.age, 59);
    const occupation = typeof (card && card.occupation) === "string" && card.occupation.trim()
      ? card.occupation.trim().charAt(0).toLowerCase() + card.occupation.trim().slice(1)
      : "healthcare professional";
    const primary = declaredValue(ctx.report, "objectives.primary");
    const horizon = num(unwrap(card && card.horizon_years), null);
    const risk = unwrap(card && card.risk_effective) || unwrap(card && card.risk_declared);
    const experience = unwrap(card && card.knowledge_level) || declaredValue(ctx.report, "knowledge.declared_level");
    const horizonText = isNum(horizon) && horizon > 0
      ? App.fmtCad(horizon, Number.isInteger(horizon) ? 0 : 1) + " years"
      : "3 to 5 years";
    const figures = [
      ["Age, occupation", App.fmtCad(age) + ", " + occupation],
      ["Objective", App.objectiveLabels[primary] || humanize(primary) || "Preserve capital, some growth"],
      ["Horizon", horizonText],
      ["Risk profile", App.riskLevels[risk] || humanize(risk) || "Low to moderate"],
      ["Experience", EXPERIENCE_LABELS[experience] || humanize(experience) || "Advanced"]
    ];
    return figures.map(item =>
      '<div class="sum__figure"><p class="sum__figure-label">' + App.esc(item[0]) + '</p>'
      + '<p class="sum__figure-value">' + App.esc(item[1]) + '</p></div>'
    ).join("");
  }

  /* ---------- coherence summary ---------- */

  /* Same internal-coherence count as the coherence screen: the three pillar cards are
     consistent, and every profile finding adds one more check to the total. */
  function coherenceFigure(report) {
    if (!report) return "3 of 4 checks consistent";
    const findings = report.profile_coherence && Array.isArray(report.profile_coherence.findings)
      ? report.profile_coherence.findings : [];
    return "3 of " + (3 + findings.length) + " checks consistent";
  }

  function objectivesFigure(report, metrics) {
    if (!report) return "Median 298,000 vs goal 250,000";
    const p50 = num(metrics && metrics.p50_T, null);
    const goal = goalAmount(report);
    if (!isNum(p50)) return "Median 298,000 vs goal 250,000";
    if (!isNum(goal)) return "Median " + App.fmtCad(p50);
    return "Median " + App.fmtCad(p50) + " vs goal " + App.fmtCad(goal);
  }

  function riskFigure(report, site, metrics) {
    if (!report) return "Loss 60,000 vs tolerance 25,000";
    const loss = riskLoss({site, metrics});
    if (!isNum(loss)) return "Loss 60,000 vs tolerance 25,000";
    const tolerance = riskTolerance(site);
    if (!isNum(tolerance) || tolerance === 0) return "Loss " + App.fmtCad(loss);
    return "Loss " + App.fmtCad(loss) + " vs tolerance " + App.fmtCad(tolerance);
  }

  function constraintsFigure(report) {
    if (!report) return "1 of 3 violated";
    const list = checksOf(report, "contraintes", "hard");
    if (!list.length) return "No hard constraint checked";
    const violated = list.filter(check => check.status === "violation").length;
    return violated + " of " + list.length + " violated";
  }

  function preferencesFigure(report) {
    if (!report) return "2 of 3 not met";
    const list = checksOf(report, "preferences", null);
    if (!list.length) return "No preference checked";
    const notMet = list.filter(check => check.status && check.status !== "pass").length;
    return notMet + " of " + list.length + " not met";
  }

  /* Findings stay factual: with a report present they are derived from its numbers and checks;
     without one, the design's English sentences are the fallback. */

  function coherenceFinding(report) {
    const findings = report.profile_coherence && Array.isArray(report.profile_coherence.findings)
      ? report.profile_coherence.findings : [];
    if (!findings.length) return "No contradiction between the profile answers.";
    return "The profile has " + findings.length + (findings.length === 1 ? " point" : " points") + " to clarify.";
  }

  function objectivesFinding(ctx) {
    const value = num(ctx.site && ctx.site.value_cad, null);
    const p5 = num(ctx.metrics && ctx.metrics.p5_T, null);
    const p50 = num(ctx.metrics && ctx.metrics.p50_T, null);
    const goal = goalAmount(ctx.report);
    const reference = isNum(goal) ? goal : value;
    const label = isNum(goal) ? "the goal" : "today's value";
    if (!isNum(reference) || !isNum(p50)) return ROWS[1].finding;
    if (isNum(p5) && p5 >= reference) return "The pessimistic and median scenarios both end at or above " + label + ".";
    if (p50 >= reference) return "The median scenario ends at or above " + label + "; the pessimistic one does not.";
    return "The median scenario ends below " + label + ".";
  }

  function riskTolerance(site) {
    const limit = site && site.limits ? num(site.limits.max_drawdown_pct, null) : null;
    const value = num(site && site.value_cad, null);
    if (!isNum(limit) || limit === 0 || !isNum(value)) return null;
    const share = Math.abs(limit) < 1 ? Math.abs(limit) : Math.abs(limit) / 100;
    return value * share;
  }

  function riskLoss(ctx) {
    const value = num(ctx.site && ctx.site.value_cad, null);
    const p5 = num(ctx.metrics && ctx.metrics.p5_T, null);
    return isNum(value) && isNum(p5) ? Math.max(0, value - p5) : null;
  }

  function riskFinding(ctx) {
    const loss = riskLoss(ctx);
    if (!isNum(loss)) return ROWS[2].finding;
    const tolerance = riskTolerance(ctx.site);
    if (!isNum(tolerance) || tolerance === 0) return "Bad-scenario loss is " + App.fmtCad(loss) + ".";
    return "Bad-scenario loss is " + App.fmtCad(loss / tolerance, 1) + "× what the profile tolerates.";
  }

  function constraintsFinding(ctx) {
    const weights = ctx.site && ctx.site.weights_pct && typeof ctx.site.weights_pct === "object" ? ctx.site.weights_pct : null;
    const values = weights ? Object.keys(weights).map(ticker => num(weights[ticker], 0)) : [];
    const largest = values.length ? Math.max.apply(null, values) : null;
    if (!isNum(largest) || largest <= 0) return ROWS[3].finding;
    const limit = ctx.site && ctx.site.limits ? num(ctx.site.limits.max_single_stock_weight, null) : null;
    if (!isNum(limit) || limit <= 0) return "The largest holding is " + App.fmtPct(largest) + " of the portfolio.";
    return "The largest holding is " + App.fmtPct(largest) + " of the portfolio; the limit is " + App.fmtPct(limit) + ".";
  }

  function preferencesFinding(report) {
    const list = checksOf(report, "preferences", null);
    if (!list.length) return "No preference check is recorded.";
    const notMet = list.filter(check => check.status && check.status !== "pass");
    if (!notMet.length) return "Every preference check is met.";
    const subjects = [];
    for (const check of notMet) {
      if (typeof check.subject === "string" && check.subject && subjects.indexOf(check.subject) < 0) subjects.push(check.subject);
    }
    if (!subjects.length) {
      return notMet.length + (notMet.length === 1 ? " preference check is" : " preference checks are") + " not met.";
    }
    return "Preference checks not met: " + subjects.slice(0, 3).join(", ") + (subjects.length > 3 ? ", …" : "") + ".";
  }

  function findingFor(ctx, index) {
    if (!ctx.report) return ROWS[index].finding;
    if (index === 0) return coherenceFinding(ctx.report);
    if (index === 1) return objectivesFinding(ctx);
    if (index === 2) return riskFinding(ctx);
    if (index === 3) return constraintsFinding(ctx);
    return preferencesFinding(ctx.report);
  }

  function execHtml(ctx) {
    const headline = ctx.report && ctx.report.explanation && typeof ctx.report.explanation.headline === "string"
      && ctx.report.explanation.headline.trim() ? ctx.report.explanation.headline.trim() : SAMPLE.summary;
    const report = ctx.report;
    const statuses = [
      coherenceStatus(report),
      report ? worstStatus([sectionStatus(report, "objectif"), sectionStatus(report, "horizon")], "unknown") : "pass",
      report ? sectionStatus(report, "risque") : "violation",
      report ? sectionStatus(report, "contraintes") : "violation",
      report ? sectionStatus(report, "preferences") : "warning"
    ];
    const figures = [
      coherenceFigure(report),
      objectivesFigure(report, ctx.metrics),
      riskFigure(report, ctx.site, ctx.metrics),
      constraintsFigure(report),
      preferencesFigure(report)
    ];
    const rows = ROWS.map((row, index) =>
      '<tr><td><a class="sum__link" href="' + App.esc(row.href) + '">' + App.esc(row.label) + '</a></td>'
      + '<td>' + App.chip(statuses[index]) + '</td>'
      + '<td>' + App.esc(figures[index]) + '</td>'
      + '<td>' + App.esc(findingFor(ctx, index)) + '</td></tr>'
    ).join("");
    return '<p class="sum__summary-text">' + App.esc(headline) + '</p>'
      + '<div class="tbl"><table class="table sum__table">'
      + '<colgroup><col style="width:250px"><col style="width:210px"><col style="width:290px"><col></colgroup>'
      + '<thead><tr><th>Check</th><th>Status</th><th>Key figure</th><th>Finding</th></tr></thead>'
      + '<tbody>' + rows + '</tbody></table></div>';
  }

  /* ---------- projection ---------- */

  function renderChart(screen, ctx) {
    const cone = ctx.metrics && ctx.metrics.cone;
    const limits = ctx.site && ctx.site.limits;
    const months = cone && Array.isArray(cone.p50) ? cone.p50.length - 1 : 0;
    const asOf = ctx.site && typeof ctx.site.as_of === "string" ? ctx.site.as_of : undefined;
    App.charts.fan(screen.querySelector('[data-chart="summary"]'), {
      cone,
      floor: limits ? limits.floor_at_horizon : undefined,
      years: months / MONTHS_PER_YEAR,
      asOf
    });
  }

  function tilesHtml(ctx) {
    const value = num(ctx.site && ctx.site.value_cad, SAMPLE.value);
    const p5 = num(ctx.metrics && ctx.metrics.p5_T, SAMPLE.p5);
    const p50 = num(ctx.metrics && ctx.metrics.p50_T, SAMPLE.p50);
    const p95 = num(ctx.metrics && ctx.metrics.p95_T, SAMPLE.p95);
    const tiles = [
      ["Pessimistic (P5)", p5, "sum__tile-value--down"],
      ["Median", p50, ""],
      ["Optimistic (P95)", p95, "sum__tile-value--up"]
    ];
    return tiles.map(tile =>
      '<div class="sum__tile"><p class="sum__tile-label">' + App.esc(tile[0]) + '</p>'
      + '<p class="sum__tile-value num' + (tile[2] ? " " + tile[2] : "") + '">' + CAD_SIGN + App.esc(App.fmtCad(tile[1])) + '</p>'
      + '<p class="sum__tile-note">' + App.esc(relativeTo(tile[1], value)) + '</p></div>'
    ).join("");
  }

  function chartNoteHtml(ctx) {
    const asOf = ctx.site && typeof ctx.site.as_of === "string" && ctx.site.as_of ? ctx.site.as_of : "2026-11-12";
    const years = horizonYears(ctx);
    return years + (years === 1 ? " year" : " years") + ", CAD, as of " + asOf;
  }

  /* ---------- persona select ---------- */

  function pickerOptions(personas) {
    const options = [];
    for (const person of personas) {
      if (!person.entries.length) {
        options.push({value: person.id + OPTION_SEPARATOR, text: person.name});
        continue;
      }
      for (const entry of person.entries) {
        const text = person.entries.length > 1 ? person.name + " · " + entry.label : person.name;
        options.push({value: person.id + OPTION_SEPARATOR + entry.file, text});
      }
    }
    return options;
  }

  function selectedValue() {
    const state = App.state;
    return (state.persona || "") + OPTION_SEPARATOR + (state.entryFile || "");
  }

  function showError(screen, message) {
    let box = screen.querySelector("[data-summary-error]");
    if (!message) {
      if (box) box.remove();
      return;
    }
    if (!box) {
      box = document.createElement("p");
      box.className = "t-small";
      box.setAttribute("data-summary-error", "");
      box.setAttribute("role", "alert");
      screen.querySelector(".sum__title-row").after(box);
    }
    box.textContent = message;
  }

  async function switchPersona(value, select, screen) {
    const at = value.indexOf(OPTION_SEPARATOR);
    const personaId = value.slice(0, at);
    const file = value.slice(at + 1) || null;
    select.disabled = true;
    try {
      await App.setPersona(personaId, file);
      showError(screen, "");
    } catch (error) {
      console.error("[summary] could not load persona " + personaId, error);
      showError(screen, "Could not load this persona: " + error.message);
      select.value = selectedValue();
    } finally {
      select.disabled = false;
    }
  }

  /* ---------- renderer ---------- */

  App.renderers.summary = function (section) {
    const screen = section.querySelector(".scr");
    if (!screen) return;
    const data = App.data || {};
    const report = data.report && typeof data.report === "object" ? data.report : null;
    const ctx = {
      report,
      site: report && report.site && typeof report.site === "object" ? report.site : null,
      metrics: report && report.metrics_current && typeof report.metrics_current === "object" ? report.metrics_current : null,
      universe: data.universe && typeof data.universe === "object" ? data.universe : null
    };
    App._summary.setMenuState(false);

    const select = screen.querySelector("[data-persona]");
    const options = pickerOptions(App.state.personas);
    select.innerHTML = options.length
      ? options.map(option => '<option value="' + App.esc(option.value) + '">Persona: ' + App.esc(option.text) + '</option>').join("")
      : '<option value="">Persona: ' + App.esc(findPersona(report)) + '</option>';
    if (options.some(option => option.value === selectedValue())) select.value = selectedValue();
    select.onchange = event => { switchPersona(event.target.value, event.target, screen); };

    screen.querySelector("[data-run-at]").textContent = fmtAsOf(
      ctx.site && typeof ctx.site.as_of === "string" && ctx.site.as_of ? ctx.site.as_of : SAMPLE.asOf
    );
    screen.querySelector("[data-totals]").innerHTML = totalsHtml(ctx);
    screen.querySelector("[data-profile]").innerHTML = profileHtml(ctx);
    screen.querySelector("[data-exec]").innerHTML = execHtml(ctx);
    renderChart(screen, ctx);
    screen.querySelector("[data-chart-note]").textContent = chartNoteHtml(ctx);
    screen.querySelector("[data-tiles]").innerHTML = tilesHtml(ctx);
    screen.querySelector("[data-keydata]").innerHTML = App._summary.keyDataHtml(ctx);
  };

  App._summary = {
    num, subjectStatus, CAD_SIGN, PALETTE, LIVE_PALETTE, SAMPLE
  };
})();
