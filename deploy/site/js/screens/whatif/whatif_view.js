(function () {
  "use strict";

  /* Pure text and row builders for the what-if screen: every figure comes from the base
     portfolio and one scenario, and every sentence describes numbers without advising. */
  const WORST_FIRST_RANK = {violation: 0, warning: 1, unknown: 2, info: 3, pass: 4};
  const VERDICT_STATUS = {coherent: "pass", coherent_with_warnings: "warning", incoherent: "violation"};
  const PILLARS = [
    {id: "objectives", label: "Objectives and horizon", factors: ["objectif", "horizon"]},
    {id: "risk", label: "Risk", factors: ["risque"]},
    {id: "constraints", label: "Constraints", factors: ["contraintes"]},
    {id: "preferences", label: "Preferences", factors: ["preferences"]}
  ];
  const NEUTRAL_FINDING = "Precomputed finding unavailable for this scenario.";
  const NO_CHANGE_TEXT = "no change vs current portfolio";
  const MINUS = "−";
  const DASH = "—";
  const SEPARATOR = " · ";
  const MONTHS_PER_YEAR = 12;
  const DEFAULT_YEARS = 5;
  const HORIZON_BUCKETS = [[1, "Under 1 year"], [2, "1 to 2 years"], [5, "3 to 5 years"], [10, "6 to 10 years"]];
  const LONGEST_HORIZON = "More than 10 years";
  const DISCLAIMER = " This is an analysis of the asset you chose, not a suggestion to buy or avoid it.";

  function hasNumber(value) {
    return value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value));
  }

  function allNumbers(values) {
    return values.every(hasNumber);
  }

  function minus(value) {
    return String(value).replace(/^-/, MINUS);
  }

  function money(value) {
    return hasNumber(value) ? minus("$" + App.fmtCad(value, 0)) : DASH;
  }

  function deltaText(before, after) {
    if (!allNumbers([before, after])) return DASH;
    const diff = Number(after) - Number(before);
    if (Math.round(Math.abs(diff)) === 0) return NO_CHANGE_TEXT;
    return (diff < 0 ? MINUS : "+") + "$" + App.fmtCad(Math.abs(diff), 0) + " vs current portfolio";
  }

  function verdictStatus(verdict) {
    return VERDICT_STATUS[verdict] || "unknown";
  }

  function keyOf(target, funding) {
    return Math.trunc(Number(target)) + "|" + String(funding);
  }

  const CASH_FUNDING = "cash";
  const PRESET_TOLERANCE_CAD = 1;

  function round2(value) {
    return Math.round(Number(value) * 100) / 100;
  }

  /* A percent-unit value (0.5 means 0.5%), unlike App.fmtPct which reads values below 1 as fractions. */
  function pctText(pct) {
    return hasNumber(pct) ? App.fmtCad(round2(pct), Number.isInteger(round2(pct)) ? 0 : 2) + "%" : DASH;
  }

  function heldPct(asset) {
    return asset && hasNumber(asset.held_pct) ? Number(asset.held_pct) : 0;
  }

  /* The amount and the % are both relative to the current portfolio value, for every funding source. */
  function amountFromPct(pct, portfolioValue) {
    return allNumbers([pct, portfolioValue]) ? round2(Number(portfolioValue) * Number(pct) / 100) : null;
  }

  function pctFromAmount(amountCad, portfolioValue) {
    return allNumbers([amountCad, portfolioValue]) && Number(portfolioValue) > 0
      ? round2(Number(amountCad) / Number(portfolioValue) * 100) : null;
  }

  /* CAD the asset gets in a precomputed target-weight scenario (only the part above what is already held). */
  function presetAmount(target, portfolioValue, held) {
    return amountFromPct(Math.max(Number(target) - Number(held || 0), 0), portfolioValue);
  }

  /* The precomputed target whose amount equals the typed one (within $1), else null. Never for new cash. */
  function presetFor(amountCad, held, portfolioValue, targets, funding) {
    if (funding === CASH_FUNDING || !hasNumber(amountCad)) return null;
    const hit = (targets || []).find(target => {
      const amount = presetAmount(target, portfolioValue, held);
      return amount !== null && amount > 0 && Math.abs(amount - Number(amountCad)) < PRESET_TOLERANCE_CAD;
    });
    return hit === undefined ? null : Number(hit);
  }

  /* Largest CAD the funding can release for this asset, or null when there is no ceiling (new cash). */
  function capacityCad(funding, held, portfolioValue, weights) {
    if (funding === CASH_FUNDING || !hasNumber(portfolioValue)) return null;
    if (String(funding).startsWith("specific:")) {
      const source = String(funding).slice("specific:".length);
      return amountFromPct((weights || {})[source] || 0, portfolioValue);
    }
    return amountFromPct(100 - Number(held || 0), portfolioValue);
  }

  /* "" when the amount can be computed, else the reason shown under the inputs. */
  function amountProblem(amountCad, capacity) {
    if (!hasNumber(amountCad) || Number(amountCad) <= 0) return "Enter an amount greater than $0.";
    if (capacity !== null && Number(amountCad) > capacity + PRESET_TOLERANCE_CAD) {
      return "This funding source holds $" + App.fmtCad(capacity, 2) + "; enter that amount or less.";
    }
    return "";
  }

  function amountLabel(target, portfolioValue, held) {
    const amount = presetAmount(target, portfolioValue, held);
    if (amount === null) return pctText(target);
    return "$" + App.fmtCad(amount, 2) + SEPARATOR + pctText(pctFromAmount(amount, portfolioValue)) + " of portfolio";
  }

  function assetLabel(asset) {
    const name = asset && asset.name ? String(asset.name) : "";
    const ticker = asset && asset.ticker ? String(asset.ticker) : "";
    return name && ticker ? name + " [" + ticker + "]" : name || ticker;
  }

  function riskWord(key) {
    return App.riskLevels[key] ? App.riskLevels[key].toLowerCase() : "";
  }

  function horizonBucket(years) {
    const count = Number(years);
    if (!Number.isFinite(count) || count <= 0) return "";
    const bucket = HORIZON_BUCKETS.find(entry => count <= entry[0]);
    return bucket ? bucket[1] : LONGEST_HORIZON;
  }

  function profileCard(report) {
    return report && report.site ? report.site.profile_card : null;
  }

  function profileLine(report) {
    const card = profileCard(report);
    if (!card) return "";
    const risk = riskWord(card.risk_effective || card.risk_declared);
    return [card.name ? String(card.name) : "", risk ? risk + " risk" : "", horizonBucket(card.horizon_years)]
      .filter(Boolean).join(SEPARATOR);
  }

  function yearsOf(cone) {
    const points = cone && Array.isArray(cone.p50) ? cone.p50.length : 0;
    return points > 1 ? Math.max(1, Math.round(points / MONTHS_PER_YEAR)) : DEFAULT_YEARS;
  }

  function asOfLine(base) {
    const date = base.as_of ? String(base.as_of).slice(0, 10) : DASH;
    return yearsOf(base.cone) + " years, CAD, as of " + date;
  }

  function pillarStatus(statuses, pillar) {
    const found = pillar.factors.map(factor => (statuses || {})[factor]).filter(Boolean);
    if (!found.length) return "unknown";
    return found.reduce((worst, status) => (WORST_FIRST_RANK[status] < WORST_FIRST_RANK[worst] ? status : worst), found[0]);
  }

  function statusWord(status) {
    return App.statusLabel[status] || "Unknown";
  }

  function objectivesFinding(ctx) {
    const [before, after] = [ctx.baseMetrics, ctx.metrics];
    if (!allNumbers([before.p50_T, after.p50_T, before.p95_T, after.p95_T])) return NEUTRAL_FINDING;
    return "The median value at year " + ctx.years + " moves from " + App.fmtCad(before.p50_T) + " to "
      + App.fmtCad(after.p50_T) + " and the optimistic value from " + App.fmtCad(before.p95_T) + " to "
      + App.fmtCad(after.p95_T) + ".";
  }

  function riskFinding(ctx) {
    const [before, after] = [ctx.baseMetrics, ctx.metrics];
    if (!allNumbers([before.vol, after.vol, ctx.baseLoss, ctx.scenLoss])) return NEUTRAL_FINDING;
    return "Annual volatility moves from " + App.fmtPct(before.vol) + " to " + App.fmtPct(after.vol)
      + " and the bad-scenario loss from " + App.fmtCad(ctx.baseLoss) + " to " + App.fmtCad(ctx.scenLoss) + ".";
  }

  function constraintsFinding(ctx) {
    const [was, now] = [ctx.baseShares, ctx.shares];
    if (!allNumbers([was.single_stocks, now.single_stocks, was.bonds, now.bonds])) return NEUTRAL_FINDING;
    return "Single stocks move from " + App.fmtPct(was.single_stocks) + " to " + App.fmtPct(now.single_stocks)
      + " of the portfolio and bonds from " + App.fmtPct(was.bonds) + " to " + App.fmtPct(now.bonds) + ".";
  }

  function preferencesFinding(ctx) {
    if (ctx.today === ctx.withAsset) {
      return "The preference checks stay at the same status (" + statusWord(ctx.today) + ") with this asset.";
    }
    return "The preference checks move from " + statusWord(ctx.today) + " to " + statusWord(ctx.withAsset)
      + " with this asset.";
  }

  const PILLAR_FINDING = {
    objectives: objectivesFinding, risk: riskFinding, constraints: constraintsFinding, preferences: preferencesFinding
  };

  function buildPillars(ctx) {
    const afterStatuses = {};
    (ctx.scenario.sections || []).forEach(item => {
      if (item && item.factor) afterStatuses[item.factor] = item.after;
    });
    return PILLARS.map(pillar => {
      const today = pillarStatus(ctx.base.sections, pillar);
      const withAsset = pillarStatus(afterStatuses, pillar);
      return {label: pillar.label, today, withAsset, finding: PILLAR_FINDING[pillar.id]({...ctx, today, withAsset})};
    });
  }

  function findingText(ctx, pillars) {
    const [before, after] = [ctx.baseMetrics, ctx.metrics];
    if (!allNumbers([before.p50_T, after.p50_T, before.p5_T, after.p5_T, before.vol, after.vol,
      ctx.baseLoss, ctx.scenLoss, ctx.amount])) return NEUTRAL_FINDING;
    const worse = pillars.filter(pillar => WORST_FIRST_RANK[pillar.withAsset] < WORST_FIRST_RANK[pillar.today]).length;
    const pillarSentence = worse === 0
      ? "No pillar is worse with this asset."
      : worse + " of the four pillars " + (worse === 1 ? "is" : "are") + " worse with this asset.";
    const lead = ctx.funding === CASH_FUNDING
      ? "With a new cash contribution of $" + App.fmtCad(ctx.amount, 2) + " (" + pctText(ctx.pct)
        + " of the current portfolio value)"
      : "At $" + App.fmtCad(ctx.amount, 2) + " (" + pctText(ctx.pct) + " of the portfolio) funded by “"
        + ctx.fundingLabel + "”";
    return lead + ", the median value at year " + ctx.years + " moves from " + App.fmtCad(before.p50_T)
      + " to " + App.fmtCad(after.p50_T) + " and the pessimistic value from " + App.fmtCad(before.p5_T) + " to "
      + App.fmtCad(after.p5_T) + ". Annual volatility moves from " + App.fmtPct(before.vol) + " to "
      + App.fmtPct(after.vol) + ". The estimated bad-scenario loss moves from " + App.fmtCad(ctx.baseLoss)
      + " to " + App.fmtCad(ctx.scenLoss) + ". " + pillarSentence + DISCLAIMER;
  }

  function isLower(before, after) {
    return allNumbers([before, after]) && Number(after) < Number(before);
  }

  function isHigher(before, after) {
    return allNumbers([before, after]) && Number(after) > Number(before);
  }

  function buildRows(ctx) {
    const [was, now] = [ctx.baseMetrics, ctx.metrics];
    const year = "year " + ctx.years;
    return [
      {label: "Median value, " + year, today: App.fmtCad(was.p50_T), withAsset: App.fmtCad(now.p50_T)},
      {label: "Pessimistic value, " + year, today: App.fmtCad(was.p5_T), withAsset: App.fmtCad(now.p5_T),
        worse: isLower(was.p5_T, now.p5_T)},
      {label: "Annual volatility", today: App.fmtPct(was.vol), withAsset: App.fmtPct(now.vol),
        worse: isHigher(was.vol, now.vol)},
      {label: "Max drawdown", today: App.fmtPct(was.mdd), withAsset: App.fmtPct(now.mdd),
        worse: isLower(was.mdd, now.mdd)},
      {label: "Bad-scenario loss", today: App.fmtCad(ctx.baseLoss), withAsset: App.fmtCad(ctx.scenLoss),
        worse: isHigher(ctx.baseLoss, ctx.scenLoss)},
      {label: "Share in single stocks", today: App.fmtPct(ctx.baseShares.single_stocks),
        withAsset: App.fmtPct(ctx.shares.single_stocks)},
      {label: "Share in bonds", today: App.fmtPct(ctx.baseShares.bonds), withAsset: App.fmtPct(ctx.shares.bonds)}
    ];
  }

  function buildTiles(ctx) {
    const [was, now] = [ctx.baseMetrics, ctx.metrics];
    return [
      {id: "p5", value: money(now.p5_T), tone: "bad", delta: deltaText(was.p5_T, now.p5_T)},
      {id: "p50", value: money(now.p50_T), tone: "mid", delta: deltaText(was.p50_T, now.p50_T)},
      {id: "p95", value: money(now.p95_T), tone: "good", delta: deltaText(was.p95_T, now.p95_T)}
    ];
  }

  function lossOf(portfolioValue, metricsRow) {
    return hasNumber(portfolioValue) && hasNumber(metricsRow.p5_T) ? portfolioValue - Number(metricsRow.p5_T) : null;
  }

  /* ctx: {base, scenario, amountCad, funding, fundingLabel, years}. Returns every derived figure. */
  function scenarioView(ctx) {
    const base = ctx.base;
    const value = hasNumber(base.value_cad) ? Number(base.value_cad) : null;
    const baseMetrics = base.metrics || {};
    const metrics = ctx.scenario.metrics || {};
    const full = {
      ...ctx,
      baseMetrics,
      metrics,
      baseShares: base.shares || {},
      shares: ctx.scenario.shares || {},
      baseLoss: lossOf(value, baseMetrics),
      // A new cash contribution grows the portfolio: its loss is measured from the grown value.
      scenLoss: lossOf(hasNumber(ctx.scenario.value_cad) ? Number(ctx.scenario.value_cad) : value, metrics),
      amount: hasNumber(ctx.amountCad) ? Number(ctx.amountCad) : null,
      pct: pctFromAmount(ctx.amountCad, value)
    };
    const pillars = buildPillars(full);
    return {
      verdict: verdictStatus(ctx.scenario.verdict),
      todayVerdict: verdictStatus(base.verdict),
      finding: findingText(full, pillars),
      tiles: buildTiles(full),
      rows: buildRows(full),
      pillars
    };
  }

  App.whatifView = {
    keyOf, hasNumber, minus, amountLabel, assetLabel, profileLine, asOfLine, yearsOf, scenarioView,
    CASH_FUNDING, heldPct, amountFromPct, pctFromAmount, presetAmount, presetFor, capacityCad, amountProblem, pctText
  };
})();
