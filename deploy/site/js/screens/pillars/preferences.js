(() => {
  "use strict";

  /* Preferences are soft: nothing here can read as a failure, so a hard check borrowed to measure the portfolio is
     shown as `warning` at most. Card titles and captions follow the design; the figures come from the report. */
  const ROUTE = "preferences";
  const FACTOR = "preferences";
  const FIRST_INTAKE_PATH = "preferences.prefers_dividends";
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

  /* ---------- parsed-rule list (per-rule approve/reject/correct) ---------- */

  /* Full raw profiles (live mode only), fetched once per persona and reused until a decision or a
     correction invalidates the entry. Fetch failures are kept separately so the card can say why,
     instead of showing "Loading…" forever. */
  const profileCache = new Map();
  const profileFetchPending = new Set();
  const profileFetchError = new Map();
  /* Plain-text notices from a correction response (D10: never our own wording), kept per persona
     and rule index so they still show after the next render pass. */
  const correctionNotices = new Map();

  function currentPreferencesSection() {
    return document.querySelector('section[data-route="' + ROUTE + '"]');
  }

  function rerenderPreferences(personaId) {
    if (App.state.persona !== personaId) return;
    const section = currentPreferencesSection();
    if (section) App.renderers[ROUTE](section);
  }

  function ensureProfileCached(personaId) {
    if (profileCache.has(personaId) || profileFetchPending.has(personaId)) return;
    profileFetchPending.add(personaId);
    App.source.profile(personaId).then(profile => {
      profileFetchPending.delete(personaId);
      profileFetchError.delete(personaId);
      profileCache.set(personaId, profile);
      rerenderPreferences(personaId);
    }).catch(error => {
      profileFetchPending.delete(personaId);
      profileFetchError.set(personaId, "Your notes could not be loaded"
        + (error && error.message ? ": " + error.message : "") + ".");
      rerenderPreferences(personaId);
    });
  }

  function parsedRulesOf(profile) {
    const node = profile && profile.values && profile.values.parsed_notes;
    return node && Array.isArray(node.value) ? node.value : [];
  }

  /* D11: an unapproved max_theme_weight rule with a number is already a provisional limit; the
     profile says so on `limits.max_theme_weight[theme]` (an object with `provisional: true`, vs. a
     plain number once a user approves a rule for that theme). */
  function isProvisionalRule(profile, rule) {
    if (!rule || rule.rule_kind !== "max_theme_weight" || rule.approved_by_user === true) return false;
    const params = rule.params || {};
    if (!isNumber(params.max_pct) || typeof params.theme !== "string" || !params.theme) return false;
    const themes = profile && profile.limits && profile.limits.max_theme_weight;
    const entry = themes && themes[params.theme];
    return Boolean(entry) && typeof entry === "object" && entry.provisional === true;
  }

  /* Plain-words summary of a parsed rule; only max_theme_weight has enough structure to phrase. */
  function ruleSummary(rule) {
    const kind = rule.rule_kind;
    const params = rule.params || {};
    if (kind === "max_theme_weight") {
      const theme = typeof params.theme === "string" && params.theme ? params.theme : "this theme";
      return isNumber(params.max_pct) ? "Cap " + theme + " at " + params.max_pct + "%" : "Cap " + theme + " (no number yet)";
    }
    return kind || "other";
  }

  function correctionFormHtml(index, notice) {
    if (App.source.mode !== "live") return "";
    return '<form class="rule-row__correct" data-correct-index="' + index + '">'
      + '<textarea name="text" maxlength="500" rows="1" aria-label="Correct this rule"></textarea>'
      + '<button class="btn btn--outline-dark" type="submit">Correct</button>'
      + "</form>"
      + (notice ? '<p class="t-small muted rule-row__notice">' + App.esc(notice) + "</p>" : "");
  }

  function parsedRuleHtml(profile, rule, index, notice) {
    const approved = rule.approved_by_user === true;
    const provisional = isProvisionalRule(profile, rule);
    const stateTag = '<span class="tag' + (approved ? " tag--pass" : "") + '">'
      + (approved ? "Approved" : "Awaiting approval") + "</span>";
    const provisionalTag = provisional ? '<span class="tag tag--info">Provisional limit</span>' : "";
    const actions = App.source.mode === "live"
      ? '<button class="btn btn--outline-dark" type="button" data-rule-action="approve" data-rule-index="' + index
        + '">Approve</button>'
        + '<button class="btn btn--outline-dark" type="button" data-rule-action="reject" data-rule-index="' + index
        + '">Reject</button>'
      : "";
    return '<li class="rule-row">'
      + '<p class="rule-row__quote">“' + App.esc(rule.quote || "") + '”</p>'
      + '<p class="t-small muted">' + App.esc(ruleSummary(rule)) + "</p>"
      + '<div class="rule-row__acts">' + stateTag + provisionalTag + actions + "</div>"
      + correctionFormHtml(index, notice)
      + "</li>";
  }

  function parsedNotesHtml(personaId) {
    if (App.source.mode !== "live" || !personaId) return "";
    const heading = '<h2 class="t-h3">Rules from your notes</h2>';
    if (profileFetchError.has(personaId)) {
      return '<div class="detail__col">' + heading
        + '<p class="t-small muted">' + App.esc(profileFetchError.get(personaId)) + "</p></div>";
    }
    if (!profileCache.has(personaId)) {
      ensureProfileCached(personaId);
      return '<div class="detail__col">' + heading + '<p class="t-small muted">Loading your notes…</p></div>';
    }
    const profile = profileCache.get(personaId);
    const rules = parsedRulesOf(profile);
    if (!rules.length) {
      return '<div class="detail__col">' + heading
        + '<p class="t-small muted">No rules were read from your notes.</p></div>';
    }
    const notices = correctionNotices.get(personaId);
    const items = rules.map((rule, index) => parsedRuleHtml(profile, rule, index, notices && notices.get(index)));
    return '<div class="detail__col">' + heading + '<ul class="rules-notes">' + items.join("") + "</ul></div>";
  }

  async function handleRuleDecision(action, index, personaId) {
    if (action !== "approve" && action !== "reject") return;
    const approved = action === "approve";
    const profile = profileCache.get(personaId);
    const rule = profile && parsedRulesOf(profile)[index];
    let maxPct;
    if (approved && rule && rule.rule_kind === "max_theme_weight" && !isNumber((rule.params || {}).max_pct)) {
      const typed = window.prompt('You wrote “' + (rule.quote || "")
        + '”. What maximum share of the portfolio (in %) do you want for this theme?');
      if (typed === null) return;
      maxPct = Number(String(typed).trim().replace(",", "."));
      if (!Number.isFinite(maxPct) || !(maxPct > 0 && maxPct <= 100)) {
        window.alert("The decision was not recorded: the maximum share must be a number greater than 0 and at most 100.");
        return;
      }
    }
    try {
      const updatedProfile = await App.source.decideRule(personaId, index, approved, maxPct);
      profileCache.set(personaId, updatedProfile);
      await App.rerunAnalysis();
    } catch (error) {
      window.alert("The decision was not recorded" + (error && error.message ? ": " + error.message : "") + ".");
    }
  }

  async function handleCorrection(index, form, personaId) {
    const textarea = form.querySelector('textarea[name="text"]');
    const text = textarea ? textarea.value.trim() : "";
    if (!text) return;
    try {
      const response = await App.source.correction(personaId, "parsed_rule", index, text);
      profileCache.delete(personaId);
      if (response && Array.isArray(response.messages) && response.messages.length) {
        let byIndex = correctionNotices.get(personaId);
        if (!byIndex) {
          byIndex = new Map();
          correctionNotices.set(personaId, byIndex);
        }
        byIndex.set(index, response.messages.join(" "));
      }
      if (response && response.report) App.setReport(response.report);
    } catch (error) {
      window.alert("The correction could not be recorded" + (error && error.message ? ": " + error.message : "") + ".");
    }
  }

  function detailHtml(section, report) {
    if (!section) return "";
    return '<section class="card detail" aria-label="Declared preferences and explanation">'
      + '<div class="detail__col">'
      + '<div class="detail__header">'
      + '<h2 class="t-h3">Declared preferences</h2>'
      + App.editButtonHtml("Edit", FIRST_INTAKE_PATH)
      + '</div>'
      + factsHtml(section.facts && section.facts.declared) + "</div>"
      + '<div class="detail__col"><h2 class="t-h3">Explanation</h2>' + segmentsHtml(section) + "</div>"
      + remindersHtml(report)
      + parsedNotesHtml(App.state.persona)
      + "</section>";
  }

  /* ---------- renderer ---------- */

  function bindOnce(section) {
    if (section.dataset.preferencesBound === "1") return;
    section.dataset.preferencesBound = "1";
    section.addEventListener("click", event => {
      const editBtn = event.target.closest("[data-edit-path]");
      if (editBtn) {
        const path = editBtn.dataset.editPath;
        App.go("#/conversation?path=" + encodeURIComponent(path));
        return;
      }
      const ruleBtn = event.target.closest("[data-rule-action]");
      if (ruleBtn) {
        handleRuleDecision(ruleBtn.dataset.ruleAction, Number(ruleBtn.dataset.ruleIndex), App.state.persona);
      }
    });
    section.addEventListener("submit", event => {
      const form = event.target.closest("[data-correct-index]");
      if (!form) return;
      event.preventDefault();
      handleCorrection(Number(form.dataset.correctIndex), form, App.state.persona);
    });
  }

  App.renderers[ROUTE] = function (section) {
    bindOnce(section);
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
