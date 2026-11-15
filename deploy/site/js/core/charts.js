(function () {
  "use strict";

  /* Shared projection fan chart: App.charts.fan(el, opts) -> boolean.
     opts: {cone: {p5,p25,p50,p75,p95: number[]}, compareMedian?: number[],
            floor?: number, years: number, asOf?: string,
            labels?: {median, optimistic, pessimistic}}
     Series start at today's value; arrays may differ in length (each spans the
     full x range). Returns false and leaves el untouched without a usable cone. */

  const VIEW = {width: 1200, height: 300, left: 84, right: 940, top: 20, bottom: 255};
  const LABEL_X = 954;
  const MIN_LABEL_GAP = 26;
  const FLOOR_LABEL_POSITION = 0.4;
  const FLOOR_LABEL_RISE = 5;
  const TICK_TARGET = 5;
  const MAX_YEAR_TICKS = 5;
  const STEP_CANDIDATES = [1, 2, 2.5, 5, 10];
  const COLOR = {ink: "#172b3f", muted: "#5c6b7a", red: "#e41c23", compare: "#55637a"};
  const DEFAULT_LABELS = {
    median: "Median", optimistic: "Optimistic", pessimistic: "Pessimistic"
  };
  const BAND_KEYS = ["p5", "p25", "p50", "p75", "p95"];

  function isSeries(value) {
    return Array.isArray(value) && value.length > 1 && value.every(Number.isFinite);
  }

  function compact(value) {
    const abs = Math.abs(value);
    if (abs >= 1e6) return String(Math.round(value / 1e4) / 100) + "M";
    if (abs >= 1e3) return App.fmtCad(Math.round(value / 1e3)) + "k";
    return App.fmtCad(Math.round(value));
  }

  function niceStep(raw) {
    const safe = Math.max(raw, 1);
    const power = Math.pow(10, Math.floor(Math.log10(safe)));
    const match = STEP_CANDIDATES.find(candidate => candidate * power >= safe);
    return (match || 10) * power;
  }

  function linePath(points) {
    return points.map((point, index) => (index ? "L" : "M") + point[0].toFixed(1)
      + " " + point[1].toFixed(1)).join("");
  }

  function bandPath(top, bottom) {
    return linePath(top) + linePath(bottom.slice().reverse()).replace(/^M/, "L") + "Z";
  }

  function axisFor(seriesList, floor) {
    const values = [].concat.apply([], seriesList);
    if (floor !== null) values.push(floor);
    const low = Math.min.apply(null, values);
    const high = Math.max.apply(null, values);
    const spread = high - low || Math.abs(high) || 1;
    const step = niceStep(spread / TICK_TARGET);
    const padding = spread * 0.02;
    return {
      step,
      min: Math.floor((low - padding) / step) * step,
      max: Math.ceil((high + padding) / step) * step
    };
  }

  function gridSvg(axis, yOf) {
    const ticks = Math.max(1, Math.round((axis.max - axis.min) / axis.step));
    const parts = [];
    for (let index = 0; index <= ticks; index += 1) {
      const value = axis.min + index * axis.step;
      const y = yOf(value).toFixed(1);
      parts.push('<line class="fan__grid" x1="' + VIEW.left + '" y1="' + y
        + '" x2="' + VIEW.right + '" y2="' + y + '"/>');
      parts.push('<text class="fan__tick" x="74" y="' + y
        + '" text-anchor="end" dominant-baseline="middle">'
        + App.esc(compact(value)) + "</text>");
    }
    return parts.join("");
  }

  function yearLabelsSvg(years, xOf) {
    const parts = ['<text class="fan__x" x="' + VIEW.left
      + '" y="277" text-anchor="start">Today</text>'];
    const step = Math.ceil(years / MAX_YEAR_TICKS);
    const shown = [];
    for (let year = step; year < years; year += step) shown.push(year);
    if (shown.length && years - shown[shown.length - 1] < step / 2) shown.pop();
    shown.push(years);
    shown.forEach(year => parts.push('<text class="fan__x" x="' + xOf(year / years).toFixed(1)
      + '" y="277" text-anchor="middle">Year ' + year + "</text>"));
    return parts.join("");
  }

  function spreadLabelYs(entries) {
    const sorted = entries.slice().sort((first, second) => first.y - second.y);
    for (let index = 1; index < sorted.length; index += 1) {
      const previous = sorted[index - 1];
      if (sorted[index].labelY - previous.labelY < MIN_LABEL_GAP) {
        sorted[index].labelY = previous.labelY + MIN_LABEL_GAP;
      }
    }
    return sorted;
  }

  function endMarkersSvg(ends) {
    const entries = ends.map(end => Object.assign({labelY: end.y}, end));
    return spreadLabelYs(entries).map(end => '<circle cx="' + VIEW.right
      + '" cy="' + end.y.toFixed(1) + '" r="' + end.radius + '" fill="' + end.dot + '"/>'
      + '<text class="fan__series" x="' + LABEL_X + '" y="' + end.labelY.toFixed(1)
      + '" dominant-baseline="middle" fill="' + end.color + '">'
      + App.esc(end.text) + "</text>").join("");
  }

  function floorLabelX() {
    return VIEW.left + (VIEW.right - VIEW.left) * FLOOR_LABEL_POSITION;
  }

  function floorSvg(floor, yOf) {
    if (floor === null) return "";
    const y = yOf(floor);
    const labelY = y - FLOOR_LABEL_RISE;
    return '<line x1="' + VIEW.left + '" y1="' + y.toFixed(1) + '" x2="' + VIEW.right
      + '" y2="' + y.toFixed(1) + '" stroke="' + COLOR.red
      + '" stroke-width="1.5" stroke-dasharray="3 5"/>'
      + '<text class="fan__floor" x="' + floorLabelX() + '" y="'
      + labelY.toFixed(1) + '" text-anchor="middle" fill="' + COLOR.red
      + '">Floor ' + App.esc(compact(floor)) + "</text>";
  }

  function endMarkers(bands, labels, yOf) {
    const last = series => series[series.length - 1];
    const median = last(bands.p50);
    const ends = [{y: yOf(median), dot: COLOR.ink, color: COLOR.ink, radius: 5,
      text: labels.median + " " + compact(median)}];
    if (bands.p95) {
      ends.push({y: yOf(last(bands.p95)), dot: COLOR.compare, color: COLOR.muted,
        radius: 4.5, text: labels.optimistic + " " + compact(last(bands.p95))});
    }
    if (bands.p5) {
      ends.push({y: yOf(last(bands.p5)), dot: COLOR.red, color: COLOR.red,
        radius: 4.5, text: labels.pessimistic + " " + compact(last(bands.p5))});
    }
    return ends;
  }

  function describe(bands, years, asOf) {
    const last = series => App.fmtCad(Math.round(series[series.length - 1]));
    return "Projected portfolio value at year " + years
      + (bands.p95 ? ", optimistic " + last(bands.p95) : "")
      + ", median " + last(bands.p50)
      + (bands.p5 ? ", pessimistic " + last(bands.p5) : "")
      + " Canadian dollars" + (asOf ? ", as of " + asOf : "") + ".";
  }

  function fan(el, opts) {
    const cone = opts && opts.cone;
    if (!el || !cone || !isSeries(cone.p50)) return false;
    const bands = {};
    BAND_KEYS.forEach(key => { if (isSeries(cone[key])) bands[key] = cone[key]; });
    const compare = isSeries(opts.compareMedian) ? opts.compareMedian : null;
    const floor = Number.isFinite(opts.floor) ? opts.floor : null;
    const axis = axisFor(Object.values(bands).concat(compare ? [compare] : []), floor);
    const spanY = (axis.max - axis.min) || 1;
    const yOf = value => VIEW.bottom - ((value - axis.min) / spanY) * (VIEW.bottom - VIEW.top);
    const xOf = fraction => VIEW.left + fraction * (VIEW.right - VIEW.left);
    const points = series => series.map((value, index) =>
      [xOf(index / (series.length - 1)), yOf(value)]);
    const years = Math.max(1, Math.round(opts.years) || 1);
    const labels = Object.assign({}, DEFAULT_LABELS, opts.labels);
    const band = (top, bottom, opacity) => top && bottom
      ? '<path d="' + bandPath(points(top), points(bottom)) + '" fill="'
        + COLOR.ink + '" fill-opacity="' + opacity + '"/>' : "";
    const stroke = (series, color, width, extra) => '<path d="' + linePath(points(series))
      + '" fill="none" stroke="' + color + '" stroke-width="' + width
      + '" stroke-linejoin="round"' + (extra || "") + "/>";
    el.innerHTML = '<div class="fan"><svg viewBox="0 0 ' + VIEW.width + " "
      + VIEW.height + '" role="img" aria-label="'
      + App.esc(describe(bands, years, opts.asOf)) + '">'
      + gridSvg(axis, yOf)
      + band(bands.p95, bands.p5, ".08") + band(bands.p75, bands.p25, ".16")
      + floorSvg(floor, yOf)
      + (bands.p5 ? stroke(bands.p5, COLOR.red, 1.8) : "")
      + (compare ? stroke(compare, COLOR.compare, 2.2,
        ' stroke-dasharray="1 5" stroke-linecap="round"') : "")
      + stroke(bands.p50, COLOR.ink, 3, ' stroke-linecap="round"')
      + yearLabelsSvg(years, xOf) + endMarkersSvg(endMarkers(bands, labels, yOf))
      + "</svg></div>";
    return true;
  }

  App.charts = {fan};
})();
