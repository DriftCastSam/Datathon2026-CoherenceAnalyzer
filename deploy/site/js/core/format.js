(() => {
  "use strict";

  /* Shared number helpers for the screens. */

  /* The value when it is a finite number, else the fallback. */
  function num(value, fallback) {
    return typeof value === "number" && Number.isFinite(value) ? value : fallback;
  }

  /* A CAD figure with a dollar sign and no decimals. */
  function amount(value) {
    return "$" + App.fmtCad(value);
  }

  App.format = {num, amount};
})();
