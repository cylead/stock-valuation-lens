"use strict";

const NS = "http://www.w3.org/2000/svg";
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));
const DAY = 86400000;
const FORMULA_VERSION = "cagr-duration-weighted-pe-capped-60-v2";

const state = {
  ticker: null,
  company: null,
  metric: "eps_diluted",
  context: null,
  data: null,
  range: null,
  selection: [],
  visibility: { price: true, adjusted: true, fair: true, dividend: false },
  chartLayout: null,
  brushLayout: null,
  chartDrag: null,
  brushDrag: null,
  rangeTimer: null,
  searchTimer: null,
};

function time(value) {
  return value ? Date.parse(value + "T00:00:00Z") : NaN;
}

function iso(value) {
  return new Date(value).toISOString().slice(0, 10);
}

function fmt(value, digits = 2) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

function compact(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  const number = Number(value);
  const absolute = Math.abs(number);
  if (absolute >= 1e12) return `${fmt(number / 1e12, 2)}T`;
  if (absolute >= 1e9) return `${fmt(number / 1e9, 2)}B`;
  if (absolute >= 1e6) return `${fmt(number / 1e6, 2)}M`;
  return fmt(number, absolute < 10 ? 2 : 1);
}

function percent(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  return `${Number(value) >= 0 ? "+" : ""}${fmt(value, 2)}%`;
}

function formulaServiceCurrent(valuation = state.data?.valuation) {
  return valuation?.formulaVersion === FORMULA_VERSION;
}

function svg(tag, attrs = {}, textValue = null) {
  const node = document.createElementNS(NS, tag);
  Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
  if (textValue !== null) node.textContent = textValue;
  return node;
}

