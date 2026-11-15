(() => {
  "use strict";

  /* Flow B on the new UI: draft a trade from free text (POST /profiles/{id}/events, type
     order_intent), edit the fields the server could not read from the text, then confirm
     (POST /intents/{id}/confirm) and show the before/after coherence report. Live API only
     (D10: nothing here ranks, suggests or defaults a field the user did not state). */

  const ROUTE = "trade";
  const LIVE_ONLY_MESSAGE = "This screen needs a live connection to the API to describe and confirm your own "
    + "trade. Start the local server, then reopen this screen.";
  /* The offline/static demo report (deploy/preview_report.py `build_en_ca`, Robert + NVDA to 10 %,
     funded from ZST.TO): its own file, read directly, never through the prebuilt reports index. */
  const STATIC_DEMO_PATH = "reports/en-CA/robert-nvda.json";
  const STATIC_DEMO_LOADING_MESSAGE = "Loading the example trade report…";
  const CONFIRM_NOTE_LIVE = "Edit any field before confirming. Nothing is applied until you confirm.";
  const CONFIRM_NOTE_DEMO = "No live connection: this is an example report, read-only. Editing or confirming "
    + "sends nothing anywhere.";

  App.navSpec[ROUTE] = {tabs: [], chrome: "bare"};

  /* Module state: survives re-renders of this screen, cleared only by describing a new trade. */
  const screen = {
    draft: null,          // the `intent` object returned by requestDraft: {intent_id, action, ticker,
                           // target_weight_pct, funding, status, unstated_fields, event_id}
    paintedDraft: null,    // identity check: avoid wiping in-progress edits on an unrelated re-render
    report: null,          // the confirmIntent response (the trade-check report)
    universe: null,        // {assets:[...]} from App.source.universe(personaId), when available
    universeTried: false,
    demoRequested: false,  // the static-demo report fetch (below) is attempted at most once per page load
    demoError: null
  };

  const esc = value => App.esc(value);
  const v = value => (value === null || value === undefined ? "" : value);

  function setHidden(root, selector, hidden) {
    const el = root && root.querySelector(selector);
    if (el) el.hidden = hidden;
  }

  /* ---------- offline note (shared by the live-only gate and the static-demo loading/error states) ---------- */

  function screenNote(root) {
    let note = root.querySelector("[data-trade-live-note]");
    if (!note) {
      note = document.createElement("p");
      note.className = "t-body muted";
      note.setAttribute("data-trade-live-note", "");
      const titleRow = root.querySelector(".title-row");
      if (titleRow && titleRow.parentNode) titleRow.parentNode.insertBefore(note, titleRow.nextSibling);
      else root.insertBefore(note, root.firstChild);
    }
    return note;
  }

  function setConfirmNote(section, message) {
    const el = section.querySelector("[data-trade-confirm-note]");
    if (el) el.textContent = message;
  }

  /* ---------- offline/static demo: no live API, so no drafting from text and no confirming; the report
     card still shows a worked example (Robert + NVDA to 10 %, funded from ZST.TO), read from its own file,
     exactly as the API-connected screen would show a confirmed trade's report. ---------- */

  function paintStaticDemo(section, root) {
    screenNote(root).hidden = true;
    setHidden(root, '[data-trade="confirm"]', false);
    ensureConfirmPainted(section);
    setConfirmNote(section, CONFIRM_NOTE_DEMO);
    const confirmBtn = section.querySelector('[data-trade-confirm-form] button[type="submit"]');
    if (confirmBtn) confirmBtn.disabled = true;
    renderReport(section, screen.report);
  }

  function renderStaticDemo(section) {
    const root = section.querySelector(".scr");
    if (!root) return;
    setHidden(root, '[data-trade="draft"]', true);       // no live text drafting without the API
    if (screen.report) {
      paintStaticDemo(section, root);
      return;
    }
    setHidden(root, '[data-trade="confirm"]', true);
    setHidden(root, '[data-trade="report"]', true);
    if (screen.demoError) {
      screenNote(root).hidden = false;
      screenNote(root).textContent = screen.demoError;
      return;
    }
    screenNote(root).hidden = false;
    screenNote(root).textContent = STATIC_DEMO_LOADING_MESSAGE;
    if (screen.demoRequested) return;
    screen.demoRequested = true;
    fetch(STATIC_DEMO_PATH, {cache: "no-store"}).then(response => {
      if (!response.ok) throw new Error("HTTP " + response.status);
      return response.json();
    }).then(report => {
      if (!report || !report.intent) throw new Error("the report has no draft trade (`intent`)");
      screen.draft = report.intent;
      screen.report = report;
      App.renderers[ROUTE](section);
    }).catch(error => {
      screen.demoError = LIVE_ONLY_MESSAGE + " (" + (error && error.message ? error.message : String(error)) + ".)";
      App.renderers[ROUTE](section);
    });
  }

  /* ---------- draft errors ---------- */

  function showDraftError(section, message) {
    const el = section.querySelector("[data-trade-draft-error]");
    if (!el) return;
    el.textContent = message || "";
    el.hidden = !message;
  }

  /* ---------- confirm card: fields built fresh per draft, and an error slot we add ourselves
     (the stub markup has none for this card, unlike the draft card). ---------- */

  function ensureConfirmError(section) {
    const card = section.querySelector('[data-trade="confirm"]');
    if (!card) return null;
    let el = card.querySelector("[data-trade-confirm-error]");
    if (!el) {
      el = document.createElement("p");
      el.className = "field__error";
      el.setAttribute("data-trade-confirm-error", "");
      el.setAttribute("role", "alert");
      el.hidden = true;
      const form = card.querySelector("[data-trade-confirm-form]");
      if (form && form.parentNode) form.parentNode.insertBefore(el, form);
      else card.appendChild(el);
    }
    return el;
  }

  function showConfirmError(section, message) {
    const el = ensureConfirmError(section);
    if (!el) return;
    el.textContent = message || "";
    el.hidden = !message;
  }

  function fundingSummary(funding) {
    if (!funding) return "Not stated";
    const extra = funding.asset_class ? "asset class " + funding.asset_class
      : funding.amount_cad != null ? "$" + funding.amount_cad + " CAD"
      : funding.ticker || "";
    return funding.kind + (extra ? " · " + extra : "");
  }

  function tickerFieldHtml(draft) {
    const value = String(v(draft.ticker));
    const assets = screen.universe && Array.isArray(screen.universe.assets) ? screen.universe.assets : null;
    if (assets && assets.length) {
      const options = ['<option value="">Choose a ticker…</option>'].concat(assets.map(asset => {
        const ticker = String(asset.ticker || "");
        return '<option value="' + esc(ticker) + '"' + (ticker === value ? " selected" : "") + '>'
          + esc(ticker + (asset.name ? " — " + asset.name : "")) + "</option>";
      }));
      return '<label class="field"><span class="t-small label">Ticker</span>'
        + '<select class="input" name="ticker" data-trade-ticker-input>' + options.join("") + "</select></label>";
    }
    return '<label class="field"><span class="t-small label">Ticker</span>'
      + '<input class="input" type="text" name="ticker" data-trade-ticker-input autocomplete="off" value="'
      + esc(value) + '"></label>';
  }

  function weightFieldHtml(draft) {
    return '<label class="field"><span class="t-small label">Target weight (%)</span>'
      + '<input class="input" type="number" step="any" min="0" name="target_weight_pct" data-trade-weight-input value="'
      + esc(v(draft.target_weight_pct)) + '"></label>';
  }

  /* Only `funding.kind === "specific"` gets an editable field (the Robert/NVDA fixture's shape);
     any other kind, or none stated, is shown as a fact and carried through unedited on confirm. */
  function fundingFieldsHtml(funding) {
    if (!funding || funding.kind === "specific") {
      return '<label class="field"><span class="t-small label">Funded by (ticker)</span>'
        + '<input class="input" type="text" name="funding_ticker" data-trade-funding-ticker autocomplete="off" value="'
        + esc(v(funding ? funding.ticker : null)) + '" placeholder="e.g. ZST.TO"></label>';
    }
    return '<div class="field"><span class="t-small label">Funding</span>'
      + '<p class="t-body" data-trade-funding-raw>' + esc(fundingSummary(funding)) + "</p></div>";
  }

  function renderConfirmFields(section, draft) {
    const container = section.querySelector("[data-trade-confirm-fields]");
    if (!container) return;
    container.innerHTML = tickerFieldHtml(draft) + weightFieldHtml(draft) + fundingFieldsHtml(draft.funding);
  }

  function ensureConfirmPainted(section) {
    const container = section.querySelector("[data-trade-confirm-fields]");
    if (!container) return;
    if (screen.paintedDraft === screen.draft && container.children.length) return;
    screen.paintedDraft = screen.draft;
    renderConfirmFields(section, screen.draft);
  }

  /* ---------- universe (optional ticker select), read-only; a failed fetch is silently ignored ---------- */

  function loadUniverse(section) {
    if (screen.universeTried || !App.state.persona || !App.source.universe) return;
    screen.universeTried = true;
    App.source.universe(App.state.persona).then(data => {
      if (data && Array.isArray(data.assets) && data.assets.length) {
        screen.universe = data;
        if (screen.draft) {
          screen.paintedDraft = null;      // force one repaint to switch the ticker input to a select
          ensureConfirmPainted(section);
        }
      }
    }).catch(() => {});
  }

  /* ---------- report card ---------- */

  function chipStatus(checks) {
    return App.worstStatus((checks || []).map(check => check.status));
  }

  function floorFromChecks(checks) {
    const h9 = (checks || []).find(check => check.id === "H9");
    return h9 ? h9.limit : null;
  }

  /* The before/after cone (SPEC §12/§20.7): `metrics_current` ("Before") and, when present,
     `metrics_proposed` ("After") overlaid on one grid, both p5-p95 bands drawn in full (the shared
     `App.charts.fan` only overlays one full band plus a single compare line, so Flow B draws its own
     SVG here to keep both cones complete and labelled). Returns an SVG markup string, or null when
     there is no current cone to draw. */
  function coneMarkup(report) {
    const curCone = (report.metrics_current || {}).cone;
    if (!curCone || !Array.isArray(curCone.p50) || curCone.p50.length < 2) return null;
    const propCone = report.metrics_proposed && report.metrics_proposed.cone;
    const n = curCone.p50.length;
    const floor = App.format.num(floorFromChecks(report.checks_proposed) ?? floorFromChecks(report.checks_current), null);
    const ink = "#172b3f", muted = "#5c6b7a", red = "#e41c23";
    const W = 720, H = 300, L = 54, R = propCone ? 170 : 130, T = 16, B = 30;
    let all = [].concat(curCone.p5, curCone.p95);
    if (propCone) all = all.concat(propCone.p5, propCone.p95);
    if (floor !== null) all = all.concat([floor]);
    const lo = Math.min.apply(null, all), hi = Math.max.apply(null, all);
    const span = (hi - lo) || 1;
    const x = i => L + (i / (n - 1)) * (W - L - R);
    const y = val => T + (1 - (val - lo) / span) * (H - T - B);
    const pts = series => series.map((val, i) => x(i).toFixed(1) + "," + y(val).toFixed(1)).join(" ");
    const bandPath = (top, bottom) => "M" + top.map((val, i) => x(i).toFixed(1) + "," + y(val).toFixed(1)).join(" L")
      + " L" + bottom.slice().reverse().map((val, i) => x(bottom.length - 1 - i).toFixed(1) + "," + y(val).toFixed(1)).join(" L") + " Z";
    const end = (val, label, color) => '<circle cx="' + x(n - 1).toFixed(1) + '" cy="' + y(val).toFixed(1) + '" r="4" fill="' + color
      + '"/><text x="' + (x(n - 1) + 10).toFixed(1) + '" y="' + (y(val) + 4).toFixed(1) + '" fill="' + color + '">'
      + App.esc(label) + "</text>";
    const last = series => series[series.length - 1];
    let g = "";
    if (floor !== null) {
      g += '<line x1="' + L + '" x2="' + (W - R) + '" y1="' + y(floor).toFixed(1) + '" y2="' + y(floor).toFixed(1)
        + '" stroke="' + red + '" stroke-width="1.5" stroke-dasharray="6 5"/>'
        + '<text x="' + (L + 6) + '" y="' + (y(floor) - 7).toFixed(1) + '" fill="' + red + '">Floor '
        + App.esc(App.format.amount(floor)) + "</text>";
    }
    g += '<path d="' + bandPath(curCone.p95, curCone.p5) + '" fill="' + ink + '" fill-opacity="' + (propCone ? "0.08" : "0.14") + '"/>';
    g += '<polyline points="' + pts(curCone.p50) + '" fill="none" stroke="' + muted + '" stroke-width="2"'
      + (propCone ? ' stroke-dasharray="5 4"' : "") + "/>";
    g += '<polyline points="' + pts(curCone.p5) + '" fill="none" stroke="' + muted + '" stroke-width="1.3"'
      + (propCone ? ' stroke-dasharray="5 4"' : "") + "/>";
    let beforeAfter = "Before: pessimistic " + App.format.amount(last(curCone.p5)) + ", median "
      + App.format.amount(last(curCone.p50)) + ", optimistic " + App.format.amount(last(curCone.p95)) + ". ";
    if (propCone) {
      g += '<path d="' + bandPath(propCone.p95, propCone.p5) + '" fill="none" stroke="' + ink + '" stroke-width="1.5"/>';
      g += '<polyline points="' + pts(propCone.p50) + '" fill="none" stroke="' + ink + '" stroke-width="2.6"/>';
      g += '<polyline points="' + pts(propCone.p5) + '" fill="none" stroke="' + red + '" stroke-width="1.5"/>';
      g += end(last(propCone.p95), "After, optimistic " + App.format.amount(last(propCone.p95)), muted);
      g += end(last(propCone.p50), "After, median " + App.format.amount(last(propCone.p50)), ink);
      g += end(last(propCone.p5), "After, pessimistic " + App.format.amount(last(propCone.p5)), red);
      g += end(last(curCone.p50), "Before, median " + App.format.amount(last(curCone.p50)), muted);
      g += end(last(curCone.p5), "Before, pessimistic " + App.format.amount(last(curCone.p5)), muted);
      beforeAfter += "After: pessimistic " + App.format.amount(last(propCone.p5)) + ", median "
        + App.format.amount(last(propCone.p50)) + ", optimistic " + App.format.amount(last(propCone.p95)) + ".";
    } else {
      g += end(last(curCone.p95), "Optimistic " + App.format.amount(last(curCone.p95)), muted);
      g += end(last(curCone.p50), "Median " + App.format.amount(last(curCone.p50)), ink);
      g += end(last(curCone.p5), "Pessimistic " + App.format.amount(last(curCone.p5)), red);
    }
    const label = (propCone ? "Projected portfolio value, before and after the trade. " : "Projected portfolio value. ") + beforeAfter;
    return '<svg viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="' + App.esc(label) + '">' + g + "</svg>";
  }

  function drawChart(section, report) {
    const host = section.querySelector('[data-chart="trade"]');
    if (!host) return;
    const svg = coneMarkup(report);
    host.innerHTML = svg || '<p class="t-small muted">Chart unavailable</p>';
  }

  function renderHeadroom(section, report) {
    const el = section.querySelector('[data-trade="headroom"]');
    if (!el) return;
    const headroom = report.headroom;
    if (headroom && headroom.line) {
      el.textContent = headroom.line;
      el.hidden = false;
    } else {
      el.textContent = "";
      el.hidden = true;
    }
  }

  function renderChecks(section, report) {
    const el = section.querySelector('[data-trade="checks"]');
    if (!el) return;
    const checks = Array.isArray(report.checks_proposed) ? report.checks_proposed : [];
    el.innerHTML = checks.map(check => App.checkRow(check)).join("");
  }

  function renderReport(section, report) {
    const currentEl = section.querySelector('[data-trade="verdict-current"]');
    const proposedEl = section.querySelector('[data-trade="verdict-proposed"]');
    if (currentEl) currentEl.innerHTML = App.chip(chipStatus(report.checks_current));
    if (proposedEl) proposedEl.innerHTML = App.chip(chipStatus(report.checks_proposed));
    drawChart(section, report);
    renderHeadroom(section, report);
    renderChecks(section, report);
    const card = section.querySelector('[data-trade="report"]');
    if (card) card.hidden = false;
  }

  /* ---------- form handlers ---------- */

  function handleDraftSubmit(section, form) {
    showDraftError(section, "");
    const personaId = App.state.persona;
    const input = form.querySelector("[data-trade-draft-input]");
    const text = input ? input.value.trim() : "";
    if (!personaId) { showDraftError(section, "No profile is selected."); return; }
    if (!text) { showDraftError(section, "Describe the trade in your own words first."); return; }
    const submitBtn = form.querySelector('button[type="submit"]');
    if (submitBtn) submitBtn.disabled = true;
    App.source.requestDraft(personaId, text).then(res => {
      const intent = res && res.intent;
      if (!intent) throw new Error("The server did not return a draft intent.");
      screen.draft = intent;
      screen.report = null;
      showDraftError(section, "");
      const confirmCard = section.querySelector('[data-trade="confirm"]');
      if (confirmCard) confirmCard.hidden = false;
      const reportCard = section.querySelector('[data-trade="report"]');
      if (reportCard) reportCard.hidden = true;
      const confirmBtn = section.querySelector('[data-trade-confirm-form] button[type="submit"]');
      if (confirmBtn) confirmBtn.disabled = false;
      showConfirmError(section, "");
      ensureConfirmPainted(section);
    }).catch(error => {
      showDraftError(section, error && error.message ? error.message : String(error));
    }).finally(() => {
      if (submitBtn) submitBtn.disabled = false;
    });
  }

  function handleConfirmSubmit(section, form) {
    showConfirmError(section, "");
    if (App.source.mode !== "live") return;      /* static demo: read-only, nothing is ever sent */
    if (!screen.draft || !screen.draft.intent_id) {
      showConfirmError(section, "Describe a trade before confirming.");
      return;
    }
    const tickerEl = form.querySelector("[data-trade-ticker-input]");
    const weightEl = form.querySelector("[data-trade-weight-input]");
    const fundingTickerEl = form.querySelector("[data-trade-funding-ticker]");
    const ticker = tickerEl ? tickerEl.value.trim().toUpperCase() : "";
    const weight = weightEl ? Number(String(weightEl.value).trim()) : NaN;
    if (!ticker || !Number.isFinite(weight) || weight <= 0) {
      showConfirmError(section, "Enter a ticker and a target weight greater than 0 before confirming.");
      return;
    }
    let funding;
    if (fundingTickerEl) {
      const fundingTicker = fundingTickerEl.value.trim().toUpperCase();
      if (!fundingTicker) { showConfirmError(section, "Enter the ticker that funds this trade."); return; }
      funding = {kind: "specific", ticker: fundingTicker};
    } else if (screen.draft.funding) {
      funding = Object.assign({}, screen.draft.funding);
    } else {
      showConfirmError(section, "This trade has no funding source stated to confirm.");
      return;
    }
    const submitBtn = form.querySelector('button[type="submit"]');
    if (submitBtn) submitBtn.disabled = true;
    App.source.confirmIntent(screen.draft.intent_id, {ticker, target_weight_pct: weight, funding}).then(report => {
      screen.report = report;
      showConfirmError(section, "");
      renderReport(section, report);
      /* The button stays disabled: a second confirmation of the same intent is a 409. */
    }).catch(error => {
      showConfirmError(section, error && error.message ? error.message : String(error));
      if (submitBtn) submitBtn.disabled = false;
    });
  }

  /* ---------- wiring ---------- */

  function bindOnce(section) {
    if (section.dataset.tradeBound === "1") return;
    section.dataset.tradeBound = "1";
    section.addEventListener("submit", event => {
      const draftForm = event.target.closest("[data-trade-draft-form]");
      if (draftForm) {
        event.preventDefault();
        handleDraftSubmit(section, draftForm);
        return;
      }
      const confirmForm = event.target.closest("[data-trade-confirm-form]");
      if (confirmForm) {
        event.preventDefault();
        handleConfirmSubmit(section, confirmForm);
      }
    });
  }

  App.renderers.trade = function (section) {
    bindOnce(section);
    if (App.source.mode !== "live") {
      renderStaticDemo(section);
      return;
    }
    const root = section.querySelector(".scr");
    if (root) {
      const note = root.querySelector("[data-trade-live-note]");
      if (note) note.hidden = true;
    }
    setHidden(root, '[data-trade="draft"]', false);
    setConfirmNote(section, CONFIRM_NOTE_LIVE);
    loadUniverse(section);
    if (screen.draft) {
      setHidden(root, '[data-trade="confirm"]', false);
      ensureConfirmPainted(section);
    }
    if (screen.report) renderReport(section, screen.report);
  };
})();
