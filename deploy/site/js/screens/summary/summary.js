(() => {
  "use strict";

  const OPTION_SEPARATOR = "|";
  const NEW_PROFILE_VALUE = "__new__";
  const NEW_PROFILE_LABEL = "+ New profile";
  const NEW_PROFILE_LOCALE = "en-CA";
  const NEW_PROFILE_NAME_PROMPT = "Name this profile (optional). Leave it blank to skip.";
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
    summary: "This profile holds together: a short horizon, a capital-preservation goal and a low-to-moderate risk level point the same way. The portfolio does not fully match it. A 22% position in one high-volatility technology stock breaks the concentration constraint and pushes the bad-scenario loss well past what the profile tolerates.",
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

  /* ---------- P2 / SPEC §12, §17, §19: stress, unknowns and known limitations (report-level, ported from
     the retired index.html; English wording, same guarantees: information only, never a check, never the
     verdict, every number read from the report). */

  const UNKNOWN_WHY = {
    employer_sector_etf_part_unknown: "the ETF share is not broken down by sector, so the employer sector cap cannot be verified",
    sector_risk_broad_market_unknown: "the “broad market” sector groups ETFs whose composition is unknown",
    excluded_activity_no_tag_rows: "no sourced activity tag for this holding",
    excluded_activity_etf_no_lookthrough: "the ETF's internal composition is unknown (no look-through)",
    excluded_activity_etf_constituents_unknown: "the ETF's constituents are not tagged",
    fee_unknown: "fees are not available",
    theme_weight_unknown: "themes are not available"
  };

  function stressHtml(report) {
    const stress = report && report.stress;
    if (!stress) return "";
    const rows = (stress.scenarios || []).map(sc => {
      const shock = sc.shock_bp === null || sc.shock_bp === undefined ? "—"
        : (sc.shock_bp > 0 ? "+" : "") + sc.shock_bp;
      return "<tr><td>" + App.esc(sc.kind || sc.id || "") + '</td><td class="r">' + App.esc(String(shock))
        + '</td><td class="r"><b>' + App.esc(App.fmtPct(sc.impact)) + "</b></td></tr>";
    }).join("");
    const durations = (stress.bond_holdings || [])
      .map(h => App.esc(h.ticker) + ": " + App.esc(String(h.duration)) + " (" + App.esc(h.duration_source) + ")")
      .join(" · ");
    const sourceNote = stress.sourced ? "Durations are sourced."
      : "Durations are not sourced: values assumed by the team (ASSUMED).";
    return '<div class="sum__head"><h2 class="t-h3">Interest rate scenarios</h2>' + App.chip(stress.status) + "</div>"
      + '<p class="t-small muted">An illustration of the effect of a rate shock on bond ETFs, not a forecast. '
      + "Information only: this is not a check, and it changes neither the verdict nor the limits.</p>"
      + '<div class="tbl"><table class="table"><thead><tr><th>Scenario</th><th class="r">Shock (bp)</th>'
      + '<th class="r">Effect on value</th></tr></thead><tbody>' + rows + "</tbody></table></div>"
      + '<p class="t-small muted">' + App.esc(sourceNote) + (durations ? " Durations used: " + durations + "." : "") + "</p>"
      + App.assumptionsHtml(stress.assumptions, stress.sourced ? "scenario assumptions" : "unsourced: scenario assumptions");
  }

  function unknownsHtml(report) {
    const list = (report && Array.isArray(report.unknowns)) ? report.unknowns : [];
    const names = App.checkNames;
    const rows = list.map(u => {
      const why = UNKNOWN_WHY[u.message_key] || u.message_key || "";
      const subject = u.subject ? " (" + App.esc(u.subject) + ")" : "";
      return '<li class="unkrow">' + App.chip("unknown") + " <b>" + App.esc(u.id) + "</b> "
        + App.esc(names[u.id] || u.id) + ": " + App.esc(why) + subject + "</li>";
    }).join("");
    return '<h2 class="t-h3">What we cannot verify</h2>'
      + (list.length
        ? '<p class="t-small muted">These checks could not be decided for lack of data. They are listed '
          + "separately and never change the verdict.</p>" + '<ul class="unklist">' + rows + "</ul>"
        : '<p class="t-small muted">Everything could be verified.</p>');
  }

  function limitationsHtml(report) {
    const all = (report && Array.isArray(report.assumptions)) ? report.assumptions : [];
    const pick = keys => all.filter(a => keys.some(k => String(a).toLowerCase().includes(k)));
    const groups = [
      ["Short historical window and expected return", pick(["data window", "drift", "bootstrap", "the sample includes"]),
       "Block resampling reuses this one history. The expected return is a team assumption (ASSUMED), not a forecast."],
      ["Survivorship bias", pick(["survivorship"]), "The asset universe was chosen after the fact."],
      ["ETFs without look-through", pick(["look-through"]),
       "Without ETF composition, the sector, employer and activity checks on ETFs stay unknown."],
      ["Noisy behaviour", [],
       "Behaviour classification is noisy: confidence is capped and several events are required. "
       + "This is a warning, never a block."],
      ["Team limits, not a regulation", [],
       "The limits table values are team calibrations. The real equivalent, a Quebec suitability assessment "
       + "(AMF/CIRO), remains out of scope."]
    ];
    const items = groups.map(([title, lines, note]) => "<li><b>" + App.esc(title) + "</b>"
      + '<div class="t-small muted">' + App.esc(note) + "</div>"
      + (lines.length ? "<ul>" + lines.map(a => "<li>" + App.esc(a) + "</li>").join("") + "</ul>" : "")
      + "</li>").join("");
    return '<h2 class="t-h3">Known limitations</h2><ul class="limits-list">' + items + "</ul>";
  }

  /* ---------- small helpers ---------- */

  const {num} = App.format;

  function isNum(value) {
    return typeof value === "number" && Number.isFinite(value);
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
    return card && typeof card.name === "string" && card.name ? card.name : "Profile";
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
    const asOf = ctx.site && typeof ctx.site.as_of === "string" ? ctx.site.as_of : "";
    const years = horizonYears(ctx);
    return years + (years === 1 ? " year" : " years") + ", CAD" + (asOf ? ", as of " + asOf : "");
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

  /* Creates a brand-new, empty profile, then routes straight to the intake flow for it.
     A new profile has no portfolio yet; conversation.js handles that gap when the analysis
     is run. On failure the select is restored to the current persona and an inline error shows. */
  async function createNewProfile(select, screen) {
    select.disabled = true;
    try {
      const typed = window.prompt(NEW_PROFILE_NAME_PROMPT);
      if (typed === null) { select.value = selectedValue(); return; }
      const display_name = typed.trim();
      const body = display_name ? {locale: NEW_PROFILE_LOCALE, display_name} : {locale: NEW_PROFILE_LOCALE};
      const created = await App.source.createProfile(body);
      const profileId = created && created.meta && created.meta.profile_id;
      if (!profileId) throw new Error("the new profile had no id");
      const list = await App.source.listProfiles();
      App.state.personas = list.map(item => ({id: item.profile_id, name: item.name, entries: []}));
      App.state.persona = profileId;
      App.state.entryFile = null;
      App.state.report = null;
      App.state.universe = null;
      App.state.whatif = null;
      App.state.selection = [];
      showError(screen, "");
      App.go("#/conversation");
    } catch (error) {
      console.error("[summary] could not create a new profile", error);
      showError(screen, "Could not create a new profile: " + error.message);
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
    const pickable = App.source.mode === "live" ? options.concat([{value: NEW_PROFILE_VALUE, text: NEW_PROFILE_LABEL}]) : options;
    select.innerHTML = pickable.length
      ? pickable.map(option => '<option value="' + App.esc(option.value) + '">'
        + (option.value === NEW_PROFILE_VALUE ? App.esc(option.text) : "Persona: " + App.esc(option.text)) + '</option>').join("")
      : '<option value="">Persona: ' + App.esc(findPersona(report)) + '</option>';
    if (options.some(option => option.value === selectedValue())) select.value = selectedValue();
    select.onchange = event => {
      const value = event.target.value;
      if (value === NEW_PROFILE_VALUE) {
        createNewProfile(event.target, screen);
      } else {
        switchPersona(value, event.target, screen);
      }
    };

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
    screen.querySelector("[data-stress]").innerHTML = stressHtml(report);
    screen.querySelector("[data-unknowns]").innerHTML = unknownsHtml(report);
    screen.querySelector("[data-limitations]").innerHTML = limitationsHtml(report);
  };

  App._summary = {
    subjectStatus, CAD_SIGN, PALETTE, LIVE_PALETTE, SAMPLE
  };
})();