async function api(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

function setLoading(active) {
  $("#chart-wrap").style.opacity = active ? ".55" : "1";
  $("#chart-wrap").style.transition = "opacity .15s";
}

function showError(error) {
  const warnings = $("#chart-warnings");
  warnings.textContent = "";
  const item = document.createElement("div");
  item.className = "warning";
  item.textContent = error.message || String(error);
  warnings.appendChild(item);
}

function updateCompanyHeader() {
  const company = state.company;
  $("#company-exchange").textContent = company.isSecFiler
    ? `${company.exchange || "US exchange"} · CIK ${String(company.cik).padStart(10, "0")}`
    : `${company.exchange || "Exchange"}${company.currency ? ` · ${company.currency}` : ""} · ${company.reportingSource}`;
  $("#company-ticker").textContent = company.ticker;
  $("#company-name").textContent = company.name;
  $("#company-search").value = company.ticker;
  const latestPriceDate = company.latestSplitDate || company.latestAdjustedDate;
  $("#source-freshness").textContent = latestPriceDate
    ? `${company.priceSource} through ${latestPriceDate}` : `No ${company.priceSource} prices`;
  $("#availability-notes").textContent = company.availabilityNotes.join(" ");

  $$("#metric-buttons button").forEach((button) => {
    const metric = button.dataset.metric;
    button.disabled = !company.availability[metric];
    button.classList.toggle("active", metric === state.metric);
    button.title = button.disabled ? "This metric is unavailable for the company." : "";
  });
  const priceLegend = $('#legend button[data-series="price"]');
  if (priceLegend && priceLegend.childNodes.length > 1) {
    priceLegend.childNodes[1].nodeValue = company.availability.split_price
      ? "Split-only price" : "Stooq adjusted (approx.)";
  }
  const adjustedLegend = $('#legend button[data-series="adjusted"]');
  adjustedLegend.hidden = !company.availability.split_price;
  const dividendLegend = $('#legend button[data-series="dividend"]');
  dividendLegend.disabled = !company.availability.dividend_per_share;
  dividendLegend.classList.toggle("on", company.availability.dividend_per_share && state.visibility.dividend);
  dividendLegend.title = company.availability.dividend_per_share
    ? "Show or hide annual dividend yield at fiscal year-end."
    : "No reported annual dividend per share is available for this company.";
}

async function selectCompany(ticker) {
  try {
    setLoading(true);
    const company = await api(`/api/company/${encodeURIComponent(ticker)}`);
    state.ticker = company.ticker;
    state.company = company;
    state.selection = [];
    state.context = null;
    state.data = null;
    if (!company.availability[state.metric]) {
      state.metric = ["eps_diluted", "eps_basic", "fcf_per_share"].find((key) => company.availability[key]) || "eps_diluted";
    }
    $("#welcome").hidden = true;
    $("#workspace").hidden = false;
    updateCompanyHeader();
    await loadContext(true);
  } catch (error) {
    showError(error);
  } finally {
    setLoading(false);
  }
}

function chartUrl(range = null, includeMultiple = true) {
  const params = new URLSearchParams({ metric: state.metric });
  if (range) {
    params.set("start", range.start);
    params.set("end", range.end);
  }
  const custom = $("#custom-multiple").value;
  if (includeMultiple && custom) params.set("multiple", custom);
  return `/api/chart/${encodeURIComponent(state.ticker)}?${params.toString()}`;
}

async function loadContext(resetWindow) {
  setLoading(true);
  try {
    state.context = await api(chartUrl(null));
    const minimum = state.context.bounds.minimum;
    const maximum = state.context.bounds.maximum;
    if (!minimum || !maximum) {
      state.range = { start: minimum, end: maximum };
      state.data = state.context;
      renderAll();
      return;
    }
    if (resetWindow || !state.range) {
      const maximumTime = time(maximum);
      const requested = maximumTime - 15 * 365.2425 * DAY;
      state.range = { start: iso(Math.max(time(minimum), requested)), end: maximum };
    } else {
      state.range = clampRange(state.range.start, state.range.end);
    }
    await loadWindow();
  } finally {
    setLoading(false);
  }
}

async function loadWindow() {
  if (!state.range || !state.range.start || !state.range.end) {
    state.data = state.context;
    renderAll();
    return;
  }
  setLoading(true);
  try {
    state.data = await api(chartUrl(state.range));
    state.range = { start: state.data.bounds.start, end: state.data.bounds.end };
    state.selection = state.selection.filter((point) => time(point.date) >= time(state.range.start) && time(point.date) <= time(state.range.end));
    renderAll();
  } catch (error) {
    showError(error);
  } finally {
    setLoading(false);
  }
}

function clampRange(start, end) {
  const minimum = time(state.context.bounds.minimum);
  const maximum = time(state.context.bounds.maximum);
  let left = time(start);
  let right = time(end);
  const fullSpan = maximum - minimum;
  const span = right - left;
  if (span >= fullSpan) return { start: iso(minimum), end: iso(maximum) };
  if (left < minimum) {
    right += minimum - left;
    left = minimum;
  }
  if (right > maximum) {
    left -= right - maximum;
    right = maximum;
  }
  left = Math.max(minimum, left);
  right = Math.min(maximum, right);
  if (right <= left) right = Math.min(maximum, left + 180 * DAY);
  return { start: iso(left), end: iso(right) };
}

function scheduleRange(start, end, delay = 80) {
  state.range = clampRange(start, end);
  clearTimeout(state.rangeTimer);
  state.rangeTimer = setTimeout(loadWindow, delay);
}

function setPreset(years) {
  if (!state.context?.bounds.maximum) return;
  const maximum = time(state.context.bounds.maximum);
  const minimum = time(state.context.bounds.minimum);
  const start = years === "all" ? minimum : Math.max(minimum, maximum - Number(years) * 365.2425 * DAY);
  $$("#time-buttons button").forEach((button) => button.classList.toggle("active", button.dataset.years === String(years)));
  scheduleRange(iso(start), iso(maximum), 0);
}

function renderAll() {
  updateCompanyHeader();
  renderStats();
  renderChart();
  renderRangeChart();
  renderAnnualChanges();
  renderTable();
  renderWarnings();
  renderSelection();
}

function renderStats() {
  const data = state.data;
  const valuation = data.valuation;
  const hasCurrentFormula = formulaServiceCurrent(valuation);
  $("#stat-cagr").textContent = valuation.cagr === null ? "—" : percent(valuation.cagr);
  $("#stat-years").textContent = !hasCurrentFormula
    ? "Restart local server to apply formula"
    : valuation.cagrYears === null
    ? "Two positive annual observations required"
    : `${fmt(valuation.cagrYears, 2)} fiscal years · ${valuation.cagrStartDate} to ${valuation.cagrEndDate}`;
  $("#stat-fair").textContent = !hasCurrentFormula || valuation.appliedMultiple === null
    ? "—" : `${fmt(valuation.appliedMultiple, 1)}×`;
  $("#stat-formula").textContent = hasCurrentFormula
    ? "Formula and custom P/E capped at 60×"
    : "Formula P/E requires a server restart";
  $("#chart-title").textContent = `${data.company.ticker} historical valuation`;
  $("#chart-subtitle").textContent = hasCurrentFormula
    ? `${data.metric.label} · weekly close · annual reported facts · selected-window formula P/E`
    : `${data.metric.label} · weekly close · annual reported facts · restart server to enable formula P/E`;
  const fairLegend = $('#legend button[data-series="fair"]');
  fairLegend.disabled = !hasCurrentFormula;
  fairLegend.title = hasCurrentFormula ? "" : "Restart the local server to use the formula P/E valuation.";
  $("#metric-column-heading").textContent = data.metric.label;
  $("#range-start").textContent = data.bounds.start || "—";
  $("#range-end").textContent = data.bounds.end || "—";
}

function extent(values) {
  const clean = values.filter((value) => value !== null && Number.isFinite(Number(value))).map(Number);
  if (!clean.length) return [0, 1];
  const maximum = Math.max(...clean);
  return [0, maximum > 0 ? maximum * 1.08 : 1];
}

function pathFrom(points, x, y) {
  let path = "";
  let open = false;
  points.forEach((point) => {
    if (point.value === null || point.value === undefined || !Number.isFinite(Number(point.value))) {
      open = false;
      return;
    }
    path += `${open ? "L" : "M"}${x(point.date).toFixed(2)},${y(point.value).toFixed(2)}`;
    open = true;
  });
  return path;
}

function addText(target, x, y, textValue, attrs = {}) {
  target.appendChild(svg("text", { x, y, fill: "#66736c", "font-size": 11, ...attrs }, textValue));
}

function rangeYearStep(minimum, maximum, width) {
  const firstYear = new Date(minimum).getUTCFullYear();
  const lastYear = new Date(maximum).getUTCFullYear();
  const maximumLabels = Math.max(2, Math.floor(width / 88) + 1);
  const minimumStep = Math.max(1, Math.ceil((lastYear - firstYear) / (maximumLabels - 1)));
  return [1, 2, 3, 5, 10, 15, 20, 25, 50].find((step) => step >= minimumStep) || minimumStep;
}

function addRangeYearTicks(target, plot, minimum, maximum, x) {
  const firstYear = new Date(minimum).getUTCFullYear();
  const lastYear = new Date(maximum).getUTCFullYear();
  if (firstYear === lastYear) {
    addText(target, plot.left, 81, firstYear, { "text-anchor": "start" });
    return;
  }

  const ticks = [{ value: minimum, label: firstYear, anchor: "start" }];
  const step = rangeYearStep(minimum, maximum, plot.right - plot.left);
  let year = Math.ceil((firstYear + 1) / step) * step;
  while (year < lastYear) {
    ticks.push({ value: `${year}-01-01`, label: year, anchor: "middle" });
    year += step;
  }
  ticks.push({ value: maximum, label: lastYear, anchor: "end" });

  ticks.forEach((tick) => {
    const xx = x(tick.value);
    if (tick.anchor === "middle") {
      target.appendChild(svg("line", {
        x1: xx,
        x2: xx,
        y1: plot.top,
        y2: plot.bottom,
        stroke: "#dce2db",
        "stroke-width": 1,
      }));
    }
    addText(target, xx, 81, tick.label, { "text-anchor": tick.anchor });
  });
}

function renderChart() {
  const target = $("#main-chart");
  target.textContent = "";
  const width = Math.max(target.clientWidth, 620);
  const height = Math.max(target.clientHeight, 360);
  target.setAttribute("viewBox", `0 0 ${width} ${height}`);
  const prices = state.data.priceSeries;
  const valuations = state.data.valuation.valuationPoints;
  const dividends = state.data.dividendYieldSeries || [];
  const hasCurrentFormula = formulaServiceCurrent();
  const hasSplit = prices.some((row) => row.splitClose !== null);
  const showDividends = state.visibility.dividend && dividends.some((row) => row.value !== null);
  const margin = { top: 18, right: showDividends ? 92 : 24, bottom: 38, left: 66 };
  const plot = { left: margin.left, top: margin.top, right: width - margin.right, bottom: height - margin.bottom };
  const primaryKey = hasSplit ? "splitClose" : "adjustedClose";
  const allDates = prices.map((row) => time(row.date)).filter(Number.isFinite);
  const empty = !allDates.length;
  $("#chart-empty").hidden = !empty;
  if (empty) return;
  const xMin = Math.min(...allDates);
  const xMax = Math.max(...allDates);
  const yValues = [];
  if (state.visibility.price) prices.forEach((row) => yValues.push(row[primaryKey]));
  if (hasSplit && state.visibility.adjusted) prices.forEach((row) => yValues.push(row.adjustedClose));
  if (hasCurrentFormula && state.visibility.fair) valuations.forEach((row) => yValues.push(row.fairValue));
  const [yMin, yMax] = extent(yValues);
  const x = (date) => plot.left + ((time(date) - xMin) / Math.max(1, xMax - xMin)) * (plot.right - plot.left);
  const y = (value) => plot.bottom - ((Number(value) - yMin) / Math.max(1e-9, yMax - yMin)) * (plot.bottom - plot.top);
  const [, dividendMax] = extent(dividends.map((row) => row.value));
  const dividendY = (value) => plot.bottom - (Number(value) / Math.max(1e-9, dividendMax)) * (plot.bottom - plot.top);
  state.chartLayout = { width, height, plot, xMin, xMax, yMin, yMax, x, y, dividendY, primaryKey };

  const background = svg("rect", { x: plot.left, y: plot.top, width: plot.right - plot.left, height: plot.bottom - plot.top, fill: "#fffdf7" });
  target.appendChild(background);
  for (let index = 0; index <= 5; index += 1) {
    const value = yMin + ((yMax - yMin) * index) / 5;
    const yy = y(value);
    target.appendChild(svg("line", { x1: plot.left, x2: plot.right, y1: yy, y2: yy, stroke: "#e1e4df", "stroke-width": 1 }));
    addText(target, plot.left - 10, yy + 4, compact(value), { "text-anchor": "end" });
  }
  for (let index = 0; index <= 5; index += 1) {
    const value = xMin + ((xMax - xMin) * index) / 5;
    const xx = plot.left + ((plot.right - plot.left) * index) / 5;
    target.appendChild(svg("line", { x1: xx, x2: xx, y1: plot.top, y2: plot.bottom, stroke: "#f0f1ed", "stroke-width": 1 }));
    addText(target, xx, plot.bottom + 22, new Date(value).getUTCFullYear(), { "text-anchor": "middle" });
  }
  if (showDividends) {
    for (let index = 0; index <= 5; index += 1) {
      const value = (dividendMax * index) / 5;
      addText(target, plot.right + 10, dividendY(value) + 4, fmt(value, 2) + "%", {
        fill: "#76508f",
        "text-anchor": "start",
      });
    }
    const dividendAxisMiddle = (plot.top + plot.bottom) / 2;
    addText(target, width - 8, dividendAxisMiddle, "Annual dividend yield (%)", {
      fill: "#76508f",
      "font-size": 10,
      "text-anchor": "middle",
      transform: "rotate(-90 " + (width - 8) + " " + dividendAxisMiddle + ")",
    });
  }

  const fairPoints = hasCurrentFormula
    ? valuations.map((row) => ({ date: row.date, value: row.fairValue })) : [];
  if (hasCurrentFormula && state.visibility.fair) {
    let segment = [];
    const flushArea = () => {
      if (segment.length > 1) {
        const area = `M${x(segment[0].date)},${plot.bottom} ${segment.map((point) => `L${x(point.date)},${y(point.value)}`).join(" ")} L${x(segment[segment.length - 1].date)},${plot.bottom}Z`;
        target.appendChild(svg("path", { d: area, fill: "rgba(31,107,77,.13)", stroke: "none" }));
      }
      segment = [];
    };
    fairPoints.forEach((point) => {
      if (point.value === null || point.value < 0) flushArea();
      else segment.push(point);
    });
    flushArea();
    target.appendChild(svg("path", { d: pathFrom(fairPoints, x, y), fill: "none", stroke: "#e27b32", "stroke-width": 2.7, "stroke-linejoin": "round" }));
  }
  if (hasSplit && state.visibility.adjusted) {
    const points = prices.map((row) => ({ date: row.date, value: row.adjustedClose }));
    target.appendChild(svg("path", { d: pathFrom(points, x, y), fill: "none", stroke: "#8d9690", "stroke-width": 1.4, "stroke-dasharray": "4 4" }));
  }
  if (state.visibility.price) {
    const points = prices.map((row) => ({ date: row.date, value: row[primaryKey] }));
    target.appendChild(svg("path", { d: pathFrom(points, x, y), fill: "none", stroke: "#17221d", "stroke-width": 2, "stroke-linejoin": "round" }));
  }
  if (showDividends) {
    const points = dividends.map((row) => ({ date: row.date, value: row.value }));
    target.appendChild(svg("path", {
      d: pathFrom(points, x, dividendY),
      fill: "none",
      stroke: "#76508f",
      "stroke-width": 2.4,
      "stroke-linejoin": "round",
    }));
    dividends.forEach((point) => {
      if (point.value === null || point.value === undefined) return;
      const marker = svg("circle", {
        cx: x(point.date),
        cy: dividendY(point.value),
        r: 3.7,
        fill: "#fffdf7",
        stroke: "#76508f",
        "stroke-width": 2,
      });
      const fiscalLabel = point.fiscalYear ? `FY${point.fiscalYear}` : point.date;
      const priceLabel = point.priceType === "split_only_close" ? "split-only close" : "adjusted close (approx.)";
      marker.appendChild(svg(
        "title",
        {},
        `${fiscalLabel}: ${fmt(point.value, 2)}% · dividend/share ${fmt(point.dividendPerShare, 3)} · ${priceLabel} ${fmt(point.price, 2)} on ${point.priceDate}`,
      ));
      target.appendChild(marker);
    });
  }
  valuations.forEach((point) => {
    if (hasCurrentFormula && point.fairValue !== null && state.visibility.fair) {
      target.appendChild(svg("circle", { cx: x(point.date), cy: y(point.fairValue), r: 3.3, fill: "#fffdf7", stroke: "#e27b32", "stroke-width": 2 }));
    }
  });
  state.selection.forEach((point, index) => {
    const xx = x(point.date);
    target.appendChild(svg("line", { x1: xx, x2: xx, y1: plot.top, y2: plot.bottom, stroke: "#1f6b4d", "stroke-width": 1.5, "stroke-dasharray": "5 4" }));
    target.appendChild(svg("circle", { cx: xx, cy: y(point[primaryKey]), r: 5, fill: "#1f6b4d", stroke: "#fff", "stroke-width": 2 }));
    addText(target, xx + 5, plot.top + 14 + index * 14, `${index + 1}: ${point.date}`, { fill: "#1f6b4d" });
  });
  target.appendChild(svg("rect", { x: plot.left, y: plot.top, width: plot.right - plot.left, height: plot.bottom - plot.top, fill: "transparent", stroke: "#cfd5cf", "stroke-width": 1 }));
}

function nearestPrice(clientX) {
  if (!state.chartLayout || !state.data?.priceSeries.length) return null;
  const rect = $("#main-chart").getBoundingClientRect();
  const localX = ((clientX - rect.left) / rect.width) * state.chartLayout.width;
  const { plot, xMin, xMax } = state.chartLayout;
  const wanted = xMin + ((localX - plot.left) / (plot.right - plot.left)) * (xMax - xMin);
  const rows = state.data.priceSeries;
  let low = 0;
  let high = rows.length - 1;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (time(rows[middle].date) < wanted) low = middle + 1;
    else high = middle;
  }
  const candidates = [rows[low], rows[Math.max(0, low - 1)]].filter(Boolean);
  return candidates.sort((a, b) => Math.abs(time(a.date) - wanted) - Math.abs(time(b.date) - wanted))[0];
}

