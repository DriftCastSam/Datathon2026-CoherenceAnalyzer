(() => {
  "use strict";

  /* Preferences are soft: nothing here can read as a failure, so a hard check borrowed to measure the portfolio is
     shown as `warning` at most. Card titles and captions follow the design; the figures come from the report. */
  const ROUTE = "preferences";
  const FACTOR = "preferences";
  const CRYPTO_METER_SCALE = 0.3;
  const SHARE_METER_SCALE = 1;
  const MAX_HOLDINGS_SHOWN = 12;
  const SAMPLE = {
    status: "warning",
    intro: "Two of three preferences are not met. Preferences are soft, so this is a warning and not a failure.",
    rules: [
      {title: "Little interest in crypto assets", status: "warning", kind: "meter", value: 0.05, scale: CRYPTO_METER_SCALE,
       caption: "Bitcoin 5% of portfolio"},
      {title: "Established companies he understands", status: "warning", kind: "holds", total: 6, flagged: 1,
       caption: "1 of 6 holdings is a recent, publicized name"},
      {title: "Stability and company quality", status: "pass", kind: "meter", value: 0.6, scale: SHARE_METER_SCALE,
       caption: "Bonds and Canadian equities 60%"}
    ]
  };
  const FIELD_LABEL = {
    "values.environment.importance": "Environmental importance",
    "values.social.importance": "Social importance",
    "values.governance.importance": "Governance importance",
    "values.notes": "Your notes",
    "preferences.prefers_dividends": "Prefers dividends",
    "preferences.prefers_large_caps": "Prefers large caps",
    "preferences.home_bias_ok": "Home bias accepted",
    "preferences.interests": "Interests",
    "values.parsed_notes": "Rules from your notes"
  };
  const VALUE_LABEL = {
    low: "Low", low_moderate: "Low to moderate", moderate: "Moderate", moderate_high: "Moderate to high", high: "High",
    pre_retirement: "Pre-retirement", retirement: "Retirement", accumulation: "Accumulation",
    capital_preservation_with_growth: "Preserve capital with some growth", capital_preservation: "Preserve capital",
    growth: "Growth", income: "Income", aggressive_growth: "Aggressive growth",
    new_opportunities: "New opportunities", market_news: "Market news",
    understandable_business: "Understandable business", hype: "Hype"
  };
  const SOFT_STATUS = {violation: "warning"};

  const isNumber = value => typeof value === "number" && Number.isFinite(value);

  function number(value) {
    if (isNumber(value)) return value;
    if (typeof value === "string" && value.trim() !== "" && Number.isFinite(Number(value))) return Number(value);
    return null;
  }

  function softStatus(status) {
    return SOFT_STATUS[status] || (Object.prototype.hasOwnProperty.call(App.statusRank, status) ? status : "unknown");
  }

  function worstStatus(checks) {
    return App.worstStatus(checks.map(check => check.status));
  }

  /* The title chip never understates the screen: the worse of the section status and the three cards, soft-mapped. */
  function titleStatus(section, rules) {
    const statuses = rules.map(rule => softStatus(rule.status));
    if (section) statuses.push(softStatus(section.status));
    return softStatus(App.worstStatus(statuses));
  }

  function iconFor(status) {
    return status === "pass" ? "icon-ok" : "icon-warn-circle";
  }

  /* ---------- model ---------- */

  function sectionOf(report) {
    const sections = (report && report.sections) || [];
    return sections.find(section => section.factor === FACTOR) || null;
  }

  function checkById(report, id) {
    const checks = (report && report.checks_current) || [];
    return checks.find(check => check.id === id) || null;
  }

  function declared(section, path) {
    const values = (section && section.facts && section.facts.declared) || {};
    return Object.prototype.hasOwnProperty.call(values, path) ? values[path] : undefined;
  }

  function flagStatus(value) {
    if (value === true) return "pass";
    if (value === false) return "warning";
    return "unknown";
  }

  function heldTickers(report) {
    const drivers = (report.metrics_current && report.metrics_current.drivers) || [];
    return new Set(drivers.filter(driver => number(driver.weight) > 0).map(driver => driver.ticker));
  }

  function flaggedHoldings(report) {
    const held = heldTickers(report);
    const checks = (report && report.checks_current) || [];
    const flagged = new Set();
    checks.forEach(check => {
      if (check.subject && held.has(check.subject) && check.status !== "pass") flagged.add(check.subject);
    });
    return flagged.size;
  }

  function cryptoRule(report) {
    const check = checkById(report, "H2");
    const value = check ? number(check.value) : null;
    const status = check ? softStatus(worstStatus([check])) : "unknown";
    return {
      title: "Little interest in crypto assets",
      status,
      kind: "meter",
      value,
      scale: CRYPTO_METER_SCALE,
      caption: "Crypto " + App.fmtPct(value) + " of portfolio"
    };
  }

  function establishedRule(report, section) {
    const preference = declared(section, "preferences.prefers_large_caps");
    const total = heldTickers(report).size;
    const flagged = Math.min(flaggedHoldings(report), total);
    return {
      title: "Established companies he understands",
      status: flagStatus(preference),
      kind: "holds",
      total: Math.min(total, MAX_HOLDINGS_SHOWN),
      flagged,
      caption: flagged + " of " + total + " holdings " + (flagged === 1 ? "is" : "are") + " named in an open check"
    };
  }

  function stabilityRule(report, section) {
    const preference = declared(section, "preferences.prefers_dividends");
    const check = checkById(report, "H4");
    const value = check ? number(check.value) : null;
    return {
      title: "Stability and company quality",
      status: flagStatus(preference),
      kind: "meter",
      value,
      scale: SHARE_METER_SCALE,
      caption: "Fixed income " + App.fmtPct(value) + " of portfolio"
    };
  }

  function ruleModel(report, section) {
    if (!report || !report.site) return null;
    return [cryptoRule(report), establishedRule(report, section), stabilityRule(report, section)];
  }

  /* ---------- markup ---------- */

  function introFor(rules) {
    const total = rules.length;
    const unmet = rules.filter(rule => rule.status === "warning").length;
    const unclear = rules.filter(rule => rule.status === "unknown" || rule.status === "info").length;
    const soft = " Preferences are soft, so this is a warning and not a failure.";
    if (!unmet && !unclear) return "All " + total + " preferences are met.";
    const parts = [];
    if (unmet) parts.push(unmet + " of " + total + " preferences " + (unmet === 1 ? "is" : "are") + " not met");
    if (unclear) parts.push(unclear + " of " + total + " cannot be verified");
    return parts.join(" and ") + "." + soft;
  }

  function factsHtml(values) {
    return App.factsHtml(values, FIELD_LABEL, VALUE_LABEL, "No preference field is declared for this profile.");
  }

  function segmentsHtml(section) {
    return App.segmentsHtml(section);
  }

  function remindersHtml(report) {
    const checks = ((report && report.checks_current) || []).filter(check => check.factor === FACTOR);
    if (!checks.length) return "";
    return '<div class="detail__col"><h2 class="t-h3">From your notes</h2>'
      + checks.map(check => '<div class="reminder">' + App.chip(softStatus(check.status))
        + '<p class="reminder__quote">' + App.esc(check.subject || "") + "</p></div>").join("")
      + "</div>";
  }

  function detailHtml(section, report) {
    if (!section) return "";
    return '<section class="card detail" aria-label="Declared preferences and explanation">'
      + '<div class="detail__col"><h2 class="t-h3">Declared preferences</h2>'
      + factsHtml(section.facts && section.facts.declared) + "</div>"
      + '<div class="detail__col"><h2 class="t-h3">Explanation</h2>' + segmentsHtml(section) + "</div>"
      + remindersHtml(report)
      + "</section>";
  }

  /* ---------- renderer ---------- */

  App.renderers[ROUTE] = function (section) {
    const report = App.data && App.data.report;
    const sectionData = sectionOf(report);
    const live = ruleModel(report, sectionData);
    const rules = live || SAMPLE.rules;

    const chip = section.querySelector("[data-title-chip]");
    if (chip) chip.innerHTML = App.chip(live ? titleStatus(sectionData, rules) : SAMPLE.status);

    const intro = section.querySelector("[data-intro]");
    if (intro) intro.textContent = live ? introFor(rules) : SAMPLE.intro;

    const list = section.querySelector("[data-rules]");
    if (list) list.innerHTML = rules.map(rule => App.ruleCardHtml(rule, iconFor)).join("");

    const detail = section.querySelector("[data-detail]");
    if (detail) detail.innerHTML = live ? detailHtml(sectionData, report) : "";
  };
})();
