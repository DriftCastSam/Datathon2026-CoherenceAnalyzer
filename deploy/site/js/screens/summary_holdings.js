(() => {
  "use strict";

  /* Key data, holdings table, accounts menu and nav-action wiring for the summary screen.
     Shares helpers with summary.js through App._summary. */
  const {num, subjectStatus, CAD_SIGN, PALETTE, LIVE_PALETTE, SAMPLE} = App._summary;

  const BUSY_LABEL = "Running…";

  /* ---------- key data ---------- */

  function assetIndex(universe) {
    const byTicker = new Map();
    const assets = universe && Array.isArray(universe.assets) ? universe.assets : [];
    for (const asset of assets) {
      if (asset && typeof asset.ticker === "string" && asset.ticker) byTicker.set(asset.ticker, asset);
    }
    return byTicker;
  }

  function holdingsList(ctx) {
    const weights = ctx.site && ctx.site.weights_pct && typeof ctx.site.weights_pct === "object" ? ctx.site.weights_pct : null;
    const entries = weights
      ? Object.keys(weights).map(ticker => [ticker, num(weights[ticker], 0)]).filter(entry => entry[1] > 0)
      : [];
    if (!entries.length) {
      return SAMPLE.holdings.map((holding, index) => ({
        name: holding.name,
        pct: holding.pct,
        tag: holding.tag || "",
        color: holding.tag === "Flagged" ? "var(--brand)" : PALETTE[index % PALETTE.length]
      }));
    }
    entries.sort((left, right) => right[1] - left[1]);
    const assets = assetIndex(ctx.universe);
    const drivers = new Map();
    const rows = ctx.report && ctx.metrics && Array.isArray(ctx.metrics.drivers) ? ctx.metrics.drivers : [];
    for (const driver of rows) {
      if (driver && typeof driver.ticker === "string") drivers.set(driver.ticker, driver);
    }
    const statuses = subjectStatus(ctx.report);
    return entries.map((entry, index) => {
      const ticker = entry[0];
      const asset = assets.get(ticker);
      const driver = drivers.get(ticker);
      const name = asset && typeof asset.name === "string" && asset.name ? asset.name
        : driver && typeof driver.name === "string" && driver.name ? driver.name : ticker;
      const status = statuses.get(ticker);
      const tag = status === "violation" ? "Flagged" : status === "warning" ? "Warning" : "";
      return {
        name,
        pct: entry[1],
        tag,
        color: tag === "Flagged" ? "var(--brand)" : LIVE_PALETTE[index % LIVE_PALETTE.length]
      };
    });
  }

  function holdingRowHtml(holding, value) {
    const tag = holding.tag === "Flagged"
      ? '<span class="sum__tag sum__tag--flag">Flagged</span>'
      : holding.tag === "Warning" ? '<span class="sum__tag sum__tag--warn">Warning</span>' : "";
    const name = '<span class="sum__hold-text">' + App.esc(holding.name) + '</span>';
    const label = holding.tag === "Flagged"
      ? '<span class="sum__hold-name">' + name + tag + '</span>'
      : '<span class="sum__hold-row">' + name + tag + '</span>';
    return '<tr' + (holding.tag === "Flagged" ? ' class="is-flag"' : '') + '>'
      + '<td><span class="sum__hold"><span class="sum__dot" style="background:' + holding.color + '"></span>' + label + '</span></td>'
      + '<td class="r sum__weight">' + App.esc(App.fmtPct(holding.pct)) + '</td>'
      + '<td class="r">' + CAD_SIGN + App.esc(App.fmtCad(holding.pct / 100 * value)) + '</td></tr>';
  }

  function keyDataHtml(ctx) {
    const value = num(ctx.site && ctx.site.value_cad, SAMPLE.value);
    const vol = ctx.metrics ? num(ctx.metrics.vol, SAMPLE.vol) : SAMPLE.vol;
    const mdd = ctx.metrics ? num(ctx.metrics.mdd, SAMPLE.mdd) : SAMPLE.mdd;
    const holdings = holdingsList(ctx);
    const figures = [
      ["Current value", CAD_SIGN + App.fmtCad(value)],
      ["Annual volatility", App.fmtPct(vol)],
      ["Max drawdown", "−" + App.fmtPct(Math.abs(mdd))],
      ["Holdings", String(holdings.length)]
    ];
    const kv = '<div class="sum__kv">' + figures.slice(0, 2).map(item =>
      '<div><p class="sum__kv-label">' + App.esc(item[0]) + '</p><p class="sum__kv-value num">' + App.esc(item[1]) + '</p></div>'
    ).join("") + '</div><div class="sum__kv">' + figures.slice(2).map(item =>
      '<div><p class="sum__kv-label">' + App.esc(item[0]) + '</p><p class="sum__kv-value num">' + App.esc(item[1]) + '</p></div>'
    ).join("") + '</div>';
    const bar = '<div class="sum__alloc"><p class="sum__alloc-title">Allocation</p>'
      + '<div class="sum__bar" aria-hidden="true">' + holdings.map(holding =>
        '<span class="sum__bar-seg" style="flex-grow:' + holding.pct + ';background:' + holding.color + '"></span>'
      ).join("") + '</div></div>';
    const table = '<table class="table sum__hold-table">'
      + '<colgroup><col><col style="width:48px"><col style="width:72px"></colgroup>'
      + '<thead><tr><th>Holding</th><th class="r">Weight</th><th class="r">Value</th></tr></thead>'
      + '<tbody>' + holdings.map(holding => holdingRowHtml(holding, value)).join("") + '</tbody></table>';
    return kv + bar + table;
  }

  /* ---------- accounts menu (node 7:1097) ---------- */

  function summaryScreen() {
    return document.querySelector('section[data-route="summary"] .scr-summary');
  }

  function accountsTab() {
    const tabs = document.getElementById("app-tabs");
    if (!tabs) return null;
    return Array.prototype.find.call(tabs.querySelectorAll("a.tab"), tab => {
      const label = tab.querySelector(".tab__label");
      return label !== null && label.textContent.trim() === "Accounts";
    }) || null;
  }

  function setMenuState(open) {
    const screen = summaryScreen();
    if (screen) screen.classList.toggle("is-open", open);
    const tab = accountsTab();
    if (!tab) return;
    tab.setAttribute("aria-expanded", open ? "true" : "false");
    tab.setAttribute("aria-controls", "summary-accounts-menu");
  }

  function closeMenu() {
    setMenuState(false);
  }

  const tabs = document.getElementById("app-tabs");
  if (tabs) {
    tabs.addEventListener("click", event => {
      const target = event.target instanceof Element ? event.target.closest("a.tab") : null;
      if (!target || App.route !== "summary") return;
      const label = target.querySelector(".tab__label");
      if (!label || label.textContent.trim() !== "Accounts") return;
      event.preventDefault();
      const screen = summaryScreen();
      if (screen) setMenuState(!screen.classList.contains("is-open"));
    });
  }

  document.addEventListener("click", event => {
    const target = event.target instanceof Element ? event.target : null;
    if (target && (target.closest("#app-tabs") || target.closest(".scr-summary .sum__menu"))) return;
    closeMenu();
  });

  document.addEventListener("keydown", event => {
    if (event.key === "Escape") closeMenu();
  });

  const menuScreen = summaryScreen();
  if (menuScreen) {
    menuScreen.addEventListener("click", event => {
      const link = event.target instanceof Element ? event.target.closest(".sum__menu a") : null;
      if (link) closeMenu();
    });
  }

  /* ---------- global nav actions ---------- */

  function rerunMessage(row) {
    let message = row.querySelector("[data-rerun-error]");
    if (!message) {
      message = document.createElement("span");
      message.className = "t-small";
      message.setAttribute("data-rerun-error", "");
      message.setAttribute("role", "alert");
      row.appendChild(message);
    }
    return message;
  }

  async function rerunAnalysis(button, row) {
    const idleLabel = button.textContent;
    button.disabled = true;
    button.textContent = BUSY_LABEL;
    rerunMessage(row).textContent = "";
    try {
      await App.rerunAnalysis();
    } catch (error) {
      console.error("[summary] re-run failed", error);
      rerunMessage(row).textContent = "Re-run failed: " + error.message;
    } finally {
      button.disabled = false;
      button.textContent = idleLabel;
    }
  }

  function wireNavActions() {
    const row = document.querySelector(".nav-actions");
    if (!row || row.dataset.appWired) return;
    row.dataset.appWired = "summary";
    row.querySelectorAll(".btn").forEach(button => {
      const label = button.textContent.trim();
      if (label === "Edit profile") {
        button.addEventListener("click", () => { App.go("#/conversation"); });
      } else if (label === "Re-run analysis") {
        button.addEventListener("click", () => { rerunAnalysis(button, row); });
      } else if (label === "Export report") {
        button.title = "Not available in this simulation";
        button.setAttribute("aria-disabled", "true");
      }
    });
  }

  wireNavActions();

  App._summary.keyDataHtml = keyDataHtml;
  App._summary.setMenuState = setMenuState;
})();