function priceMetricMultiple(point, valuation) {
  const price = Number(point?.splitClose ?? point?.adjustedClose);
  const metric = Number(valuation?.metricValue);
  if (!Number.isFinite(price) || !Number.isFinite(metric) || price <= 0 || metric <= 0) return null;
  return price / metric;
}

function renderTooltip(event) {
  if (state.chartDrag?.moved) return;
  const point = nearestPrice(event.clientX);
  const tooltip = $("#chart-tooltip");
  if (!point) {
    tooltip.hidden = true;
    return;
  }
  const valuation = state.data.valuation.valuationPoints.reduce((best, item) => {
    if (!best) return item;
    return Math.abs(time(item.date) - time(point.date)) < Math.abs(time(best.date) - time(point.date)) ? item : best;
  }, null);
  const dividend = (state.data.dividendYieldSeries || []).reduce((best, item) => {
    if (time(item.date) > time(point.date)) return best;
    return !best || time(item.date) > time(best.date) ? item : best;
  }, null);
  tooltip.textContent = "";
  const title = document.createElement("b");
  title.textContent = point.date;
  tooltip.appendChild(title);
  const priceMultiple = priceMetricMultiple(point, valuation);
  const priceMultipleLabel = state.metric === "fcf_per_share" ? "Price / FCF" : "Price P/E";
  const rows = [
    [state.company.availability.split_price ? "Split-only close" : "Stooq close (approx.)", point.splitClose ?? point.adjustedClose],
    ["Stooq adjusted", point.adjustedClose],
    [state.data.metric.label, valuation?.metricValue],
    [dividend?.fiscalYear ? `FY${dividend.fiscalYear} year-end dividend yield` : "Annual dividend yield", state.visibility.dividend ? dividend?.value : null, dividend?.value === null || dividend?.value === undefined ? null : fmt(dividend.value, 2) + "%"],
    [priceMultipleLabel, priceMultiple, priceMultiple === null ? null : `${fmt(priceMultiple, 1)}×`],
    ["Formula value", formulaServiceCurrent() ? valuation?.fairValue : null],
  ];
  rows.forEach(([label, value, formattedValue]) => {
    if (value === null || value === undefined) return;
    const line = document.createElement("span");
    const left = document.createElement("em");
    left.style.fontStyle = "normal";
    left.textContent = label;
    const right = document.createElement("strong");
    right.textContent = formattedValue || compact(value);
    line.append(left, right);
    tooltip.appendChild(line);
  });
  const wrap = $("#chart-wrap").getBoundingClientRect();
  let left = event.clientX - wrap.left + 14;
  let top = event.clientY - wrap.top + 12;
  if (left + 285 > wrap.width) left -= 300;
  if (top + 240 > wrap.height) top -= 245;
  tooltip.style.left = `${Math.max(5, left)}px`;
  tooltip.style.top = `${Math.max(5, top)}px`;
  tooltip.hidden = false;
}

