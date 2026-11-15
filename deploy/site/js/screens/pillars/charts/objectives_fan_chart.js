(function () {
  "use strict";

  /* ---------- projected value fan chart ---------- */

  function niceStep(raw) {
    const safe = Math.max(raw, 1);
    const power = Math.pow(10, Math.floor(Math.log10(safe)));
    const candidates = [1, 2, 2.5, 5, 10];
    for (let index = 0; index < candidates.length; index += 1) {
      if (candidates[index] * power >= safe) return candidates[index] * power;
    }
    return 10 * power;
  }

  function linePath(points) {
    return points.map((point, index) => (index ? "L" : "M") + point[0].toFixed(1) + " "
      + point[1].toFixed(1)).join("");
  }

  function bandPath(top, bottom) {
    return linePath(top) + linePath(bottom.slice().reverse()).replace(/^M/, "L") + "Z";
  }

  function fanChart(state, fmt) {
    const {compact, amount} = fmt;
    const cone = state.cone;
    const total = cone.p50.length;
    const keys = ["p5", "p25", "p50", "p75", "p95"];
    const values = [];
    keys.forEach(key => {
      (cone[key] || []).forEach(value => {
        if (isFinite(value)) values.push(value);
      });
    });
    values.push(state.start);
    if (state.floor !== null) values.push(state.floor);
    const low = Math.min.apply(null, values);
    const high = Math.max.apply(null, values);
    const spread = high - low || Math.abs(high) || 1;
    const step = niceStep(spread / 5);
    const padding = spread * 0.02;
    const axisMin = Math.floor((low - padding) / step) * step;
    const axisMax = Math.ceil((high + padding) / step) * step;
    const left = 52;
    const right = 1050;
    const top = 20;
    const bottom = 255;
    const yOf = value => bottom - ((value - axisMin) / ((axisMax - axisMin) || 1)) * (bottom - top);
    const xOf = index => left + (index / (total - 1)) * (right - left);
    const points = key => cone[key].map((value, index) => [xOf(index), yOf(value)]);
    const grid = [];
    const ticks = Math.max(1, Math.round((axisMax - axisMin) / step));
    for (let index = 0; index <= ticks; index += 1) {
      const value = axisMin + index * step;
      const y = yOf(value).toFixed(1);
      grid.push('<line class="fan__grid" x1="' + left + '" y1="' + y + '" x2="' + right + '" y2="' + y + '"/>');
      grid.push('<text class="fan__tick" x="42" y="' + y + '" text-anchor="end" dominant-baseline="middle">'
        + App.esc(compact(value)) + "</text>");
    }
    const years = Math.max(1, Math.round(state.months / 12));
    const yearLabels = ['<text class="fan__x" x="' + left + '" y="277" text-anchor="start">Today</text>'];
    for (let index = 1; index <= years; index += 1) {
      const x = xOf((index / years) * (total - 1));
      yearLabels.push('<text class="fan__x" x="' + x.toFixed(1) + '" y="277" text-anchor="middle">Year '
        + index + "</text>");
    }
    const p95Y = yOf(state.p95);
    const p50Y = yOf(state.p50);
    const p5Y = yOf(state.p5);
    const seriesLabel = (text, y, color) => '<text class="fan__series" x="1064" y="' + y.toFixed(1)
      + '" dominant-baseline="middle" fill="' + color + '">' + App.esc(text) + "</text>";
    const floorY = state.floor === null ? null : yOf(state.floor);
    const floorLabelY = floorY === null ? 0 : Math.abs(floorY + 13 - p5Y) < 18 ? floorY - 7 : floorY + 13;
    const floorLine = floorY === null ? "" : (
      '<line x1="' + left + '" y1="' + floorY.toFixed(1) + '" x2="' + right + '" y2="'
      + floorY.toFixed(1) + '" stroke="#e41c23" stroke-width="1.5" stroke-dasharray="3 5"/>'
      + '<text class="fan__series" x="1042" y="' + floorLabelY.toFixed(1)
      + '" text-anchor="end" fill="#e41c23" font-size="12">Floor ' + App.esc(compact(state.floor)) + "</text>");
    const svg = '<svg viewBox="0 0 1200 300" role="img" aria-label="Projected portfolio value at year '
      + App.esc(years) + ": optimistic " + App.esc(amount(state.p95)) + ", median " + App.esc(amount(state.p50))
      + ", pessimistic " + App.esc(amount(state.p5)) + '.">'
      + grid.join("")
      + '<path d="' + bandPath(points("p95"), points("p5")) + '" fill="#172b3f" fill-opacity=".08"/>'
      + '<path d="' + bandPath(points("p75"), points("p25")) + '" fill="#172b3f" fill-opacity=".16"/>'
      + floorLine
      + '<path d="' + linePath(points("p5")) + '" fill="none" stroke="#e41c23" stroke-width="1.8" stroke-linejoin="round"/>'
      + '<path d="' + linePath(points("p50")) + '" fill="none" stroke="#172b3f" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>'
      + '<circle cx="' + right + '" cy="' + p95Y.toFixed(1) + '" r="4.5" fill="#55637a"/>'
      + '<circle cx="' + right + '" cy="' + p50Y.toFixed(1) + '" r="5" fill="#172b3f"/>'
      + '<circle cx="' + right + '" cy="' + p5Y.toFixed(1) + '" r="4.5" fill="#e41c23"/>'
      + yearLabels.join("")
      + seriesLabel("Optimistic " + compact(state.p95), p95Y, "#5c6b7a")
      + seriesLabel("Median " + compact(state.p50), p50Y, "#172b3f")
      + seriesLabel("Pessimistic " + compact(state.p5), p5Y, "#e41c23")
      + "</svg>";
    return '<div class="fan" data-chart="objectives">' + svg + "</div>";
  }

  App._objectivesChart = {fanChart};
})();
