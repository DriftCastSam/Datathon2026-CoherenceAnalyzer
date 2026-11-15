(() => {
  "use strict";

  /* Asset class, market and sector vocabulary, translated from the reference
     dashboard's keyword engine so both screens classify the universe the same way. */
  const CLASS_LABEL = {
    etf: "ETF",
    equity: "Equity",
    etf_equity: "Equity ETF",
    etf_bond: "Bond ETF",
    etf_commodity: "Gold ETF",
    crypto: "Crypto"
  };

  const MARKET_FILTERS = {
    us: {label: "United States", test: asset => ["NASDAQ", "NYSE", "NYSE Arca", "Cboe BZX"].indexOf(asset.market) >= 0},
    ca: {label: "Canada", test: asset => asset.market === "TSX"},
    cx: {label: "Crypto (24/7)", test: asset => String(asset.market || "").indexOf("Crypto") === 0}
  };

  const SECTOR_FILTERS = {
    tech: {label: "Technology", test: asset => asset.sector === "Information Technology" || /tech/i.test(String(asset.sector || ""))},
    fin: {label: "Financials", test: asset => asset.sector === "Financials"},
    nrg: {label: "Energy", test: asset => asset.sector === "Energy"},
    hea: {label: "Health care", test: asset => asset.sector === "Health Care"},
    con: {label: "Consumer", test: asset => String(asset.sector || "").indexOf("Consumer") === 0},
    utl: {label: "Utilities", test: asset => asset.sector === "Utilities"},
    ind: {label: "Industrials", test: asset => asset.sector === "Industrials"},
    com: {label: "Communication services", test: asset => asset.sector === "Communication Services"},
    bnd: {label: "Bonds", test: asset => String(asset.sector || "").indexOf("Bonds") === 0},
    brd: {label: "Broad market", test: asset => String(asset.sector || "").indexOf("Broad") === 0},
    gld: {label: "Gold", test: asset => asset.sector === "Gold"},
    cry: {label: "Crypto", test: asset => asset.sector === "Crypto asset"}
  };

  const CURRENCIES = ["CAD", "USD"];
  const CLASS_ORDER = ["equity", "etf", "etf_equity", "etf_bond", "etf_commodity", "crypto"];

  const KEYWORDS = [
    [/\b(etfs?|funds?)\b/, "cls", ["etf"]],
    [/\b(stocks?|shares?|equit(?:y|ies)|companies|company)\b/, "cls", ["equity"]],
    [/\bbonds?\b|fixed[- ]income/, "cls", ["etf_bond"]],
    [/crypto|bitcoin|ether/, "cls", ["crypto"]],
    [/\bgold\b|metals?/, "cls", ["etf_commodity"]],
    [/united states|\bu\.?s\.?a?\b|american|nasdaq|nyse/, "mkt", ["us"]],
    [/canad|\btsx\b/, "mkt", ["ca"]],
    [/technolog|\btech\b|software|semiconductor/, "sec", ["tech"]],
    [/\bbanks?\b|financial|insur/, "sec", ["fin"]],
    [/energy|oil|petrol|pipeline/, "sec", ["nrg"]],
    [/health|pharma|medical/, "sec", ["hea"]],
    [/consumer|retail/, "sec", ["con"]],
    [/utilit|electric/, "sec", ["utl"]],
    [/industrial|rail|transport/, "sec", ["ind"]],
    [/communication|media/, "sec", ["com"]],
    [/broad market|\bindex\b|indices/, "sec", ["brd"]],
    [/\busd\b|us dollars?/, "ccy", ["USD"]],
    [/\bcad\b|canadian dollars?/, "ccy", ["CAD"]]
  ];

  /* Requests for a ranking, a pick or a trade are refused before any filtering. */
  const REFUSE = /\bbest\b|recommend|should (?:i|we)|which (?:one|ones|is|of)|top picks?|\btop\b|\bbuy\b|\bsell\b|\badvice\b|good investment|worth (?:buying|it)|pick (?:one|for me)/;
  const RESET = /\b(clear|reset)\b|start over|remove all/;
  const ADD_WORD = /\badd\b|also|include/;
  const ONLY_WORD = /\bonly\b|\bjust\b/;

  const REFUSE_REPLY = "I can't rank or recommend assets. I can add filters to narrow the list, or run a coherence analysis on the assets you select yourself.";
  const UNKNOWN_REPLY = "I didn't recognize a filter. Try an asset class (stocks, ETFs, bonds, crypto), a market (Canada, United States), a sector (technology, energy, financials) or a ticker.";

  const SAMPLE_TOTAL = 31;
  const SAMPLE_ASSETS = [
    {key: "sample-1", ticker: "[TICKER]", name: "Technology sector ETF", asset_class: "etf", market: "NYSE Arca", currency: "USD", sector: "Technology"},
    {key: "sample-2", ticker: "[TICKER]", name: "US cloud software company", asset_class: "equity", market: "NASDAQ", currency: "USD", sector: "Technology"},
    {key: "sample-3", ticker: "[TICKER]", name: "US consumer electronics company", asset_class: "equity", market: "NASDAQ", currency: "USD", sector: "Technology"},
    {key: "sample-4", ticker: "[TICKER]", name: "US semiconductor company A", asset_class: "equity", market: "NASDAQ", currency: "USD", sector: "Technology"},
    {key: "sample-5", ticker: "[TICKER]", name: "US semiconductor company B", asset_class: "equity", market: "NASDAQ", currency: "USD", sector: "Technology"},
    {key: "sample-6", ticker: "[TICKER]", name: "US social media company", asset_class: "equity", market: "NASDAQ", currency: "USD", sector: "Technology"}
  ];

  const SEED_CHAT = [
    {who: "user", text: "Show me US technology stocks and technology ETFs."},
    {who: "agent", text: "I applied 3 filters to the 31-asset universe. 6 assets match. The list is sorted A to Z and is not a recommendation."},
    {who: "user", text: "Which one of these is best for me?"},
    {who: "agent", text: REFUSE_REPLY, outside: true}
  ];

  const NORMALIZE = /[\u0300-\u036f]/g;
  const TICKER_SHAPE = /^[a-z][a-z0-9.\-]{0,9}$/;

  /* Module state, like the reference dashboard's askState: it survives re-renders. */
  const state = {
    filters: {cls: ["equity", "etf"], mkt: ["us"], sec: ["tech"], ccy: []},
    sel: ["sample-1", "sample-4"],
    chat: SEED_CHAT.slice()
  };

  function deacc(value) {
    return String(value).normalize("NFD").replace(NORMALIZE, "").toLowerCase();
  }

  function rowSource() {
    const universe = App.data && App.data.universe;
    const assets = universe && Array.isArray(universe.assets) ? universe.assets : null;
    if (assets && assets.length) {
      return {
        total: assets.length,
        rows: assets.map(asset => ({
          key: String(asset.ticker),
          ticker: asset.ticker,
          name: asset.name,
          asset_class: asset.asset_class,
          market: asset.market,
          currency: asset.currency,
          sector: asset.sector
        }))
      };
    }
    return {total: SAMPLE_TOTAL, rows: SAMPLE_ASSETS};
  }

  function matchesRow(row) {
    const filters = state.filters;
    const byClass = filters.cls.length === 0 || filters.cls.indexOf(row.asset_class) >= 0
      || (filters.cls.indexOf("etf") >= 0 && String(row.asset_class || "").indexOf("etf") === 0);
    const byMarket = filters.mkt.length === 0 || filters.mkt.some(key => MARKET_FILTERS[key] && MARKET_FILTERS[key].test(row));
    const bySector = filters.sec.length === 0 || filters.sec.some(key => SECTOR_FILTERS[key] && SECTOR_FILTERS[key].test(row));
    const byCurrency = filters.ccy.length === 0 || filters.ccy.indexOf(row.currency) >= 0;
    return byClass && byMarket && bySector && byCurrency;
  }

  function visibleRows(source) {
    return source.rows.filter(matchesRow).sort((left, right) => String(left.name).localeCompare(String(right.name), "en-CA"));
  }

  function filterCount() {
    return Object.keys(state.filters).reduce((total, dim) => total + state.filters[dim].length, 0);
  }

  function findTickers(query, rows) {
    return rows.filter(row => {
      const ticker = String(row.ticker || "").toLowerCase();
      if (!TICKER_SHAPE.test(ticker)) return false;
      const base = ticker.replace(/\.to$|-usd$/, "");
      if (!base) return false;
      const pattern = new RegExp("(^|[^a-z0-9.])" + base.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "(\\.to|-usd)?($|[^a-z0-9])");
      return pattern.test(query);
    });
  }

  function respond(text) {
    const query = deacc(text);
    if (REFUSE.test(query)) return {text: REFUSE_REPLY, outside: true};

    const source = rowSource();
    if (RESET.test(query)) {
      Object.keys(state.filters).forEach(dim => { state.filters[dim] = []; });
      return {text: "Cleared all filters. All " + source.total + " assets in the universe are shown."};
    }

    const named = findTickers(query, source.rows);
    const hits = {};
    KEYWORDS.forEach(entry => {
      if (!entry[0].test(query)) return;
      const dim = entry[1];
      hits[dim] = hits[dim] || [];
      entry[2].forEach(value => {
        if (hits[dim].indexOf(value) < 0) hits[dim].push(value);
      });
    });
    if (!Object.keys(hits).length) {
      if (named.length) {
        const tickers = named.map(row => row.ticker).join(", ");
        named.forEach(row => {
          if (state.sel.indexOf(row.key) < 0) state.sel.push(row.key);
        });
        return {text: "I checked " + tickers + " in the list. Select Analyze coherence of selected to see how the portfolio would change."};
      }
      return {text: UNKNOWN_REPLY};
    }

    if (hits.cls && hits.cls.indexOf("etf_bond") >= 0 && hits.cls.indexOf("etf") >= 0) {
      hits.cls.splice(hits.cls.indexOf("etf"), 1);
    }
    const replace = ONLY_WORD.test(query) || !ADD_WORD.test(query);
    Object.keys(hits).forEach(dim => {
      if (replace) state.filters[dim] = [];
      hits[dim].forEach(value => {
        if (state.filters[dim].indexOf(value) < 0) state.filters[dim].push(value);
      });
    });

    const count = visibleRows(source).length;
    const filters = filterCount();
    return {
      text: "I applied " + filters + (filters === 1 ? " filter" : " filters")
        + " to the " + source.total + "-asset universe. "
        + count + (count === 1 ? " asset matches" : " assets match")
        + ". The list is sorted A to Z and is not a recommendation."
    };
  }

  /* ---------- markup ---------- */

  function messageMarkup(message) {
    const isUser = message.who === "user";
    const who = isUser ? "You" : message.outside ? "Agent · outside what I can do" : "Agent";
    return '<div class="sq-msg sq-msg--' + (isUser ? "you" : "agent") + '">'
      + '<p class="sq-msg__who">' + App.esc(who) + '</p>'
      + '<p class="sq-msg__text">' + App.esc(message.text) + '</p>'
      + '</div>';
  }

  function chipMarkup(dim, label) {
    return '<span class="sq-chip"><span class="sq-chip__label">' + App.esc(label) + '</span>'
      + '<button class="sq-chip__x" type="button" data-drop="' + App.esc(dim) + '" aria-label="Remove filter ' + App.esc(label) + '">'
      + '<img src="assets/icon-x-blue.svg" alt="" width="12" height="12"></button></span>';
  }

  function chipsMarkup() {
    const filters = state.filters;
    const parts = [];
    if (filters.cls.length) {
      const ordered = filters.cls.slice().sort((left, right) => {
        const leftAt = CLASS_ORDER.indexOf(left);
        const rightAt = CLASS_ORDER.indexOf(right);
        return (leftAt < 0 ? CLASS_ORDER.length : leftAt) - (rightAt < 0 ? CLASS_ORDER.length : rightAt);
      });
      parts.push(chipMarkup("cls", "Asset class: " + ordered.map(key => CLASS_LABEL[key] || key).join(", ")));
    }
    if (filters.mkt.length) parts.push(chipMarkup("mkt", "Market: " + filters.mkt.map(key => (MARKET_FILTERS[key] || {}).label || key).join(", ")));
    if (filters.sec.length) parts.push(chipMarkup("sec", "Sector: " + filters.sec.map(key => (SECTOR_FILTERS[key] || {}).label || key).join(", ")));
    if (filters.ccy.length) parts.push(chipMarkup("ccy", "Currency: " + filters.ccy.join(", ")));
    return parts.join("");
  }

  function optionsMarkup(dim, label, entries) {
    return '<optgroup label="' + App.esc(label) + '">'
      + entries.map(entry => '<option value="' + App.esc(dim + ":" + entry[0]) + '">' + App.esc(entry[1]) + '</option>').join("")
      + '</optgroup>';
  }

  function addOptionsMarkup() {
    return optionsMarkup("cls", "Asset class", Object.keys(CLASS_LABEL).map(key => [key, CLASS_LABEL[key]]))
      + optionsMarkup("mkt", "Market", Object.keys(MARKET_FILTERS).map(key => [key, MARKET_FILTERS[key].label]))
      + optionsMarkup("sec", "Sector", Object.keys(SECTOR_FILTERS).map(key => [key, SECTOR_FILTERS[key].label]))
      + optionsMarkup("ccy", "Currency", CURRENCIES.map(key => [key, key]));
  }

  function rowMarkup(row) {
    const selected = state.sel.indexOf(row.key) >= 0;
    return '<tr class="sq-row' + (selected ? " sq-row--on" : "") + '">'
      + '<td><input class="sq-check" type="checkbox" data-key="' + App.esc(row.key) + '"'
      + (selected ? " checked" : "") + ' aria-label="Select ' + App.esc(row.name) + '"></td>'
      + '<td class="sq-name">' + App.esc(row.name) + '</td>'
      + '<td>' + App.esc(row.ticker) + '</td>'
      + '<td>' + App.esc(CLASS_LABEL[row.asset_class] || row.asset_class) + '</td>'
      + '<td>' + App.esc(row.sector) + '</td>'
      + '<td>' + App.esc(row.currency) + '</td>'
      + '</tr>';
  }

  function rowsMarkup(rows) {
    if (!rows.length) {
      return '<tr><td colspan="6" class="t-small muted">No asset matches these filters. Remove a filter to widen the list.</td></tr>';
    }
    return rows.map(rowMarkup).join("");
  }

  function countLabel(matched, total) {
    return matched + " of " + total + (matched === 1 ? " asset matches" : " assets match");
  }

  function selectionLabel() {
    return state.sel.length + (state.sel.length === 1 ? " asset selected" : " assets selected");
  }

  /* ---------- render ---------- */

  function pruneSelection(source) {
    state.sel = state.sel.filter(key => source.rows.some(row => row.key === key));
  }

  function paintSelection(section) {
    const label = section.querySelector("[data-selected]");
    if (label) label.textContent = selectionLabel();
    const button = section.querySelector("[data-analyze]");
    if (button) button.disabled = state.sel.length === 0;
  }

  /* Profile note: the declared name, risk level and horizon, never the sample values for a
     live report. The risk label comes from the shared map; the horizon uses the same buckets
     as the coherence screen. A missing value is omitted instead of invented. */
  function noteHorizon(years) {
    const count = Number(years);
    if (!Number.isFinite(count) || count <= 0) return "";
    if (count < 1) return "Under 1 year";
    if (count <= 2) return "1 to 2 years";
    if (count <= 5) return "3 to 5 years";
    if (count <= 10) return "6 to 10 years";
    return "More than 10 years";
  }

  function paintNote(section) {
    const note = section.querySelector("[data-note]");
    if (!note) return;
    const report = App.data && App.data.report;
    let text = "Robert \u00b7 low to moderate risk \u00b7 3 to 5 years";
    if (report) {
      const card = report.site && report.site.profile_card ? report.site.profile_card : null;
      const parts = [];
      const name = card && typeof card.name === "string" && card.name.trim() ? card.name.trim() : "";
      if (name) parts.push(name);
      const riskKey = card ? (card.risk_effective || card.risk_declared) : null;
      const risk = riskKey && App.riskLevels[riskKey] ? App.riskLevels[riskKey].toLowerCase() : "";
      if (risk) parts.push(risk + " risk");
      const horizon = card ? noteHorizon(card.horizon_years) : "";
      if (horizon) parts.push(horizon);
      text = parts.join(" \u00b7 ");
    }
    if (note.textContent !== text) note.textContent = text;
  }

  function scrollThread(section) {
    const thread = section.querySelector("[data-thread]");
    if (thread) thread.scrollTop = thread.scrollHeight;
  }

  function paint(section) {
    const thread = section.querySelector("[data-thread]");
    const chips = section.querySelector("[data-chips]");
    const count = section.querySelector("[data-count]");
    const rows = section.querySelector("[data-rows]");
    const add = section.querySelector("[data-add]");
    if (!thread || !chips || !count || !rows || !add) return;

    const source = rowSource();
    pruneSelection(source);
    const visible = visibleRows(source);

    thread.innerHTML = state.chat.map(messageMarkup).join("");
    chips.innerHTML = chipsMarkup();
    count.textContent = countLabel(visible.length, source.total);
    rows.innerHTML = rowsMarkup(visible);
    if (add.options.length === 1) add.insertAdjacentHTML("beforeend", addOptionsMarkup());
    add.value = "";
    paintSelection(section);
    paintNote(section);
    scrollThread(section);
  }

  function send(section, text) {
    const value = String(text || "").trim();
    if (!value) return;
    state.chat.push({who: "user", text: value});
    const reply = respond(value);
    reply.who = "agent";
    state.chat.push(reply);
    paint(section);
    const field = section.querySelector("[data-message-input]");
    if (field) field.focus();
  }

  /* Delegated once per section; the shell keeps the section between visits. */
  function bind(section) {
    if (section.dataset.searchBound === "1") return;
    section.dataset.searchBound = "1";

    section.addEventListener("submit", event => {
      const form = event.target.closest("form[data-query-form], form[data-message-form]");
      if (!form) return;
      event.preventDefault();
      const input = form.querySelector("input[type=text]");
      const value = input ? input.value : "";
      if (input) input.value = "";
      send(section, value);
    });

    section.addEventListener("click", event => {
      const action = event.target.closest("[data-say], [data-drop], [data-clear], [data-analyze]");
      if (!action) return;
      if (action.hasAttribute("data-say")) {
        send(section, action.getAttribute("data-say"));
        return;
      }
      if (action.hasAttribute("data-drop")) {
        const dim = action.getAttribute("data-drop");
        if (state.filters[dim]) state.filters[dim] = [];
        paint(section);
        return;
      }
      if (action.hasAttribute("data-clear")) {
        Object.keys(state.filters).forEach(dim => { state.filters[dim] = []; });
        paint(section);
        return;
      }
      if (state.sel.length) App.go("#/whatif");
    });

    section.addEventListener("change", event => {
      const box = event.target.closest("input[data-key]");
      if (box) {
        const key = box.getAttribute("data-key");
        const at = state.sel.indexOf(key);
        if (box.checked && at < 0) state.sel.push(key);
        if (!box.checked && at >= 0) state.sel.splice(at, 1);
        const row = box.closest("tr");
        if (row) row.classList.toggle("sq-row--on", box.checked);
        paintSelection(section);
        return;
      }
      const adder = event.target.closest("select[data-add]");
      if (!adder || !adder.value) return;
      const split = adder.value.split(/:(.*)/);
      const dim = split[0];
      const key = split[1];
      if (state.filters[dim] && key && state.filters[dim].indexOf(key) < 0) state.filters[dim].push(key);
      paint(section);
    });
  }

  App.renderers.search = function (section) {
    bind(section);
    paint(section);
  };

  /* The design (node 8:552) keeps the standard Accounts chrome, with Accounts active. */
  App.navSpec.search = {
    chrome: "full",
    tabs: [
      {label: "Accounts", href: "#/summary", active: true},
      {label: "Markets", href: "#", active: false},
      {label: "Activities", href: "#", active: false},
      {label: "Documents", href: "#", active: false},
      {label: "Research", href: "#", active: false}
    ]
  };
})();
