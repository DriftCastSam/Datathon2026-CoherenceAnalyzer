(() => {
  "use strict";

  /* Typed answer options per intake path. Values match coherence_analyzer/schemas/profile.v2.json
     and the fixture profiles. Question texts come from the API (fixed en-CA templates); this
     module only adds the right-panel label, the input kind and the suggestion chips. */
  const KIND = {
    ENUM: "enum",
    NUMBER: "number",
    AMOUNT: "amount",
    BOOLEAN: "boolean",
    LIST: "list",
    TEXT: "text"
  };

  const KEEP_VALUE = Symbol("keep-current");
  const KEEP_CHIP = {label: "Keep current", value: KEEP_VALUE};
  const NONE_CHIP = {label: "None", value: []};
  const YES_NO_CHIPS = [{label: "Yes", value: true}, {label: "No", value: false}];

  const RISK_LEVEL_CHIPS = [
    {label: "Low", value: "low"},
    {label: "Low to moderate", value: "low_moderate"},
    {label: "Moderate", value: "moderate"},
    {label: "Moderate to high", value: "moderate_high"},
    {label: "High", value: "high"}
  ];

  const OBJECTIVE_CHIPS = [
    {label: "Preserve capital", value: "capital_preservation"},
    {label: "Preserve capital, some growth", value: "capital_preservation_with_growth"},
    {label: "Preserve capital, income", value: "capital_preservation_with_income"},
    {label: "Income", value: "income"},
    {label: "Balanced growth", value: "balanced_growth"},
    {label: "Growth", value: "growth"},
    {label: "Aggressive growth", value: "aggressive_growth"}
  ];

  const REACTION_CHIPS = [
    {label: "Sell everything", value: "sell_all"},
    {label: "Sell some", value: "sell_some"},
    {label: "Do nothing", value: "hold"},
    {label: "Buy more", value: "buy_more"}
  ];

  /* Sector names as used by the holdings data (GICS sectors). */
  const SECTOR_CHIPS = [
    "Information Technology", "Health Care", "Financials", "Industrials", "Energy",
    "Consumer Discretionary", "Consumer Staples", "Materials", "Utilities", "Communication Services"
  ].map(sector => ({label: sector, value: sector}));

  /* Drawdown is stored as a non-positive percentage (schema maximum 0). */
  const DRAWDOWN_CHIPS = [-5, -10, -20, -30].map(value => ({label: value + "%", value}));
  const HORIZON_CHIPS = [1, 3, 5, 10, 20].map(value => ({label: value + (value === 1 ? " year" : " years"), value}));

  const MAX_AGE = 120;
  const MAX_RETIREMENT_AGE = 120;
  const MAX_HORIZON_YEARS = 100;
  const MAX_PERCENT = 100;

  const EMPLOYMENT_TYPE_CHIPS = [
    {label: "Salaried", value: "salaried"},
    {label: "Self-employed", value: "self_employed"},
    {label: "Contract", value: "contract"},
    {label: "Commission", value: "commission"},
    {label: "Unemployed", value: "unemployed"},
    {label: "Retired", value: "retired"}
  ];

  const KNOWLEDGE_LEVEL_CHIPS = [
    {label: "Novice", value: "novice"},
    {label: "Intermediate", value: "intermediate"},
    {label: "Advanced", value: "advanced"},
    {label: "Expert", value: "expert"}
  ];

  const SPEC = {
    "identity.age": {
      label: "Age", kind: KIND.NUMBER, unit: "years", integer: true, min: 0,
      max: MAX_AGE, placeholder: "e.g. 45", chips: []
    },
    "identity.employment_type": {
      label: "Employment type", kind: KIND.ENUM, chips: EMPLOYMENT_TYPE_CHIPS
    },
    "knowledge.declared_level": {
      label: "Investment knowledge", kind: KIND.ENUM, chips: KNOWLEDGE_LEVEL_CHIPS
    },
    "objectives.primary": {
      label: "Objective", kind: KIND.ENUM, chips: OBJECTIVE_CHIPS
    },
    "objectives.goals": {
      label: "Dated goals", kind: KIND.LIST, readOnlyList: true, chips: [KEEP_CHIP, NONE_CHIP]
    },
    "objectives.horizon_years": {
      label: "Time horizon", kind: KIND.NUMBER, unit: "years", min: 0, max: MAX_HORIZON_YEARS,
      placeholder: "e.g. 5", chips: HORIZON_CHIPS
    },
    "identity.retirement_target_age": {
      label: "Retirement age", kind: KIND.NUMBER, unit: "age", integer: true, min: 0,
      max: MAX_RETIREMENT_AGE, placeholder: "e.g. 65", chips: []
    },
    "situation.planned_withdrawals": {
      label: "Planned withdrawals", kind: KIND.LIST, readOnlyList: true, chips: [KEEP_CHIP, NONE_CHIP]
    },
    "risk.declared": {
      label: "Risk profile", kind: KIND.ENUM, chips: RISK_LEVEL_CHIPS
    },
    "risk.max_acceptable_drawdown_pct": {
      label: "Largest acceptable drop", kind: KIND.NUMBER, unit: "drawdown", max: 0,
      negateOnInput: true, placeholder: "e.g. -12", chips: DRAWDOWN_CHIPS
    },
    "risk.reaction_to_20pct_drop": {
      label: "Reaction to a 20% drop", kind: KIND.ENUM, chips: REACTION_CHIPS
    },
    "situation.emergency_fund": {
      label: "Emergency fund", kind: KIND.AMOUNT, min: 0, placeholder: "e.g. 30,000", chips: []
    },
    "situation.monthly_expenses": {
      label: "Monthly expenses", kind: KIND.AMOUNT, exclusiveMin: 0, placeholder: "e.g. 6,500", chips: []
    },
    "identity.employer_sector": {
      label: "Employer sector", kind: KIND.TEXT, placeholder: "e.g. Health Care", chips: SECTOR_CHIPS
    },
    "preferences.asset_types_refused": {
      label: "Refused product types", kind: KIND.LIST, placeholder: "e.g. crypto, options",
      chips: [KEEP_CHIP, NONE_CHIP]
    },
    "preferences.prefers_dividends": {
      label: "Prefers dividends", kind: KIND.BOOLEAN, chips: YES_NO_CHIPS
    },
    "preferences.prefers_large_caps": {
      label: "Prefers large caps", kind: KIND.BOOLEAN, chips: YES_NO_CHIPS
    },
    "preferences.home_bias_ok": {
      label: "Canadian overweight is fine", kind: KIND.BOOLEAN, chips: YES_NO_CHIPS
    }
  };

  /* A path the API asks about that SPEC does not list: the share of a parsed theme rule, a
     percentage in (0, 100]. */
  const THEME_SHARE_SPEC = {
    label: "Theme share", kind: KIND.NUMBER, unit: "percent", exclusiveMin: 0, max: MAX_PERCENT,
    placeholder: "e.g. 20", chips: []
  };
  const THEME_SHARE_PATH = /\.params\.max_pct$/;

  function specFor(path) {
    if (SPEC[path]) return SPEC[path];
    return THEME_SHARE_PATH.test(path) ? THEME_SHARE_SPEC : {label: path, kind: KIND.TEXT, chips: []};
  }

  App.intakeSpec = {KIND, KEEP_VALUE, specFor};
})();