function hideTooltip() {
  $("#chart-tooltip").hidden = true;
}

function chartPointerDown(event) {
  if (!state.chartLayout) return;
  state.chartDrag = { x: event.clientX, range: { ...state.range }, moved: false };
  event.currentTarget.setPointerCapture(event.pointerId);
}

function chartPointerMove(event) {
  if (!state.chartDrag) {
    renderTooltip(event);
    return;
  }
  const distance = event.clientX - state.chartDrag.x;
  if (Math.abs(distance) < 4) return;
  state.chartDrag.moved = true;
  hideTooltip();
  const span = time(state.chartDrag.range.end) - time(state.chartDrag.range.start);
  const shift = (-distance / (state.chartLayout.plot.right - state.chartLayout.plot.left)) * span;
  const tentative = clampRange(iso(time(state.chartDrag.range.start) + shift), iso(time(state.chartDrag.range.end) + shift));
  $("#range-start").textContent = tentative.start;
  $("#range-end").textContent = tentative.end;
  state.chartDrag.tentative = tentative;
}

function chartPointerUp(event) {
  if (!state.chartDrag) return;
  if (state.chartDrag.moved && state.chartDrag.tentative) {
    scheduleRange(state.chartDrag.tentative.start, state.chartDrag.tentative.end, 0);
  } else {
    const point = nearestPrice(event.clientX);
    if (point) addSelection(point);
  }
  state.chartDrag = null;
}

