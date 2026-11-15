(function () {
  "use strict";

  const MONTHS = ["January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"];

  const REACTION_LABEL = {
    sell_all: "Sell all", sell_some: "Sell some", hold: "Hold", buy_more: "Buy more"
  };
  const KNOWLEDGE_LABEL = {
    novice: "Novice", intermediate: "Intermediate", advanced: "Advanced", expert: "Expert"
  };
  const ASSET_LABEL = {
    stocks: "Stocks", etf: "ETFs", bonds: "Bonds", gold: "Gold", crypto: "Crypto",
    options: "Options", leveraged_etf: "Leveraged ETFs", margin: "Margin", mutual_funds: "Mutual funds"
  };
  const INTEREST_LABEL = {
    new_opportunities: "New opportunities", market_news: "Market news", ai: "AI",
    new_tech: "New tech", crypto: "Crypto", esg_funds: "ESG funds", dividend_income: "Dividend income"
  };
  const ENUM_GROUPS = [
    {pattern: /^risk\.(declared|capacity|effective|behavioural)$/, labels: App.riskLevels},
    {pattern: /^objectives\.(primary|secondary)$/, labels: App.objectiveLabels},
    {pattern: /^risk\.reaction_to_20pct_drop$/, labels: REACTION_LABEL},
    {pattern: /^knowledge\.declared_level$/, labels: KNOWLEDGE_LABEL},
    {pattern: /^preferences\.asset_types_(allowed|refused)$/, labels: ASSET_LABEL},
    {pattern: /^preferences\.interests$/, labels: INTEREST_LABEL}
  ];
  const FIELD_LABEL = {
    "risk.declared": "Risk level",
    "risk.capacity": "Risk capacity",
    "risk.effective": "Effective risk",
    "risk.behavioural": "Behavioural risk",
    "risk.max_acceptable_drawdown_pct": "Maximum acceptable drop",
    "risk.reaction_to_20pct_drop": "Reaction to a 20% drop",
    "objectives.primary": "Objective",
    "objectives.secondary": "Secondary objective",
    "objectives.horizon_years": "Horizon",
    "objectives.effective_horizon_years": "Effective horizon",
    "identity.life_stage": "Life stage",
    "identity.retirement_target_age": "Retirement age",
    "situation.emergency_fund_months": "Emergency fund",
    "situation.planned_withdrawals": "Planned withdrawals",
    "preferences.interests": "Stated interests",
    "preferences.asset_types_allowed": "Allowed types",
    "preferences.asset_types_refused": "Refused types",
    "preferences.prefers_dividends": "Prefers dividends",
    "preferences.prefers_large_caps": "Prefers large caps",
    "values.notes": "Notes",
    "knowledge.declared_level": "Experience",
    "knowledge.years_investing": "Years investing",
    "knowledge.quiz_score": "Quiz score",
    "knowledge.score": "Knowledge score"
  };
  const CHECK_ICON = {pass: "icon-check", warning: "icon-warn"};
  const CORE_PATHS = /^(objectives\.horizon_years|objectives\.effective_horizon_years|objectives\.primary|objectives\.goals)/;
  const BASE_SUMMARY = "Risk level, horizon and objective point the same way.";
  const CORE_SUMMARY = "Some profile answers point in different directions.";
  const FIGMA_SUMMARY = "Risk level, horizon and objective point the same way. One tension: an interest in publicized stocks.";
  const CONSISTENT_LABEL = "Consistent";
  const KIND_LABEL = {contradiction: "Contradiction", tension: "Tension"};
  const SAMPLE = {
    risk: "Low to moderate",
    horizon: "3 to 5 years",
    objective: "Preserve capital, some growth",
    knowledge: "Advanced",
    finding: {
      labels: ["Stated interests", "Risk level"],
      values: ["Publicized stocks", "Low to moderate"],
      kind: "tension",
      question: {
        "en-CA": "Your answers for \u201cstated interests\u201d and \u201crisk level\u201d seem to pull in "
          + "different directions. Can you clarify what you want?"
      }
    }
  };

  /* ---------- report lookups ---------- */

  function reportData() {
    return App.data && App.data.report ? App.data.report : null;
  }

  function profileCard() {
    const report = reportData();
    return report && report.site && report.site.profile_card ? report.site.profile_card : null;
  }

  function declaredFact(path) {
    const report = reportData();
    const sections = report && Array.isArray(report.sections) ? report.sections : [];
    for (let i = 0; i < sections.length; i += 1) {
      const facts = sections[i] && sections[i].facts;
      const declared = facts && facts.declared;
      if (declared && Object.prototype.hasOwnProperty.call(declared, path)) return declared[path];
    }
    return undefined;
  }

  function findingValue(coherence, path) {
    const findings = coherence && Array.isArray(coherence.findings) ? coherence.findings : [];
    for (let i = 0; i < findings.length; i += 1) {
      const fields = Array.isArray(findings[i].fields) ? findings[i].fields : [];
      const values = Array.isArray(findings[i].values) ? findings[i].values : [];
      const at = fields.indexOf(path);
      if (at >= 0) return values[at];
    }
    return undefined;
  }

  /* ---------- labels and values ---------- */

  function enumLabel(path, text) {
    for (let i = 0; i < ENUM_GROUPS.length; i += 1) {
      const group = ENUM_GROUPS[i];
      if (group.pattern.test(path) && group.labels[text]) return group.labels[text];
    }
    return text;
  }

  function humanize(path) {
    const raw = String(path || "").split(".").pop().replace(/\[\d+\]/g, "").replace(/_/g, " ");
    return raw ? raw.charAt(0).toUpperCase() + raw.slice(1) : "Field";
  }

  function fieldLabel(path) {
    const key = String(path || "");
    if (Object.prototype.hasOwnProperty.call(FIELD_LABEL, key)) return FIELD_LABEL[key];
    const goal = /^objectives\.goals\[(\d+)\]\.(\w+)$/.exec(key);
    if (goal) return "Goal " + (Number(goal[1]) + 1) + " " + goal[2];
    const importance = /^values\.(environment|social|governance)\.importance$/.exec(key);
    if (importance) return importance[1].charAt(0).toUpperCase() + importance[1].slice(1) + " importance";
    return humanize(key);
  }

  function monthLabel(text) {
    const match = /^(\d{4})-(\d{2})/.exec(String(text));
    if (!match) return String(text);
    const month = MONTHS[Number(match[2]) - 1];
    return month ? month + " " + match[1] : String(text);
  }

  function numberValue(key, value) {
    if (/(_years|years_investing)$/.test(key)) {
      return App.fmtCad(value) + (Number(value) === 1 ? " year" : " years");
    }
    if (/_pct$/.test(key)) return App.fmtPct(value);
    if (/_months$/.test(key)) return App.fmtCad(value, Number.isInteger(value) ? 0 : 1) + " months";
    if (/amount$/.test(key)) return "$" + App.fmtCad(value);
    if (key === "knowledge.quiz_score") return App.fmtPct(value);
    return App.fmtCad(value, Number.isInteger(value) ? 0 : 2);
  }

  function displayValue(path, value) {
    if (Array.isArray(value)) {
      return value.length ? value.map(item => displayValue(path, item)).join(", ") : "—";
    }
    if (value === null || value === undefined || value === "") return "—";
    if (typeof value === "boolean") return value ? "Yes" : "No";
    const key = String(path || "");
    if (typeof value === "number") return numberValue(key, value);
    const text = String(value);
    if (/\.date$/.test(key)) return monthLabel(text);
    return enumLabel(key, text);
  }

  function horizonBucket(years) {
    const count = Number(years);
    if (!Number.isFinite(count) || count <= 0) return "";
    if (count < 1) return "Under 1 year";
    if (count <= 2) return "1 to 2 years";
    if (count <= 5) return "3 to 5 years";
    if (count <= 10) return "6 to 10 years";
    return "More than 10 years";
  }

  function questionText(question) {
    if (!question) return "";
    if (typeof question === "string") return question;
    return question["en-CA"] || question["fr-CA"] || "";
  }

  function sentenceCase(text) {
    const value = String(text || "");
    return value.charAt(0).toUpperCase() + value.slice(1);
  }

  function lowerFirst(text) {
    const value = String(text || "");
    return value.charAt(0).toLowerCase() + value.slice(1);
  }

  /* ---------- findings ---------- */

  function normalizeFinding(finding) {
    const source = finding || {};
    const paths = Array.isArray(source.fields) ? source.fields.slice(0, 2) : [];
    const labels = Array.isArray(source.labels) ? source.labels : paths.map(fieldLabel);
    const raw = Array.isArray(source.quotes) && source.quotes.length
      ? source.quotes
      : (Array.isArray(source.values) ? source.values : []);
    const sides = [0, 1].map(index => {
      const path = paths[index] || "";
      return {
        label: labels[index] || (path ? fieldLabel(path) : ""),
        value: raw.length > index ? displayValue(path, raw[index]) : "—"
      };
    }).filter(side => side.label || side.value !== "—");
    const kind = source.kind === "contradiction" ? "contradiction" : "tension";
    return {
      sides: sides,
      paths: paths,
      kind: kind,
      reason: typeof source.reason === "string" ? source.reason : "",
      question: questionText(source.question),
      id: typeof source.id === "string" ? source.id : ""
    };
  }

  function chipMarkup(label, value, extraClass) {
    return '<div class="coh-chip' + (extraClass || "") + '">'
      + '<span class="coh-chip__label">' + App.esc(label) + '</span>'
      + '<span class="coh-chip__value">' + App.esc(value) + '</span></div>';
  }

  /* sides: the two field pills of one check. options: {icon, wide}.
     Long quotes (values.notes) wrap inside the wide variant instead of overflowing the card. */
  function sidesMarkup(sides, options) {
    const extraClass = options.wide ? " coh-chip--wide" : "";
    const parts = [];
    sides.forEach((side, index) => {
      if (index > 0) {
        parts.push('<span class="coh-link" aria-hidden="true"></span>');
        parts.push('<img class="coh-icon" src="assets/' + options.icon + '.svg" alt="" width="26" height="26">');
        parts.push('<span class="coh-link" aria-hidden="true"></span>');
      }
      parts.push(chipMarkup(side.label, side.value, extraClass));
    });
    return '<div class="coh-pair">' + parts.join("") + '</div>';
  }

  /* Per-check chip: same markup, palette and icons as App.chip, with the design's
     check wording ("Consistent" / "Tension" / "Contradiction") instead of a status label.
     A profile finding never reads as a violation: every finding uses the neutral warning palette. */
  function checkChip(status, label) {
    const key = status === "pass" ? "pass" : "warning";
    return '<span class="st st--' + key + '">'
      + '<img class="st__icon" src="assets/' + CHECK_ICON[key] + '.svg" alt="" width="16" height="16">'
      + '<span class="st__label">' + App.esc(label) + '</span></span>';
  }

  function consistentCard(left, right) {
    return '<div class="card coh-card">'
      + sidesMarkup([left, right], {icon: "icon-ok"})
      + checkChip("pass", CONSISTENT_LABEL)
      + '</div>';
  }

  /* Module-level so a message from a correction response survives the full-app
     re-render that App.setReport triggers (the response itself is not part of the report). */
  const correctionNotes = {};

  /* Passive correction affordance (P6.CORRECT pattern, ported from index.html): a text box
     and a plain verb, nothing asked. Only drawn in live mode, and only for findings that
     came from the backend (real findings carry an id; the SAMPLE fallback does not). */
  function correctionFormHtml(finding) {
    if (App.source.mode !== "live" || !finding.id) return "";
    return '<form class="coh-correct" data-correct-ref="' + App.esc(finding.id) + '">'
      + '<textarea name="text" maxlength="500" aria-label="Correct"></textarea>'
      + '<button class="btn btn--outline-dark" type="submit">Correct</button>'
      + '</form>';
  }

  function correctionNoteHtml(finding) {
    const note = finding.id ? correctionNotes[finding.id] : "";
    return note ? '<p class="t-small coh-note">' + App.esc(note) + '</p>' : "";
  }

  function findingCard(finding) {
    const extra = (finding.reason
      ? '<p class="t-small coh-reason">' + App.esc(finding.reason) + '</p>'
      : "")
      + (finding.question
        ? '<p class="t-small coh-question"><b>Question:</b> ' + App.esc(finding.question) + '</p>'
        : "");
    return '<div class="card coh-card">'
      + sidesMarkup(finding.sides, {icon: "icon-warn-circle", wide: true})
      + checkChip("warning", KIND_LABEL[finding.kind])
      + extra
      + correctionFormHtml(finding)
      + correctionNoteHtml(finding)
      + '</div>';
  }

  /* ---------- screen copy ---------- */

  function summaryText(findings) {
    if (!findings.length) return BASE_SUMMARY + " No contradictions were found in the profile.";
    const clauses = findings.map(finding => {
      const labels = finding.sides.map(side => lowerFirst(side.label)).filter(Boolean).join(" and ");
      return sentenceCase(finding.kind) + ": " + labels + ".";
    });
    const touchesCore = findings.some(finding => finding.paths.some(path => CORE_PATHS.test(path)));
    return (touchesCore ? CORE_SUMMARY : BASE_SUMMARY) + " " + clauses.join(" ");
  }

  function dotsMarkup(consistentCount, findingCount) {
    const dots = [];
    for (let i = 0; i < consistentCount; i += 1) dots.push('<span class="coh-dot"></span>');
    for (let i = 0; i < findingCount; i += 1) dots.push('<span class="coh-dot coh-dot--warning"></span>');
    return dots.join("");
  }

  /* ---------- correction submit ---------- */

  function submitCorrection(section, form) {
    const ref = form.dataset.correctRef;
    const text = String((form.elements.text && form.elements.text.value) || "").trim();
    if (!ref || !text) return;
    const button = form.querySelector('button[type="submit"]');
    if (button) button.disabled = true;
    App.source.correction(App.state.persona, "profile_finding", ref, text).then(response => {
      if (response && Array.isArray(response.messages) && response.messages.length) {
        correctionNotes[ref] = response.messages.join(" ");
      } else {
        delete correctionNotes[ref];
      }
      App.setReport(response.report);
    }).catch(error => {
      console.error("[coherence] correction not sent", error);
      correctionNotes[ref] = "Correction not sent: " + error.message;
      if (button) button.disabled = false;
      App.renderers["coherence"](section);
    });
  }

  function bindOnce(section) {
    if (section.dataset.cohBound === "1") return;
    section.dataset.cohBound = "1";
    section.addEventListener("submit", event => {
      const form = event.target.closest("[data-correct-ref]");
      if (!form) return;
      event.preventDefault();
      submitCorrection(section, form);
    });
  }

  /* ---------- renderer ---------- */

  App.renderers["coherence"] = function (section) {
    bindOnce(section);
    const report = reportData();
    const coherence = report && report.profile_coherence ? report.profile_coherence : null;
    const card = profileCard();

    const riskLevel = App.riskLevels[card && card.risk_declared]
      || App.riskLevels[findingValue(coherence, "risk.declared")]
      || SAMPLE.risk;
    const horizon = horizonBucket(card && card.horizon_years) || SAMPLE.horizon;
    const objectiveRaw = declaredFact("objectives.primary") || findingValue(coherence, "objectives.primary");
    const objective = App.objectiveLabels[objectiveRaw] || (typeof objectiveRaw === "string" && objectiveRaw
      ? objectiveRaw
      : SAMPLE.objective);
    const knowledge = KNOWLEDGE_LABEL[findingValue(coherence, "knowledge.declared_level")] || SAMPLE.knowledge;

    const rawFindings = coherence && Array.isArray(coherence.findings) ? coherence.findings : null;
    const findings = rawFindings === null
      ? [normalizeFinding(SAMPLE.finding)]
      : rawFindings.map(normalizeFinding);

    const status = coherence && coherence.status;
    const verdict = status === "warning" || status === "violation" ? "warning" : "pass";
    const consistentCount = 3;
    const total = consistentCount + findings.length;

    section.querySelector("[data-coh-verdict]").innerHTML = App.chip(verdict);
    section.querySelector("[data-coh-summary]").textContent = report ? summaryText(findings) : FIGMA_SUMMARY;
    section.querySelector("[data-coh-score]").textContent = consistentCount + " of " + total;
    section.querySelector("[data-coh-dots]").innerHTML = dotsMarkup(consistentCount, findings.length);
    section.querySelector("[data-coh-checks]").innerHTML = [
      consistentCard({label: "Risk level", value: riskLevel}, {label: "Horizon", value: horizon}),
      consistentCard({label: "Risk level", value: riskLevel}, {label: "Objective", value: objective}),
      consistentCard({label: "Experience", value: knowledge}, {label: "Financial knowledge", value: knowledge})
    ].join("") + findings.map(findingCard).join("");
  };
})();
