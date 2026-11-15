(() => {
  "use strict";

  const AMOUNT_DIGITS = 2;
  const MIN_AMOUNT = 0;

  /* Client-side only: answers live in this module and survive re-renders, never sent anywhere. */
  const answer = { choice: "", amount: null, draft: "" };

  App.navSpec.conversation = { tabs: [], chrome: "bare" };

  function formatAmount(value) {
    const digits = Number.isInteger(value) ? 0 : AMOUNT_DIGITS;
    return "$" + App.fmtCad(value, digits) + " CAD";
  }

  function formatDraft(value) {
    return App.fmtCad(value, Number.isInteger(value) ? 0 : AMOUNT_DIGITS);
  }

  function parseAmount(raw) {
    const cleaned = String(raw || "").replace(/[$\s,]/g, "").replace(/cad/gi, "");
    if (!cleaned) return null;
    const value = Number(cleaned);
    return Number.isFinite(value) && value > MIN_AMOUNT ? value : null;
  }

  function applyAnswer(section) {
    section.querySelectorAll("[data-choice]").forEach(button => {
      const selected = button.dataset.choice === answer.choice;
      button.setAttribute("aria-pressed", selected ? "true" : "false");
    });

    const chosen = section.querySelector("[data-chosen]");
    const chosenValue = section.querySelector("[data-chosen-value]");
    const text = answer.amount === null ? answer.choice : formatAmount(answer.amount);
    chosen.hidden = !text;
    chosenValue.textContent = text;

    const input = section.querySelector("[data-amount]");
    if (input && document.activeElement !== input) input.value = answer.draft;
    const error = section.querySelector("[data-amount-error]");
    if (error) error.hidden = true;
    if (input) input.removeAttribute("aria-invalid");
  }

  function chooseSuggestion(section, label) {
    answer.choice = label;
    answer.amount = null;
    answer.draft = "";
    const input = section.querySelector("[data-amount]");
    if (input) input.value = "";
    applyAnswer(section);
  }

  function submitAmount(section) {
    const input = section.querySelector("[data-amount]");
    const error = section.querySelector("[data-amount-error]");
    const value = parseAmount(input.value);
    if (value === null) {
      input.setAttribute("aria-invalid", "true");
      if (error) error.hidden = false;
      input.focus();
      return;
    }
    answer.amount = value;
    answer.choice = "";
    answer.draft = formatDraft(value);
    applyAnswer(section);
    input.value = answer.draft;
  }

  function bindOnce(section) {
    if (section.dataset.conversationBound === "1") return;
    section.dataset.conversationBound = "1";

    section.addEventListener("click", event => {
      const suggestion = event.target.closest("[data-choice]");
      if (suggestion) {
        chooseSuggestion(section, suggestion.dataset.choice);
        return;
      }
      if (event.target.closest("[data-skip]")) App.go("#/coherence");
    });

    const form = section.querySelector("[data-amount-form]");
    form.addEventListener("submit", event => {
      event.preventDefault();
      submitAmount(section);
    });

    section.querySelector("[data-amount]").addEventListener("input", event => {
      answer.draft = event.target.value;
      event.target.removeAttribute("aria-invalid");
      const error = section.querySelector("[data-amount-error]");
      if (error) error.hidden = true;
    });
  }

  App.renderers.conversation = function (section) {
    bindOnce(section);
    applyAnswer(section);
  };
})();