function chartWheel(event) {
  if (!state.range || !state.context) return;
  // Trackpad scrolling should keep moving the page. Browsers expose trackpad
  // pinch-to-zoom (and Ctrl + wheel) as a wheel event with ctrlKey set.
  if (!event.ctrlKey) return;
  event.preventDefault();
  const start = time(state.range.start);
  const end = time(state.range.end);
  const span = end - start;
  const factor = event.deltaY > 0 ? 1.25 : 0.8;
  const nextSpan = Math.max(180 * DAY, span * factor);
  const rect = event.currentTarget.getBoundingClientRect();
  const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
  const anchor = start + span * ratio;
  scheduleRange(iso(anchor - nextSpan * ratio), iso(anchor + nextSpan * (1 - ratio)), 120);
}

function renderRangeChart() {
  const target = $("#range-chart");
  target.textContent = "";
  if (!state.context?.priceSeries.length || !state.range) return;
  const width = Math.max(target.clientWidth, 620);
  const height = 84;
  target.setAttribute("viewBox", `0 0 ${width} ${height}`);
  const plot = { left: 12, right: width - 12, top: 10, bottom: 66 };
  const rows = state.context.priceSeries;
  const hasSplit = rows.some((row) => row.splitClose !== null);
  const key = hasSplit ? "splitClose" : "adjustedClose";
  const minimum = time(state.context.bounds.minimum);
  const maximum = time(state.context.bounds.maximum);
  const values = rows.map((row) => row[key]).filter((value) => value !== null);
  const [, maxValue] = extent(values);
  const x = (value) => {
    const timestamp = typeof value === "number" ? value : time(value);
    return plot.left + ((timestamp - minimum) / Math.max(1, maximum - minimum)) * (plot.right - plot.left);
  };
  const y = (value) => plot.bottom - (Number(value) / Math.max(1, maxValue)) * (plot.bottom - plot.top);
  const points = rows.map((row) => ({ date: row.date, value: row[key] }));
  target.appendChild(svg("rect", { x: plot.left, y: plot.top, width: plot.right - plot.left, height: plot.bottom - plot.top, rx: 6, fill: "#eef1eb" }));
  target.appendChild(svg("path", { d: pathFrom(points, x, y), fill: "none", stroke: "#78837c", "stroke-width": 1.2 }));
  const left = x(state.range.start);
  const right = x(state.range.end);
  target.appendChild(svg("rect", { x: plot.left, y: plot.top, width: Math.max(0, left - plot.left), height: plot.bottom - plot.top, fill: "rgba(244,241,232,.72)" }));
  target.appendChild(svg("rect", { x: right, y: plot.top, width: Math.max(0, plot.right - right), height: plot.bottom - plot.top, fill: "rgba(244,241,232,.72)" }));
  target.appendChild(svg("rect", { x: left, y: plot.top, width: Math.max(2, right - left), height: plot.bottom - plot.top, fill: "rgba(31,107,77,.08)", stroke: "#1f6b4d", "stroke-width": 1.2 }));
  [left, right].forEach((xx) => {
    target.appendChild(svg("rect", { x: xx - 5, y: plot.top - 3, width: 10, height: plot.bottom - plot.top + 6, rx: 4, fill: "#1f6b4d" }));
    target.appendChild(svg("line", { x1: xx - 1.5, x2: xx - 1.5, y1: plot.top + 14, y2: plot.bottom - 14, stroke: "#fff" }));
    target.appendChild(svg("line", { x1: xx + 1.5, x2: xx + 1.5, y1: plot.top + 14, y2: plot.bottom - 14, stroke: "#fff" }));
  });
  addRangeYearTicks(target, plot, minimum, maximum, x);
  state.brushLayout = { width, plot, minimum, maximum, left, right };
}

