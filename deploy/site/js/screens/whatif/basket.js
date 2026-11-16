(function () {
  "use strict";

  /* Prototype basket flow (live API only): the assets ticked in Search, each with the CAD amount and funding the
     user types -> all trades checked together (one verdict, limits crossed, cone over time) -> a chat that answers
     questions about that report only (server: gated, numchecked, template fallback). Nothing here suggests an
     asset, an amount or a funding source; the user states each of them. Static mode keeps the old what-if screen. */

  const view = App.whatifView;
  const DEBOUNCE_MS = 500;
  const SUGGESTIONS = [
    "Does this fit my risk profile?",
    "Which of my limits do these trades cross, and why do those limits exist?",
    "How does the portfolio behave over time compared with today?",
    "What can't the tool verify here?"
  ];

  const st = {
    persona: null,
    rows: [],          // [{ticker, amount, funding}]
    result: null,      // {key, scenario, base} | null
    loading: false,
    error: "",
    chat: [],          // [{role, text, mode?}]
    chatBusy: false,
    reqSeq: 0
  };
  let timer = null;

  function tickers() {
    const raw = App.routeParams().get("assets");
    const list = raw ? raw.split(",").filter(Boolean) : (App.state.selection || []);
    return list.filter((t, i) => list.indexOf(t) === i);
  }

  function universe() {
    return App.state.universe || {assets: [], base: {}};
  }

  function asset(ticker) {
    return (universe().assets || []).find(a => a.ticker === ticker) || {ticker, name: ticker};
  }

  function fundings() {
    const base = universe().base || {};
    const weights = base.weights_pct || {};
    const held = Object.keys(weights).filter(t => weights[t] > 0)
      .sort((a, b) => String(asset(a).name).localeCompare(String(asset(b).name)));
    return [{id: "cash", label: "New cash (nothing sold)"}, {id: "pro_rata_all", label: "Sell a slice of every holding"}]
      .concat(held.map(t => ({id: "specific:" + t, label: "Sell from " + asset(t).name + " (" + t + ")"})));
  }

  function sync() {
    if (st.persona !== App.state.persona) {
      Object.assign(st, {persona: App.state.persona, rows: [], result: null, error: "", chat: []});
    }
    const wanted = tickers();
    st.rows = wanted.map(t => st.rows.find(r => r.ticker === t) || {ticker: t, amount: null, funding: "cash"});
  }

  function validTrades() {
    if (!st.rows.length) return null;
    if (st.rows.some(r => !(Number(r.amount) > 0))) return null;
    return st.rows.map(r => ({ticker: r.ticker, amount_cad: Number(r.amount), funding: r.funding}));
  }

  function tradesKey(trades) {
    return JSON.stringify(trades);
  }

  function schedule(root) {
    clearTimeout(timer);
    timer = setTimeout(() => runCheck(root), DEBOUNCE_MS);
  }

  function runCheck(root) {
    const trades = validTrades();
    if (!trades) { st.result = null; st.error = ""; paintResults(root); return; }
    const key = tradesKey(trades);
    if (st.result && st.result.key === key) return;
    const seq = ++st.reqSeq;
    st.loading = true; st.error = "";
    paintResults(root);
    App.source.basket(App.state.persona, trades).then(data => {
      if (seq !== st.reqSeq) return;
      st.result = {key, scenario: data.scenario, base: data.base};
    }).catch(error => {
      if (seq !== st.reqSeq) return;
      st.result = null;
      st.error = String(error && error.message || error).replace(/^HTTP \d+ for \S+: ?/, "");
    }).then(() => {
      if (seq !== st.reqSeq) return;
      st.loading = false;
      paintResults(root);
    });
  }

  /* ---------- markup ---------- */

  function rowMarkup(row, index) {
    const a = asset(row.ticker);
    const opts = fundings().map(f => '<option value="' + App.esc(f.id) + '"' + (f.id === row.funding ? " selected" : "")
      + ">" + App.esc(f.label) + "</option>").join("");
    const held = Number(a.held_pct) > 0 ? '<span class="bk-held">held: ' + App.esc(view.pctText(a.held_pct)) + "</span>" : "";
    return '<tr data-row="' + index + '">'
      + '<td><strong>' + App.esc(a.name || row.ticker) + '</strong> <span class="muted">' + App.esc(row.ticker) + "</span> " + held + "</td>"
      + '<td><div class="wf-amount"><span class="wf-amount__unit" aria-hidden="true">$</span>'
      + '<input class="wf-amount__input" type="number" min="0" step="any" inputmode="decimal" placeholder="Amount"'
      + ' data-amount="' + index + '" aria-label="Amount for ' + App.esc(row.ticker) + ', CAD" value="'
      + (row.amount === null ? "" : App.esc(String(row.amount))) + '"></div></td>'
      + '<td><select class="wf-scn__select" data-funding="' + index + '" aria-label="Funding for ' + App.esc(row.ticker) + '">' + opts + "</select></td>"
      + '<td><button type="button" class="bk-x" data-remove="' + App.esc(row.ticker) + '" aria-label="Remove ' + App.esc(row.ticker) + '">×</button></td>'
      + "</tr>";
  }

  function shell() {
    return '<div class="wrap scr-whatif scr-basket">'
      + '<a class="back" href="#/search"><img class="wf-back__icon" src="assets/icon-back.svg" alt="" width="16" height="16"><span>Back to search (add or remove assets)</span></a>'
      + '<div class="wf-title"><h1 class="t-title">Check and simulate your trades</h1>'
      + '<p class="wf-meta" data-bk="profile"></p></div>'
      + '<section class="card" aria-label="Your trades"><h3>1. Your trades</h3>'
      + '<p class="t-small muted">Type how much you want to put in each asset and where the money comes from. The trades are applied together, in this order.</p>'
      + '<div class="wf-tbl"><table class="table bk-table"><thead><tr><th>Asset</th><th>Amount (CAD)</th><th>Funded from</th><th></th></tr></thead>'
      + '<tbody data-bk="rows"></tbody></table></div>'
      + '<p class="t-small" data-bk="total"></p></section>'
      + '<p class="t-body-strong wf-error" data-bk="error" role="alert" hidden></p>'
      + '<p class="t-small muted" data-bk="status" aria-live="polite"></p>'
      + '<div data-bk="results" hidden>'
      + '<section class="card wf-verdict" aria-label="Verdict"><div class="wf-verdict__main">'
      + '<p class="wf-verdict__label">2. Do these trades fit your profile?</p><div data-bk="verdict"></div>'
      + '<p class="t-body" data-bk="finding"></p><ul class="bk-limits" data-bk="limits"></ul></div>'
      + '<div class="wf-verdict__side"><div class="wf-verdict__item"><p class="wf-verdict__label">Portfolio today</p><div data-bk="today"></div></div>'
      + '<div class="wf-verdict__item"><p class="wf-verdict__label">With these trades</p><div data-bk="with"></div></div></div></section>'
      + '<div class="bk-cols">'
      + '<section class="card bk-chat" aria-label="Ask about these trades"><h3>3. Ask about these trades</h3>'
      + '<p class="t-small muted">Answers come only from the report on this page. The assistant describes; it does not tell you what to buy or sell.</p>'
      + '<div class="bk-msgs" data-bk="msgs"></div>'
      + '<div class="bk-sugs" data-bk="sugs"></div>'
      + '<form class="bk-form" data-bk="form"><input type="text" class="bk-input" data-bk="input" maxlength="500" placeholder="Ask anything about these trades…" aria-label="Your question">'
      + '<button class="btn btn--primary" type="submit">Ask</button></form></section>'
      + '<section class="card wf-proj" aria-label="Simulation over time"><div class="wf-proj__head"><h3>4. Simulation over time</h3>'
      + '<p class="wf-proj__meta" data-bk="asof"></p></div>'
      + '<div class="wf-fan"><div data-chart="basket"></div></div>'
      + '<div class="wf-legend"><span class="wf-legend__item"><span class="wf-legend__swatch wf-legend__swatch--band"></span><span class="wf-legend__text">P5 to P95</span></span>'
      + '<span class="wf-legend__item"><span class="wf-legend__swatch wf-legend__swatch--ink"></span><span class="wf-legend__text">Median with trades</span></span>'
      + '<span class="wf-legend__item"><span class="wf-legend__swatch wf-legend__swatch--blue"></span><span class="wf-legend__text">Current median</span></span>'
      + '<span class="wf-legend__item"><span class="wf-legend__swatch wf-legend__swatch--floor"></span><span class="wf-legend__text">Floor</span></span></div>'
      + '<div class="wf-tiles" data-bk="tiles"></div>'
      + '<table class="table wf-ba__table"><thead><tr><th>Measure</th><th class="r">Today</th><th class="r">With trades</th></tr></thead><tbody data-bk="ba"></tbody></table>'
      + '</section></div>'
      + '<section class="card wf-coh" aria-label="Coherence by pillar"><h3>Coherence by pillar</h3><div class="wf-tbl"><table class="table">'
      + '<thead><tr><th>Pillar</th><th>Today</th><th>With trades</th><th>Finding</th></tr></thead><tbody data-bk="coh"></tbody></table></div></section>'
      + '</div>'
      + '<p class="wf-note">Simulation from monthly history with a 12-month block bootstrap, recomputed with the new weights. Not a forecast. Educational simulation: not financial advice, no transaction is executed.</p>'
      + "</div>";
  }

  function el(root, key) { return root.querySelector('[data-bk="' + key + '"]'); }

  function paintRows(root) {
    el(root, "rows").innerHTML = st.rows.length ? st.rows.map(rowMarkup).join("")
      : '<tr><td colspan="4">No assets yet. <a href="#/search">Go to search</a> and tick a couple of assets.</td></tr>';
    const total = st.rows.reduce((sum, r) => sum + (Number(r.amount) > 0 ? Number(r.amount) : 0), 0);
    const value = (universe().base || {}).value_cad;
    el(root, "total").textContent = st.rows.length
      ? "Total: $" + App.fmtCad(total, 0) + (value ? " (" + view.pctText(total / value * 100) + " of the current portfolio value of $" + App.fmtCad(value, 0) + ")" : "")
      : "";
  }

  function finding(base, scen) {
    const b = base.metrics || {}, m = scen.metrics || {};
    const yrs = view.yearsOf(scen.cone);
    const n = (scen.trades || []).length;
    return "With " + (n === 1 ? "this trade" : "these " + n + " trades") + ", the median value at year " + yrs + " moves from $"
      + App.fmtCad(b.p50_T) + " to $" + App.fmtCad(m.p50_T) + " and the pessimistic value from $" + App.fmtCad(b.p5_T)
      + " to $" + App.fmtCad(m.p5_T) + ". Annual volatility moves from " + App.fmtPct(b.vol) + " to " + App.fmtPct(m.vol)
      + ". This describes the trades you entered; it is not a suggestion to make or avoid them.";
  }

  function limitsMarkup(scen) {
    const lines = [];
    (scen.sections || []).forEach(sec => {
      if (sec.after !== "violation" && sec.after !== "warning") return;
      (sec.segments || []).forEach(seg => {
        String(seg.text || "").split(/(?<=\.)\s+(?=[A-Z])/).forEach(t => {
          if (t && !/cannot be verified/i.test(t) && lines.indexOf(t) < 0) lines.push(t);
        });
      });
    });
    return lines.map(t => "<li>" + App.esc(t) + "</li>").join("");
  }

  function paintResults(root) {
    const res = st.result;
    const err = el(root, "error");
    err.hidden = !st.error; err.textContent = st.error ? "These trades could not be checked: " + st.error : "";
    el(root, "status").textContent = st.loading ? "Checking the trades together…"
      : (!validTrades() && st.rows.length ? "Enter an amount greater than $0 for every asset to check them." : "");
    el(root, "results").hidden = !res;
    if (!res) return;
    const {scenario: scen, base} = res;
    const v = view.scenarioView({base, scenario: scen, amountCad: 0, funding: "cash", fundingLabel: "", years: view.yearsOf(base.cone)});
    el(root, "verdict").innerHTML = App.chip(v.verdict);
    el(root, "today").innerHTML = App.chip(v.todayVerdict);
    el(root, "with").innerHTML = App.chip(v.verdict);
    el(root, "finding").textContent = finding(base, scen);
    el(root, "limits").innerHTML = limitsMarkup(scen);
    el(root, "tiles").innerHTML = v.tiles.map(t => '<div class="wf-tile"><p class="wf-tile__label">'
      + ({p5: "Pessimistic (P5)", p50: "Median", p95: "Optimistic (P95)"}[t.id]) + '</p><p class="wf-tile__value wf-tile__value--' + t.tone + '">'
      + App.esc(t.value) + '</p><p class="wf-tile__delta">' + App.esc(t.delta) + "</p></div>").join("");
    el(root, "ba").innerHTML = v.rows.map(r => "<tr><td>" + App.esc(r.label) + '</td><td class="r">' + App.esc(view.minus(r.today))
      + '</td><td class="r' + (r.worse ? " wf-bad" : "") + '"><strong>' + App.esc(view.minus(r.withAsset)) + "</strong></td></tr>").join("");
    el(root, "coh").innerHTML = v.pillars.map(p => '<tr><td class="wf-pillar">' + App.esc(p.label) + "</td><td>" + App.chip(p.today)
      + "</td><td>" + App.chip(p.withAsset) + "</td><td>" + App.esc(p.finding.replace(/this asset/g, "these trades")) + "</td></tr>").join("");
    el(root, "asof").textContent = view.asOfLine(base);
    const host = root.querySelector('[data-chart="basket"]');
    const limits = App.state.report && App.state.report.site ? App.state.report.site.limits : null;
    const floor = limits && Number.isFinite(Number(limits.floor_at_horizon)) ? Number(limits.floor_at_horizon) : undefined;
    const drawn = App.charts && App.charts.fan && App.charts.fan(host, {cone: scen.cone, compareMedian: (base.cone || {}).p50,
      floor, years: view.yearsOf(base.cone), asOf: base.as_of});
    if (!drawn) host.innerHTML = '<p class="t-small muted">Chart unavailable</p>';
    paintChat(root);
  }

  function paintChat(root) {
    el(root, "msgs").innerHTML = st.chat.map(m => '<div class="sq-msg sq-msg--' + (m.role === "user" ? "you" : "agent") + '">'
      + '<p class="sq-msg__who">' + (m.role === "user" ? "You" : "Assistant" + (m.mode === "template" ? " · fixed summary" : "")) + "</p>"
      + '<p class="sq-msg__text">' + App.esc(m.text) + "</p></div>").join("")
      + (st.chatBusy ? '<div class="sq-msg sq-msg--agent"><p class="sq-msg__who">Assistant</p><p class="sq-msg__text">Thinking…</p></div>' : "");
    el(root, "sugs").innerHTML = st.chat.length ? "" : SUGGESTIONS.map(q => '<button type="button" class="sq-chip bk-sug" data-ask="'
      + App.esc(q) + '">' + App.esc(q) + "</button>").join("");
    const box = el(root, "msgs");
    box.scrollTop = box.scrollHeight;
  }

  function ask(root, text) {
    const q = String(text || "").trim();
    const trades = validTrades();
    if (!q || !trades || st.chatBusy) return;
    st.chat.push({role: "user", text: q});
    st.chatBusy = true;
    paintChat(root);
    const history = st.chat.map(m => ({role: m.role, text: m.text}));
    App.source.basketChat(App.state.persona, trades, history).then(out => {
      st.chat.push({role: "assistant", text: out.text, mode: out.mode});
    }).catch(error => {
      st.chat.push({role: "assistant", text: "The question could not be answered right now (" + String(error && error.message || error).slice(0, 120) + ").", mode: "template"});
    }).then(() => { st.chatBusy = false; paintChat(root); });
  }

  function bind(root) {
    root.addEventListener("input", e => {
      const i = e.target.getAttribute("data-amount");
      if (i === null) return;
      st.rows[Number(i)].amount = e.target.value === "" ? null : Number(e.target.value);
      paintRowsTotalOnly(root);
      schedule(root);
    });
    root.addEventListener("change", e => {
      const i = e.target.getAttribute("data-funding");
      if (i === null) return;
      st.rows[Number(i)].funding = e.target.value;
      schedule(root);
    });
    root.addEventListener("click", e => {
      const rm = e.target.closest("[data-remove]");
      if (rm) {
        const t = rm.getAttribute("data-remove");
        App.setSelection((App.state.selection || []).filter(x => x !== t));
        App.go("#/whatif?assets=" + tickers().filter(x => x !== t).map(encodeURIComponent).join(","));
        return;
      }
      const sug = e.target.closest("[data-ask]");
      if (sug) ask(root, sug.getAttribute("data-ask"));
    });
    root.addEventListener("submit", e => {
      if (!e.target.closest('[data-bk="form"]')) return;
      e.preventDefault();
      const input = el(root, "input");
      const v = input.value; input.value = "";
      ask(root, v);
    });
  }

  function paintRowsTotalOnly(root) {
    const total = st.rows.reduce((sum, r) => sum + (Number(r.amount) > 0 ? Number(r.amount) : 0), 0);
    const value = (universe().base || {}).value_cad;
    el(root, "total").textContent = "Total: $" + App.fmtCad(total, 0)
      + (value ? " (" + view.pctText(total / value * 100) + " of the current portfolio value of $" + App.fmtCad(value, 0) + ")" : "");
  }

  function render(section) {
    let root = section.querySelector("[data-basket]");
    const legacy = section.querySelector(".scr");
    if (!root) {
      root = document.createElement("div");
      root.setAttribute("data-basket", "");
      root.innerHTML = shell();
      section.appendChild(root);
      bind(root);
    }
    if (legacy) legacy.hidden = true;
    sync();
    el(root, "profile").textContent = view.profileLine(App.state.report);
    paintRows(root);
    paintResults(root);
    paintChat(root);
    schedule(root);
  }

  const legacyRender = App.renderers.whatif;
  App.renderers.whatif = function (section) {
    if (App.source && App.source.mode === "live" && App.source.basket) render(section);
    else legacyRender(section);
  };
})();
