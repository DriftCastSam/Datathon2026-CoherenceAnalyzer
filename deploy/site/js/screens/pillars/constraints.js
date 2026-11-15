(() => {
  "use strict";

  /* The three hard-rule cards mirror the design one-for-one; each one reads the limit it names and the check that
     measures the portfolio against it, so the sample content only shows when the report is missing. */
  const ROUTE = "constraints";
  const FACTOR = "contraintes";
  const FIRST_INTAKE_PATH = "situation.emergency_fund";
  const METER_STEPS = [0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.75, 1];
  const METER_HEADROOM = 1.35;
  const HORIZON_WINDOW_YEARS = 1;
  const MAX_HOLDINGS_SHOWN = 12;
  const DERIVATIVE_TYPES = ["options", "margin", "leveraged_etf", "derivatives", "futures", "short"];
  const SAMPLE = {
    status: "violation",
    intro: "One of three hard rules is broken, which fails the pillar.",
    rules: [
      {title: "No single stock above 10%", status: "violation", kind: "meter", value: 0.22, limit: 0.1,
       caption: "Largest position 22%"},
      {title: "Funds available in 3 to 5 years", status: "pass", kind: "holds", total: 6, flagged: 0,
       caption: "6 of 6 holdings are liquid, listed assets"},
      {title: "No derivatives or margin", status: "pass", kind: "figure", figure: "0",
       caption: "derivative or margin positions"}
    ]
  };
  const FIELD_LABEL = {
    "situation.emergency_fund_months": "Emergency fund (months)",
    "situation.income_stability": "Income stability",
    "identity.employer_sector": "Employer sector",
    "preferences.asset_types_refused": "Refused asset types",
    "preferences.max_fee_mer_pct": "Maximum fee (MER)",
    "knowledge.score": "Knowledge score",
    "values.environment.exclusions": "Environmental exclusions",
    "values.social.exclusions": "Social exclusions",
    "values.governance.exclusions": "Governance exclusions",
    "values.parsed_notes": "Rules from your notes"
  };
  const VALUE_LABEL = {
    low: "Low", low_moderate: "Low to moderate", moderate: "Moderate", moderate_high: "Moderate to high", high: "High",
    pre_retirement: "Pre-retirement", retirement: "Retirement", accumulation: "Accumulation",
    crypto: "Crypto", options: "Options", margin: "Margin", leveraged_etf: "Leveraged ETF", derivatives: "Derivatives",
    stocks: "Stocks", etf: "ETF", bonds: "Bonds", gold: "Gold", mutual_funds: "Mutual funds",
    tobacco: "Tobacco", accounting_fraud_last_5y: "Accounting fraud (last 5 years)",
    understandable_business: "Understandable business", hype: "Hype"
  };

  const isNumber = value => typeof value === "number" && Number.isFinite(value);

  function number(value) {
    if (isNumber(value)) return value;
    if (typeof value === "string" && value.trim() !== "" && Number.isFinite(Number(value))) return Number(value);
    return null;
  }

  function worstStatus(checks) {
    return App.worstStatus(checks.map(check => check.status));
  }

  /* The title chip never understates the screen: it is the worse of the section status and the three cards. */
  function titleStatus(section, rules) {
    const statuses = rules.map(rule => rule.status);
    if (section) statuses.push(section.status);
    return App.worstStatus(statuses);
  }

  function iconFor(status) {
    if (status === "pass") return "icon-ok";
    if (status === "violation") return "icon-bad";
    return "icon-warn-circle";
  }

  function meterScale(limit, value) {
    const target = Math.max(limit || 0, value || 0) * METER_HEADROOM;
    return METER_STEPS.find(step => step >= target) || METER_STEPS[METER_STEPS.length - 1];
  }

  /* ---------- model ---------- */

  function sectionOf(report) {
    const sections = (report && report.sections) || [];
    return sections.find(section => section.factor === FACTOR) || null;
  }

  function factorChecks(report) {
    return ((report && report.checks_current) || []).filter(check => check.factor === FACTOR);
  }

  function singleStockRule(report) {
    const limits = (report.site && report.site.limits) || {};
    const checks = factorChecks(report).filter(check => check.id === "H3");
    const values = checks.map(check => number(check.value)).filter(value => value !== null);
    const limit = number(limits.max_single_stock_weight) ?? (checks.length ? number(checks[0].limit) : null);
    const value = values.length ? Math.max(...values) : null;
    const status = checks.length ? worstStatus(checks) : "unknown";
    return {
      title: isNumber(limit) ? "No single stock above " + App.fmtPct(limit) : "No single stock above the limit",
      status,
      kind: "meter",
      value,
      limit: isNumber(limit) ? limit : null,
      scale: meterScale(limit, value),
      caption: "Largest position " + App.fmtPct(value)
    };
  }

  function horizonRule(report) {
    const card = (report.site && report.site.profile_card) || {};
    const limits = (report.site && report.site.limits) || {};
    const metrics = report.metrics_current || {};
    const horizon = number(card.effective_horizon_years);
    const floor = number(limits.floor_at_horizon);
    const p5 = number(metrics.p5_T);
    const holdings = (metrics.drivers || []).filter(driver => number(driver.weight) > 0);
    const total = holdings.length;
    const outside = factorChecks(report).filter(check => check.id === "H1" && check.status === "violation").length;
    /* The card reports the H9 check when the report has one; the floor/p5 comparison is the
       fallback and a pessimistic value below the floor is a violation, never a warning. */
    const horizonChecks = factorChecks(report).filter(check => check.id === "H9");
    const status = horizonChecks.length ? worstStatus(horizonChecks)
      : (floor !== null && p5 !== null ? (p5 >= floor ? "pass" : "violation") : "unknown");
    const title = horizon !== null
      ? "Funds available in " + Math.floor(horizon) + " to " + (Math.ceil(horizon) + HORIZON_WINDOW_YEARS) + " years"
      : "Funds available when needed";
    return {
      title,
      status,
      kind: "holds",
      total: Math.min(total, MAX_HOLDINGS_SHOWN),
      flagged: Math.min(outside, total),
      caption: (total - Math.min(outside, total)) + " of " + total
        + (total === 1 ? " holding is" : " holdings are") + " outside the excluded asset types",
      note: floor !== null && p5 !== null
        ? "Floor at the horizon " + App.fmtCad(floor) + " CAD; pessimistic value " + App.fmtCad(p5) + " CAD"
        : ""
    };
  }

  function excludedTypesRule(report) {
    const limits = (report.site && report.site.limits) || {};
    const excluded = Array.isArray(limits.excluded_asset_types) ? limits.excluded_asset_types : [];
    const checks = factorChecks(report).filter(check => check.id === "H1");
    const held = checks.filter(check => check.status === "violation");
    const derivatives = excluded.filter(type => DERIVATIVE_TYPES.indexOf(type) >= 0);
    const status = checks.length ? worstStatus(checks) : "unknown";
    const title = held.length === 0 && derivatives.length ? "No derivatives or margin" : "No excluded asset types";
    return {
      title,
      status,
      kind: "figure",
      figure: App.fmtCad(held.length),
      caption: held.length === 1 ? "position outside the excluded asset types" : "positions outside the excluded asset types"
    };
  }

  function ruleModel(report) {
    if (!report || !report.site) return null;
    return [singleStockRule(report), horizonRule(report), excludedTypesRule(report)];
  }

  /* ---------- markup ---------- */

  /* Card bodies, facts and narrative come from the shared shell builders; only the intro copy
     and the status icons are screen-specific. */
  function introFor(rules) {
    const total = rules.length;
    const broken = rules.filter(rule => rule.status === "violation").length;
    const open = rules.filter(rule => rule.status !== "pass" && rule.status !== "violation").length;
    if (broken) {
      return broken + " of " + total + " hard rules " + (broken === 1 ? "is" : "are") + " broken, which fails the pillar.";
    }
    if (open) return open + " of " + total + " hard rules cannot be verified from the data, which flags the pillar.";
    return "All " + total + " hard rules are respected.";
  }

  function detailHtml(section) {
    if (!section) return "";
    return '<section class="card detail" aria-label="Declared constraints and explanation">'
      + '<div class="detail__col">'
      + '<div class="detail__header">'
      + '<h2 class="t-h3">Declared constraints</h2>'
      + App.editButtonHtml("Edit", FIRST_INTAKE_PATH)
      + '</div>'
      + App.factsHtml(section.facts && section.facts.declared, FIELD_LABEL, VALUE_LABEL,
        "No constraint field is declared for this profile.") + "</div>"
      + '<div class="detail__col"><h2 class="t-h3">Explanation</h2>' + App.segmentsHtml(section) + "</div>"
      + "</section>";
  }

  /* ---------- renderer ---------- */

  function bindOnce(section) {
    if (section.dataset.constraintsBound === "1") return;
    section.dataset.constraintsBound = "1";
    section.addEventListener("click", event => {
      const editBtn = event.target.closest("[data-edit-path]");
      if (editBtn) {
        const path = editBtn.dataset.editPath;
        App.go("#/conversation?path=" + encodeURIComponent(path));
      }
    });
  }

  App.renderers[ROUTE] = function (section) {
    bindOnce(section);
    const report = App.data && App.data.report;
    const live = ruleModel(report);
    /* The sample meter reads its scale from the same helper as the live one. */
    const rules = live || SAMPLE.rules.map(rule => rule.kind === "meter"
      ? Object.assign({}, rule, {scale: meterScale(rule.limit, rule.value)})
      : rule);
    const sectionData = sectionOf(report);

    const chip = section.querySelector("[data-title-chip]");
    if (chip) chip.innerHTML = App.chip(live ? titleStatus(sectionData, rules) : SAMPLE.status);

    const intro = section.querySelector("[data-intro]");
    if (intro) intro.textContent = live ? introFor(rules) : SAMPLE.intro;

    const list = section.querySelector("[data-rules]");
    if (list) list.innerHTML = rules.map(rule => App.ruleCardHtml(rule, iconFor)).join("");

    const detail = section.querySelector("[data-detail]");
    if (detail) detail.innerHTML = live ? detailHtml(sectionData) : "";
  };
})();