function brushDate(clientX) {
  const rect = $("#range-chart").getBoundingClientRect();
  const local = ((clientX - rect.left) / rect.width) * state.brushLayout.width;
  const ratio = Math.min(1, Math.max(0, (local - state.brushLayout.plot.left) / (state.brushLayout.plot.right - state.brushLayout.plot.left)));
  return state.brushLayout.minimum + ratio * (state.brushLayout.maximum - state.brushLayout.minimum);
}

function brushPointerDown(event) {
  if (!state.brushLayout) return;
  const rect = event.currentTarget.getBoundingClientRect();
  const local = ((event.clientX - rect.left) / rect.width) * state.brushLayout.width;
  const distanceLeft = Math.abs(local - state.brushLayout.left);
  const distanceRight = Math.abs(local - state.brushLayout.right);
  let mode = distanceLeft <= distanceRight ? "left" : "right";
  if (local > state.brushLayout.left + 8 && local < state.brushLayout.right - 8) mode = "window";
  state.brushDrag = { mode, anchor: brushDate(event.clientX), range: { ...state.range } };
  event.currentTarget.setPointerCapture(event.pointerId);
}

function brushPointerMove(event) {
  if (!state.brushDrag) return;
  const value = brushDate(event.clientX);
  const originalStart = time(state.brushDrag.range.start);
  const originalEnd = time(state.brushDrag.range.end);
  let start = originalStart;
  let end = originalEnd;
  if (state.brushDrag.mode === "left") start = Math.min(value, end - 180 * DAY);
  else if (state.brushDrag.mode === "right") end = Math.max(value, start + 180 * DAY);
  else {
    const shift = value - state.brushDrag.anchor;
    start += shift;
    end += shift;
  }
  state.range = clampRange(iso(start), iso(end));
  $("#range-start").textContent = state.range.start;
  $("#range-end").textContent = state.range.end;
  renderRangeChart();
}

function brushPointerUp() {
  if (!state.brushDrag) return;
  state.brushDrag = null;
  loadWindow();
}

function addSelection(point) {
  const key = state.chartLayout.primaryKey;
  if (point[key] === null && point.adjustedClose === null) return;
  if (state.selection.length >= 2) state.selection = [];
  state.selection.push(point);
  state.selection.sort((a, b) => time(a.date) - time(b.date));
  renderChart();
  renderSelection();
}

function returnStats(first, last, key) {
  const start = first[key];
  const end = last[key];
  if (!start || !end || start <= 0 || end <= 0) return null;
  const years = (time(last.date) - time(first.date)) / (365.2425 * DAY);
  if (years <= 0) return null;
  const ratio = end / start;
  return { total: (ratio - 1) * 100, annualized: (Math.pow(ratio, 1 / years) - 1) * 100, years };
}

function renderSelection() {
  const target = $("#selection-result");
  target.textContent = "";
  if (state.selection.length !== 2) {
    target.hidden = true;
    return;
  }
  const [first, last] = state.selection;
  const key = state.company.availability.split_price ? "splitClose" : "adjustedClose";
  const capital = returnStats(first, last, key);
  const adjusted = returnStats(first, last, "adjustedClose");
  const label = document.createElement("b");
  label.textContent = `${first.date} → ${last.date}`;
  target.appendChild(label);
  const adjustedText = state.company.availability.split_price && adjusted
    ? ` · Stooq adjusted return ${percent(adjusted.total)} (${percent(adjusted.annualized)} annualized)` : "";
  const text = document.createTextNode(
    ` · Price return ${capital ? percent(capital.total) : "—"} (${capital ? percent(capital.annualized) : "—"} annualized)${adjustedText}`
  );
  target.appendChild(text);
  target.hidden = false;
}

function sourceLabel(source) {
  if (!source) return "Unavailable";
  if (source.label) return source.label;
  if (source.tag) return `${source.tag} · filed ${source.filed || "—"}`;
  if (source.ocf && source.capex) return `${source.ocf.tag} − ${source.capex.tag} · filed ${source.ocf.filed || "—"}`;
  return "SEC Company Facts";
}

function annualChange(current, previous) {
  if (current === null || current === undefined || previous === null || previous === undefined) return null;
  const currentValue = Number(current);
  const previousValue = Number(previous);
  if (!Number.isFinite(currentValue) || !Number.isFinite(previousValue) || previousValue === 0) return null;
  // An absolute denominator keeps the sign aligned with the metric's direction
  // when a loss narrows or a positive value turns negative.
  const change = ((currentValue - previousValue) / Math.abs(previousValue)) * 100;
  return Math.abs(change) < 0.005 ? 0 : change;
}

