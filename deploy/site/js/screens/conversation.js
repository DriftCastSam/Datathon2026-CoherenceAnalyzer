(() => {
  "use strict";

  const {KIND, KEEP_VALUE, specFor} = App.intakeSpec;
  const SUMMARY_HASH = "#/summary";
  const CONVERSATION_ROUTE = "conversation";
  const MODE_LIVE = "live";
  const BOOLEAN_WORDS = {yes: true, y: true, true: true, no: false, n: false, false: false};
  const NONE_WORDS = ["none", "no", "nothing", "empty"];
  const NUMBER_NOISE = /[$\s%]|cad|years?|yrs?/gi;
  const THOUSANDS_FORM = /^-?\d{1,3}(,\d{3})+(\.\d+)?$/;
  const COMMA_DECIMAL_FORM = /^-?\d+,\d{1,2}$/;
  const AMBIGUOUS_NUMBER = "Enter a number with a dot for decimals and commas only for thousands (for example 25,000 or 1.5).";
  const STATIC_NOTICE = "Offline demo: answers are not analysed; showing the prebuilt analysis.";
  const RUN_LABEL = "Run analysis";
  const RUN_BUSY_LABEL = "Running analysis…";
  const STATIC_RUN_LABEL = "Go to the analysis";
  const STATE_ANSWERED = "answered";
  const STATE_SKIPPED = "skipped";
  const STATE_OPEN = "open";
  const DONE_ALL_ANSWERED = "All questions have an answer.";
  const DONE_RUN_PROMPT = "Run the analysis to check your profile against your portfolio.";

  /* Screen state survives re-renders; it is rebuilt when the persona changes or the screen is re-entered. */
  const screen = {
    persona: null, questions: [], index: -1, busy: false, saving: false,
    loading: false, error: "", fieldError: "", runError: ""
  };
  /* Only the most recent question load for the current persona may fill the screen. */
  let loadSeq = 0;
  let activeSection = null;
  let pathSelectionApplied = false;

  App.navSpec.conversation = {tabs: [], chrome: "bare"};

  const esc = value => App.esc(value);
  const isLive = () => App.source.mode === MODE_LIVE;

  /* ---------- value formatting ---------- */

  function enumLabel(spec, value) {
    const chip = spec.chips.find(item => item.value === value);
    return chip ? chip.label : String(value).replace(/_/g, " ");
  }

  function listText(value) {
    if (!value.length) return "None";
    const names = value.map(item => (item && typeof item === "object" ? item.name || item.purpose || item.date : item));
    return names.map(name => String(name).replace(/_/g, " ")).join("; ");
  }

  function displayValue(spec, value) {
    if (Array.isArray(value)) return listText(value);
    if (typeof value === "boolean") return value ? "Yes" : "No";
    if (spec.kind === KIND.ENUM) return enumLabel(spec, value);
    if (spec.kind === KIND.AMOUNT) return "$" + App.fmtCad(value, Number.isInteger(value) ? 0 : 2) + " CAD";
    if (spec.unit === "years") return value + (value === 1 ? " year" : " years");
    if (spec.unit === "drawdown" || spec.unit === "percent") return value + "%";
    return String(value);
  }

  /* ---------- input parsing ---------- */

  function parseEnum(spec, raw) {
    const wanted = raw.toLowerCase().replace(/[\s_]+/g, " ");
    const chip = spec.chips.find(item => item.label.toLowerCase() === wanted
      || String(item.value).replace(/_/g, " ") === wanted);
    if (chip) return {value: chip.value};
    return {error: "Choose one of the suggestions: " + spec.chips.map(item => item.label).join(", ") + "."};
  }

  /* "25,000" is thousands, a single trailing ",5" or ",25" is a decimal comma; anything else
     with a comma is rejected rather than guessed. */
  function normalizeNumberText(cleaned) {
    if (!cleaned.includes(",")) return {text: cleaned};
    if (THOUSANDS_FORM.test(cleaned)) return {text: cleaned.replace(/,/g, "")};
    if (COMMA_DECIMAL_FORM.test(cleaned)) return {text: cleaned.replace(",", ".")};
    return {error: AMBIGUOUS_NUMBER};
  }

  function parseNumber(spec, raw) {
    const normalized = normalizeNumberText(raw.replace(NUMBER_NOISE, ""));
    if (normalized.error) return normalized;
    let value = normalized.text === "" ? NaN : Number(normalized.text);
    if (!Number.isFinite(value)) return {error: "Enter a number."};
    if (spec.negateOnInput && value > 0) value = -value;
    if (spec.integer && !Number.isInteger(value)) return {error: "Enter a whole number."};
    if (spec.min !== undefined && value < spec.min) return {error: "Enter " + spec.min + " or more."};
    if (spec.exclusiveMin !== undefined && value <= spec.exclusiveMin) {
      return {error: "Enter a number greater than " + spec.exclusiveMin + "."};
    }
    if (spec.max !== undefined && value > spec.max) return {error: "Enter " + spec.max + " or less."};
    return {value};
  }

  function parseBoolean(raw) {
    const word = raw.toLowerCase();
    if (Object.prototype.hasOwnProperty.call(BOOLEAN_WORDS, word)) return {value: BOOLEAN_WORDS[word]};
    return {error: "Answer yes or no."};
  }

  function parseList(raw) {
    if (NONE_WORDS.includes(raw.toLowerCase())) return {value: []};
    const items = raw.split(/[,;]/).map(item => item.trim().toLowerCase().replace(/\s+/g, "_")).filter(Boolean);
    return items.length ? {value: items} : {error: "List the items separated by commas, or answer None."};
  }

  function parseInput(spec, raw) {
    const text = String(raw || "").trim();
    if (!text) return {error: "Type an answer or pick a suggestion."};
    if (spec.kind === KIND.ENUM) return parseEnum(spec, text);
    if (spec.kind === KIND.NUMBER || spec.kind === KIND.AMOUNT) return parseNumber(spec, text);
    if (spec.kind === KIND.BOOLEAN) return parseBoolean(text);
    if (spec.kind === KIND.LIST) return parseList(text);
    return {value: text};
  }

  /* ---------- question state ---------- */

  function questionState(question) {
    if (question.skipped) return STATE_SKIPPED;
    return question.current === null || question.current === undefined ? STATE_OPEN : STATE_ANSWERED;
  }

  function firstOpenAfter(start) {
    const total = screen.questions.length;
    for (let step = 1; step <= total; step += 1) {
      const position = (start + step + total) % total;
      if (questionState(screen.questions[position]) === STATE_OPEN) return position;
    }
    return -1;
  }

  async function loadQuestions() {
    const personaId = App.state.persona;
    loadSeq += 1;
    const token = loadSeq;
    Object.assign(screen, {
      persona: personaId, questions: [], index: -1, busy: false, saving: false,
      loading: true, error: "", fieldError: "", runError: ""
    });
    try {
      const result = await App.source.intake(personaId, App.state.report);
      if (token !== loadSeq) return;
      screen.questions = result.questions.map(question => ({
        ...question,
        skipped: Boolean(question.skipped)
      }));
      screen.index = firstOpenAfter(-1);
    } catch (error) {
      if (token !== loadSeq) return;
      console.error("[conversation] could not load the questions", error);
      screen.questions = [];
      screen.error = "Could not load the questions: " + error.message;
    }
    screen.loading = false;
  }

  /* Saves (or skips) the current question through the data source, then moves on. */
  async function commit(action, failure) {
    const question = screen.questions[screen.index];
    if (screen.saving || !question) return;
    const token = loadSeq;
    screen.saving = true;
    screen.fieldError = "";
    render();
    try {
      await action(question);
      if (token === loadSeq) screen.index = firstOpenAfter(-1);
    } catch (error) {
      console.error("[conversation] " + failure, error);
      if (token === loadSeq) screen.fieldError = "Could not " + failure + ": " + error.message;
    } finally {
      if (token === loadSeq) {
        screen.saving = false;
        render();
      }
    }
  }

  function saveAnswer(value) {
    return commit(async question => {
      if (value === KEEP_VALUE) return;
      await App.source.answer(screen.persona, question.path, value);
      question.current = value;
      question.skipped = false;
    }, "save the answer");
  }

  function skipQuestion() {
    return commit(async question => {
      await App.source.skip(screen.persona, question.path);
      question.current = null;
      question.skipped = true;
    }, "skip the question");
  }

  function submitInput(section) {
    const question = screen.questions[screen.index];
    const input = section.querySelector("[data-answer-input]");
    if (!question || !input) return;
    const parsed = parseInput(specFor(question.path), input.value);
    if (parsed.error) {
      screen.fieldError = parsed.error;
      render();
      return;
    }
    saveAnswer(parsed.value);
  }

  /* ---------- rendering ---------- */

  function chipHtml(chip, position) {
    return '<button class="btn btn--outline-dark sug" type="button" data-chip="' + position + '"'
      + (screen.saving ? " disabled" : "") + ">" + esc(chip.label) + "</button>";
  }

  function inputHtml(spec) {
    if (spec.readOnlyList) return "";
    return '<form class="field" data-answer-form novalidate>'
      + '<label class="sr-only" for="conv-answer">Your answer</label>'
      + '<input class="field__input" id="conv-answer" type="text" autocomplete="off" data-answer-input'
      + (spec.placeholder ? ' placeholder="' + esc(spec.placeholder) + '"' : "")
      + (screen.fieldError ? ' aria-invalid="true"' : "") + ">"
      + '<button class="btn btn--primary" type="submit"' + (screen.saving ? " disabled" : "") + ">Send</button></form>";
  }

  function currentHtml() {
    const question = screen.questions[screen.index];
    const spec = specFor(question.path);
    const hasAnswer = questionState(question) !== STATE_OPEN;
    const chips = spec.chips.filter(chip => chip.value !== KEEP_VALUE || hasAnswer);
    const readOnly = spec.readOnlyList && Array.isArray(question.current)
      ? '<p class="t-body current__readonly">Current: ' + esc(listText(question.current)) + "</p>" : "";
    return '<p class="t-small label">Question ' + (screen.index + 1) + " of " + screen.questions.length + " · required</p>"
      + '<p class="current__q">' + esc(question.text) + "</p>" + readOnly
      + '<div class="sugs" role="group" aria-label="Suggested answers">'
      + chips.map(chip => chipHtml(chip, spec.chips.indexOf(chip))).join("") + "</div>"
      + inputHtml(spec)
      + '<p class="field__error" role="alert"' + (screen.fieldError ? "" : " hidden") + ">" + esc(screen.fieldError) + "</p>"
      + '<div class="current__actions"><button class="btn btn--outline-dark" type="button" data-skip-question'
      + (screen.saving ? " disabled" : "") + ">Skip this question</button></div>";
  }

  function doneHtml() {
    return '<p class="t-body-strong">' + esc(DONE_ALL_ANSWERED) + "</p>"
      + '<p class="t-body">' + esc(DONE_RUN_PROMPT) + "</p>";
  }

  function renderCurrent(section) {
    const container = section.querySelector("[data-conversation-current]");
    if (!isLive()) {
      container.innerHTML = "";
    } else if (screen.loading) {
      container.innerHTML = '<p class="t-body muted">Loading the questions…</p>';
    } else if (screen.error) {
      container.innerHTML = '<p class="field__error" role="alert">' + esc(screen.error) + "</p>";
    } else {
      container.innerHTML = screen.index < 0 ? doneHtml() : currentHtml();
    }
    const input = container.querySelector("[data-answer-input]");
    if (input && screen.fieldError) input.focus();
  }

  function rowValue(question, position) {
    const state = questionState(question);
    if (position === screen.index) return ["Answering now", "prow__val--now"];
    if (state === STATE_OPEN) return ["Required", "prow__val--opt"];
    if (state === STATE_SKIPPED) return ["Skipped", "prow__val--opt"];
    return [displayValue(specFor(question.path), question.current), "prow__val--done"];
  }

  function panelRow(question, position) {
    const [text, cls] = rowValue(question, position);
    const label = esc(specFor(question.path).label);
    const value = '<p class="prow__val ' + cls + '">' + esc(text) + "</p>";
    if (!isLive()) return '<div class="prow"><p>' + label + "</p>" + value + "</div>";
    return '<button type="button" class="prow prow--btn" data-edit="' + position + '"><p>' + label + "</p>" + value + "</button>";
  }

  function renderPanel(section) {
    const total = screen.questions.length;
    const done = screen.questions.filter(question => questionState(question) !== STATE_OPEN).length;
    section.querySelector("[data-profile-count]").textContent = done + " of " + total + " required answers";
    section.querySelector("[data-profile-progress]").innerHTML = screen.questions
      .map(question => '<span class="prog__seg' + (questionState(question) === STATE_OPEN ? "" : " prog__seg--on") + '"></span>')
      .join("");
    section.querySelector("[data-profile-rows]").innerHTML = screen.questions.map(panelRow).join("");
  }

  function renderRun(section) {
    const button = section.querySelector("[data-run]");
    button.textContent = screen.busy ? RUN_BUSY_LABEL : (isLive() ? RUN_LABEL : STATIC_RUN_LABEL);
    button.disabled = screen.busy;
    button.setAttribute("aria-busy", screen.busy ? "true" : "false");
    const error = section.querySelector("[data-run-error]");
    error.hidden = !screen.runError;
    error.textContent = screen.runError;
    const notice = section.querySelector("[data-conversation-notice]");
    notice.hidden = isLive();
    notice.textContent = isLive() ? "" : STATIC_NOTICE;
  }

  function render() {
    if (!activeSection) return;
    renderCurrent(activeSection);
    renderPanel(activeSection);
    renderRun(activeSection);
  }

  /* ---------- run analysis ---------- */

  async function runAnalysis() {
    if (!isLive()) {
      App.go(SUMMARY_HASH);
      return;
    }
    if (screen.busy) return;
    const personaId = App.state.persona;
    const token = loadSeq;
    screen.busy = true;
    screen.runError = "";
    render();
    try {
      const report = await App.source.rerun(personaId, App.state.entryFile);
      if (App.source.isFallback(report)) throw new Error("the live analysis failed; see the server log");
      if (token !== loadSeq || App.state.persona !== personaId) return;
      screen.busy = false;
      App.setReport(report);
      App.go(SUMMARY_HASH);
    } catch (error) {
      console.error("[conversation] analysis failed", error);
      if (token !== loadSeq || App.state.persona !== personaId) return;
      screen.busy = false;
      screen.runError = "The analysis could not be run: " + error.message;
      render();
    }
  }

  /* ---------- wiring ---------- */

  function onClick(event) {
    const edit = event.target.closest("[data-edit]");
    const chip = event.target.closest("[data-chip]");
    if (edit && !screen.saving) {
      screen.index = Number(edit.dataset.edit);
      screen.fieldError = "";
      render();
    } else if (chip && !screen.saving) {
      const spec = specFor(screen.questions[screen.index].path);
      saveAnswer(spec.chips[Number(chip.dataset.chip)].value);
    } else if (event.target.closest("[data-skip-question]")) {
      skipQuestion();
    } else if (event.target.closest("[data-run]")) {
      runAnalysis();
    }
  }

  function bindOnce(section) {
    if (section.dataset.conversationBound === "1") return;
    section.dataset.conversationBound = "1";
    section.addEventListener("click", onClick);
    section.addEventListener("submit", event => {
      event.preventDefault();
      submitInput(section);
    });
    window.addEventListener("hashchange", () => {
      pathSelectionApplied = false;
      if (App.route !== CONVERSATION_ROUTE) screen.persona = null;
    });
  }

  function applyPathSelection() {
    if (pathSelectionApplied) return;
    const pathParam = App.routeParams().get("path");
    if (!pathParam || !screen.questions.length) return;
    const matchIndex = screen.questions.findIndex(question => question.path === pathParam);
    if (matchIndex >= 0) {
      screen.index = matchIndex;
      screen.fieldError = "";
      pathSelectionApplied = true;
    }
  }

  App.renderers.conversation = function (section) {
    bindOnce(section);
    activeSection = section;
    const needsLoad = Boolean(App.state.persona) && screen.persona !== App.state.persona;
    if (needsLoad) {
      pathSelectionApplied = false;
      loadQuestions().then(() => {
        applyPathSelection();
        render();
      });
    } else {
      if (!pathSelectionApplied) applyPathSelection();
      render();
    }
  };
})();