function renderAnnualChanges() {
  const target = $("#annual-change-list");
  target.textContent = "";
  const rows = [...state.data.fundamentals].sort((a, b) => time(a.period_end) - time(b.period_end));
  const history = [...(state.context?.fundamentals || rows)].sort((a, b) => time(a.period_end) - time(b.period_end));
  const previousByDate = new Map(history.map((row, index) => [row.period_end, index > 0 ? history[index - 1] : null]));
  $("#annual-change-subtitle").textContent = `${state.data.metric.label} · change from the previous reported fiscal year`;

  if (!rows.length) {
    const empty = document.createElement("p");
    empty.className = "annual-change-empty";
    empty.textContent = "No annual observations in this window.";
    target.appendChild(empty);
    return;
  }

  rows.forEach((row) => {
    const previous = previousByDate.get(row.period_end) || null;
    const change = previous ? annualChange(row.value, previous.value) : null;
    const item = document.createElement("div");
    const isNegative = change !== null && change < 0;
    const isFlat = change !== null && Math.abs(change) < 0.005;
    item.className = `annual-change-item${isNegative ? " negative" : ""}${isFlat ? " flat" : ""}${change === null ? " unavailable" : ""}`;
    item.setAttribute("role", "listitem");

    const year = document.createElement("span");
    year.textContent = row.fiscalYear || row.period_end.slice(0, 4);
    const value = document.createElement("strong");
    value.textContent = change === null ? "—" : percent(change);
    const detail = document.createElement("small");
    detail.textContent = compact(row.value);
    item.append(year, value, detail);

    if (!previous) {
      item.title = "No prior fiscal year in the available history";
    } else if (row.value === null || row.value === undefined || previous.value === null || previous.value === undefined) {
      item.title = "A reported value is missing for this year or the prior year";
    } else if (Number(previous.value) === 0) {
      item.title = "Percentage change cannot be calculated from a zero prior-year value";
    } else {
      item.title = `${state.data.metric.label}: ${compact(row.value)}; prior year: ${compact(previous.value)}`;
    }
    item.setAttribute("aria-label", `${year.textContent}: ${change === null ? "change unavailable" : percent(change)}; reported value ${compact(row.value)}`);
    target.appendChild(item);
  });
}

function renderTable() {
  const body = $("#fundamentals-body");
  body.textContent = "";
  const rows = [...state.data.fundamentals].reverse();
  const dividendYieldByPeriod = new Map();
  (state.data.dividendYieldSeries || []).forEach((point) => {
    dividendYieldByPeriod.set(point.dividendDate, point);
  });
  $("#table-count").textContent = `${rows.filter((row) => row.value !== null).length} observations`;
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    const annualDividend = dividendYieldByPeriod.get(row.period_end);
    const values = [
      row.period_end,
      compact(row.value),
      compact(row.dividendPerShare),
      annualDividend?.value === null || annualDividend?.value === undefined ? "—" : fmt(annualDividend.value, 2) + "%",
      compact(row.fcf),
      sourceLabel(row.source),
    ];
    values.forEach((value, index) => {
      const td = document.createElement("td");
      td.textContent = value;
      if (index === 2 && annualDividend) {
        td.title = `FY${annualDividend.fiscalYear || row.period_end.slice(0, 4)} reported annual dividend/share · ${sourceLabel(annualDividend.source)}`;
      }
      if (index === 3 && annualDividend) {
        const priceDescription = annualDividend.priceType === "split_only_close"
          ? "split-only close"
          : annualDividend.priceType === "stooq_adjusted_close"
          ? "Stooq adjusted close (approximation)"
          : "no eligible year-end price";
        td.title = annualDividend.value === null
          ? `Unavailable: ${priceDescription}`
          : `${fmt(annualDividend.dividendPerShare, 3)} ÷ ${fmt(annualDividend.price, 2)} (${priceDescription}, ${annualDividend.priceDate}) × 100`;
      }
      if (index === 5) {
        td.className = "source";
        td.title = row.source ? JSON.stringify(row.source, null, 2) : "No supported reported fact";
      }
      tr.appendChild(td);
    });
    body.appendChild(tr);
  });
}

function renderWarnings() {
  const target = $("#chart-warnings");
  target.textContent = "";
  const warnings = [...state.data.warnings];
  if (!formulaServiceCurrent()) {
    warnings.unshift("Formula P/E is disabled because this page is connected to an older server process. Restart the local server, then reload the page.");
  }
  warnings.forEach((warning) => {
    const item = document.createElement("div");
    item.className = "warning";
    item.textContent = warning;
    target.appendChild(item);
  });
}

function exportCsv() {
  if (!state.data) return;
  const hasCurrentFormula = formulaServiceCurrent();
  const lines = [["series", "date", "value", "details"]];
  state.data.priceSeries.forEach((row) => {
    if (row.splitClose !== null) lines.push(["split_only_close", row.date, row.splitClose, ""]);
    if (row.adjustedClose !== null) lines.push(["stooq_adjusted_close", row.date, row.adjustedClose, ""]);
  });
  state.data.valuation.valuationPoints.forEach((row) => {
    lines.push([state.metric, row.date, row.metricValue ?? "", "reported annual metric"]);
    if (hasCurrentFormula && row.fairValue !== null) lines.push(["formula_value", row.date, row.fairValue, `multiple=${state.data.valuation.appliedMultiple}`]);
  });
  (state.data.dividendYieldSeries || []).forEach((row) => {
    const fiscalYear = row.fiscalYear || row.dividendDate.slice(0, 4);
    lines.push([
      "annual_dividend_per_share",
      row.dividendDate,
      row.dividendPerShare,
      `fiscal_year=${fiscalYear};source=${sourceLabel(row.source)}`,
    ]);
    const details = "fiscal_year=" + fiscalYear +
      ";dividend_per_share=" + row.dividendPerShare +
      ";price=" + (row.price ?? "") +
      ";price_date=" + (row.priceDate ?? "") +
      ";price_type=" + (row.priceType ?? "") +
      ";source=" + sourceLabel(row.source);
    lines.push(["annual_dividend_yield_percent", row.date, row.value ?? "", details]);
  });
  const escaped = lines.map((row) => row.map((cell) => `"${String(cell ?? "").replaceAll('"', '""')}"`).join(",")).join("\n");
  downloadBlob(new Blob([escaped], { type: "text/csv;charset=utf-8" }), `${state.ticker}-${state.metric}-${state.range.start}-${state.range.end}.csv`);
}

function exportPng() {
  const source = $("#main-chart");
  const serializer = new XMLSerializer();
  const content = serializer.serializeToString(source);
  const blob = new Blob([content], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const image = new Image();
  image.onload = () => {
    const scale = 2;
    const canvas = document.createElement("canvas");
    canvas.width = state.chartLayout.width * scale;
    canvas.height = state.chartLayout.height * scale;
    const context = canvas.getContext("2d");
    context.scale(scale, scale);
    context.fillStyle = "#fffdf7";
    context.fillRect(0, 0, state.chartLayout.width, state.chartLayout.height);
    context.drawImage(image, 0, 0, state.chartLayout.width, state.chartLayout.height);
    canvas.toBlob((png) => downloadBlob(png, `${state.ticker}-${state.metric}.png`), "image/png");
    URL.revokeObjectURL(url);
  };
  image.src = url;
}

function downloadBlob(blob, filename) {
  const anchor = document.createElement("a");
  const url = URL.createObjectURL(blob);
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function searchCompanies(query) {
  const results = $("#search-results");
  try {
    const payload = await api(`/api/companies?q=${encodeURIComponent(query)}&limit=12`);
    results.textContent = "";
    payload.companies.forEach((company) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "search-result";
      button.setAttribute("role", "option");
      const ticker = document.createElement("b");
      ticker.textContent = company.ticker;
      const name = document.createElement("span");
      name.textContent = company.name;
      const exchange = document.createElement("small");
      exchange.textContent = company.exchange || "US";
      button.append(ticker, name, exchange);
      button.addEventListener("click", () => {
        results.hidden = true;
        selectCompany(company.ticker);
      });
      results.appendChild(button);
    });
    results.hidden = payload.companies.length === 0;
    return payload.companies;
  } catch (error) {
    results.hidden = true;
    return [];
  }
}

function bindEvents() {
  $("#company-search").addEventListener("input", (event) => {
    clearTimeout(state.searchTimer);
    const query = event.target.value.trim();
    if (!query) {
      $("#search-results").hidden = true;
      return;
    }
    state.searchTimer = setTimeout(() => searchCompanies(query), 150);
  });
  $("#company-search").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      const first = $("#search-results .search-result");
      if (first) first.click();
      else if (event.target.value.trim()) selectCompany(event.target.value.trim());
    }
    if (event.key === "Escape") $("#search-results").hidden = true;
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".search-shell")) $("#search-results").hidden = true;
  });
  $$("#metric-buttons button").forEach((button) => button.addEventListener("click", async () => {
    if (button.disabled || state.metric === button.dataset.metric) return;
    state.metric = button.dataset.metric;
    state.selection = [];
    $("#custom-multiple").value = "";
    await loadContext(false);
  }));
  $$("#time-buttons button").forEach((button) => button.addEventListener("click", () => setPreset(button.dataset.years)));
  $("#custom-multiple").addEventListener("change", loadWindow);
  $("#reset-multiple").addEventListener("click", () => {
    $("#custom-multiple").value = "";
    loadWindow();
  });
  $("#reset-view").addEventListener("click", () => setPreset(15));
  $("#clear-selection").addEventListener("click", () => {
    state.selection = [];
    renderChart();
    renderSelection();
  });
  $("#export-csv").addEventListener("click", exportCsv);
  $("#export-png").addEventListener("click", exportPng);
  $$("#legend button").forEach((button) => button.addEventListener("click", () => {
    const key = button.dataset.series;
    state.visibility[key] = !state.visibility[key];
    button.classList.toggle("on", state.visibility[key]);
    renderChart();
  }));
  const chart = $("#main-chart");
  chart.addEventListener("pointerdown", chartPointerDown);
  chart.addEventListener("pointermove", chartPointerMove);
  chart.addEventListener("pointerup", chartPointerUp);
  chart.addEventListener("pointercancel", () => { state.chartDrag = null; });
  chart.addEventListener("pointerleave", hideTooltip);
  chart.addEventListener("wheel", chartWheel, { passive: false });
  const range = $("#range-chart");
  range.addEventListener("pointerdown", brushPointerDown);
  range.addEventListener("pointermove", brushPointerMove);
  range.addEventListener("pointerup", brushPointerUp);
  range.addEventListener("pointercancel", () => { state.brushDrag = null; });
  window.addEventListener("resize", () => {
    if (state.data) {
      renderChart();
      renderRangeChart();
    }
  });
}

async function initialize() {
  bindEvents();
  try {
    const health = await api("/api/health");
    $("#source-freshness").textContent = `${health.companies.toLocaleString()} local companies`;
    const companies = await searchCompanies("AAPL");
    $("#search-results").hidden = true;
    const apple = companies.find((company) => company.ticker === "AAPL");
    if (apple) await selectCompany(apple.ticker);
    else $("#welcome").hidden = false;
  } catch (error) {
    $("#source-freshness").textContent = "Database unavailable";
    $("#welcome").hidden = false;
    showError(error);
  }
}

initialize();
