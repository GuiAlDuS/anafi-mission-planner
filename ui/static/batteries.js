"use strict";
// Batteries view: how long each battery lasts, from FreeFlight's flight logs.
// Colour follows the battery name (sorted over all flights, so filtering never repaints);
// slots 1-3 are validated all-pairs on white, a 4th+ name and unlabelled flights are grey.
(() => {
  const byId = (id) => document.getElementById(id);
  const SVG = "http://www.w3.org/2000/svg";
  const css = getComputedStyle(document.documentElement);
  const token = (name) => css.getPropertyValue(name).trim();
  const SLOTS = ["--bat-1", "--bat-2", "--bat-3"].map(token);
  const NONE = token("--bat-none");
  const CRITICAL = token("--critical");
  const SUGGESTED_NAMES = ["A", "B", "C"];
  const LAST_N = 3; // same rule as `anafi-plan runs`: worst of the last 3 measurable flights

  let data = null;
  let packData = null; // battery health checks (/api/battery)
  let loading = null;

  // ---------- helpers ----------
  function svg(tag, attrs = {}, parent = null) {
    const e = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined) e.setAttribute(k, v);
    if (parent) parent.append(e);
    return e;
  }
  function node(tag, props = {}, text = null, parent = null) {
    const e = document.createElement(tag);
    Object.assign(e, props);
    if (text !== null) e.textContent = text;
    if (parent) parent.append(e);
    return e;
  }
  const dateOf = (r) => new Date(r.date.replace(" ", "T"));
  const fmt = (v, d = 0) => Number(v).toFixed(d);
  // Names from flight labels and from pack checks share one sorted list, so pack A has the
  // same colour in the pack cards, the scatter and the flight charts.
  const names = () => [...new Set([
    ...(data ? data.runs.map((r) => r.battery) : []),
    ...(packData ? packData.packs.map((p) => p.name) : []),
  ].filter(Boolean))].sort();
  const colorOf = (name) => {
    if (!name) return NONE;
    const i = names().indexOf(name);
    return i >= 0 && i < SLOTS.length ? SLOTS[i] : NONE;
  };
  const groupName = (name) => (name ? `Battery ${name}` : "Not labelled");

  function inRange(r) {
    const months = document.querySelector('input[name="range"]:checked').value;
    if (months === "all") return true;
    const since = new Date();
    since.setMonth(since.getMonth() - Number(months));
    return dateOf(r) >= since;
  }
  const visibleRuns = () => data.runs.filter(inRange);
  const measurable = (runs) => runs.filter((r) => r.endurance !== null);

  function groups(runs) {
    // Labelled batteries in name order, then the unlabelled flights if there are any.
    const list = names().map((n) => ({ name: n, runs: runs.filter((r) => r.battery === n) }));
    const none = runs.filter((r) => !r.battery);
    if (none.length) list.push({ name: null, runs: none });
    return list.filter((g) => g.runs.length);
  }

  function estimate(runs) {
    const recent = measurable(runs).slice(-LAST_N);
    if (!recent.length) return null;
    return { minutes: Math.min(...recent.map((r) => r.endurance)), recent };
  }

  // ---------- tooltip ----------
  const tip = byId("chart-tip");
  function showTip(evt, r, value) {
    tip.replaceChildren();
    node("b", {}, value, tip);
    node("div", {}, `Flight ${r.id}, ${r.date}`, tip);
    const bat = node("div", {}, null, tip);
    node("span", { className: "key" }, null, bat).style.background = colorOf(r.battery);
    bat.append(document.createTextNode(groupName(r.battery)));
    node("div", {}, `${r.battery_start} → ${r.battery_end} % in ${fmt(r.airborne_s / 60, 1)} min`, tip);
    const notes = [r.survey ? "Survey flight" : "Other flight"];
    if (r.low_battery_at !== null) notes.push(`low-battery alert at ${r.low_battery_at} %`);
    node("div", {}, notes.join(", "), tip);
    tip.hidden = false;
    const box = evt.target.getBoundingClientRect();
    const x = evt.clientX ?? box.left + box.width / 2;
    const y = evt.clientY ?? box.top;
    tip.style.left = `${Math.min(window.innerWidth - tip.offsetWidth - 8, x + 14)}px`;
    tip.style.top = `${Math.max(8, y - tip.offsetHeight - 10)}px`;
  }
  const hideTip = () => { tip.hidden = true; };

  function hoverable(target, r, value, onHot) {
    target.setAttribute("tabindex", "0");
    target.setAttribute("aria-label", `Flight ${r.id}, ${r.date}, ${groupName(r.battery)}: ${value}`);
    const on = (e) => { showTip(e, r, value); onHot?.(true); };
    const off = () => { hideTip(); onHot?.(false); };
    target.addEventListener("pointermove", on);
    target.addEventListener("pointerleave", off);
    target.addEventListener("focus", on);
    target.addEventListener("blur", off);
  }

  // ---------- tiles ----------
  function renderTiles(runs) {
    const box = byId("tiles");
    box.replaceChildren();
    const gs = groups(runs);
    const labelled = gs.some((g) => g.name);
    for (const g of gs) {
      const est = estimate(g.runs);
      const tile = node("article", { className: "tile" }, null, box);
      tile.style.setProperty("--key", colorOf(g.name));
      const h = node("h4", {}, null, tile);
      node("span", { className: "key-dot" }, null, h);
      h.append(document.createTextNode(g.name ? groupName(g.name) : labelled ? "Not labelled" : "All flights"));
      const value = node("div", { className: "value" }, est ? String(Math.floor(est.minutes)) : "–", tile);
      if (est) node("small", {}, "min", value);
      node("p", {}, est
        ? `Plan with this or less: the worst of the last ${est.recent.length} flights `
          + `(${est.recent.map((r) => `${r.id}: ${fmt(r.endurance)} min`).join(", ")}).`
        : "No flight long enough to measure yet.", tile);
      const newest = g.runs[g.runs.length - 1];
      node("p", {}, `${g.runs.length} flight${g.runs.length === 1 ? "" : "s"}, newest ${newest.date.slice(0, 10)}.`, tile);
      if (est) {
        const b = node("button", { className: "secondary", type: "button" }, "Use in planner", tile);
        b.addEventListener("click", () => {
          const input = byId("endurance");
          input.value = Math.floor(est.minutes);
          input.dispatchEvent(new Event("input", { bubbles: true }));
          b.textContent = `Planner set to ${Math.floor(est.minutes)} min`;
        });
      }
    }
    if (!labelled) {
      node("p", { className: "hint" }, "Flights aren't labelled yet: pick the battery that flew each one in the "
        + "table below, and each battery gets its own figure, colour and panel.", box);
    }
  }

  // ---------- chart: endurance over time ----------
  function yearTicks(t0, t1) {
    const ticks = [];
    const span = (t1 - t0) / 864e5 / 365;
    const d = new Date(t0);
    if (span > 2) {
      for (let y = d.getFullYear() + 1; y <= new Date(t1).getFullYear(); y++) ticks.push([new Date(y, 0, 1), String(y)]);
    } else {
      const m = new Date(d.getFullYear(), d.getMonth() + 1, 1);
      for (; m <= t1; m.setMonth(m.getMonth() + 3)) {
        ticks.push([new Date(m), m.toLocaleDateString(undefined, { month: "short", year: "numeric" })]);
      }
    }
    return ticks;
  }

  function renderEndurance(runs) {
    const box = byId("chart-endurance");
    box.replaceChildren();
    const pts = measurable(runs);
    renderEnduranceLegend(pts);
    if (!pts.length) { node("p", { className: "empty-state" }, "No flights long enough to measure in this range.", box); return; }
    const W = Math.max(340, box.clientWidth), H = 300;
    const m = { l: 40, r: 128, t: 28, b: 34 };
    const t0 = Math.min(...pts.map((r) => +dateOf(r))), t1 = Math.max(...pts.map((r) => +dateOf(r)));
    const pad = Math.max((t1 - t0) * 0.04, 864e5 * 10);
    const X = (d) => m.l + ((+d - (t0 - pad)) / (t1 - t0 + 2 * pad)) * (W - m.l - m.r);
    const planner = Number(byId("endurance").value) || data.planner_endurance;
    const ymax = Math.max(30, Math.ceil(Math.max(planner, ...pts.map((r) => r.endurance)) / 5) * 5);
    const Y = (v) => m.t + (1 - v / ymax) * (H - m.t - m.b);
    const s = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img",
      "aria-label": "Full-battery minutes per flight over time, by battery" }, box);

    const axis = svg("g", { class: "axis" }, s);
    for (let v = 0; v <= ymax; v += 5) {
      svg("line", { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v), stroke: v ? token("--grid") : token("--axis") }, axis);
      svg("text", { x: m.l - 8, y: Y(v) + 4, "text-anchor": "end" }, axis).textContent = v;
    }
    svg("text", { x: m.l - 8, y: m.t - 16, "text-anchor": "end", class: "title" }, axis).textContent = "min";
    for (const [d, label] of yearTicks(t0 - pad, t1 + pad)) {
      svg("line", { x1: X(d), x2: X(d), y1: H - m.b, y2: H - m.b + 5, stroke: token("--axis") }, axis);
      svg("text", { x: X(d), y: H - m.b + 19, "text-anchor": "middle" }, axis).textContent = label;
    }

    // The planner's current setting: what the battery estimates assume.
    svg("line", { x1: m.l, x2: W - m.r, y1: Y(planner), y2: Y(planner), stroke: token("--ink"), "stroke-width": 1 }, s);
    svg("text", { x: W - m.r + 8, y: Y(planner) + 4, class: "ref-label" }, s).textContent = `Planner: ${fmt(planner)} min`;

    for (const name of names()) {
      const series = pts.filter((r) => r.battery === name);
      if (series.length > 1) {
        svg("polyline", { points: series.map((r) => `${X(dateOf(r))},${Y(r.endurance)}`).join(" "),
          fill: "none", stroke: colorOf(name), "stroke-width": 2, "stroke-linejoin": "round" }, s);
      }
      const last = series[series.length - 1];
      if (last) {  // direct label at each battery's newest point
        svg("text", { x: Math.min(X(dateOf(last)) + 9, W - m.r + 8), y: Y(last.endurance) - 9, class: "ref-label" }, s)
          .textContent = name;
      }
    }
    for (const r of pts) {
      const c = colorOf(r.battery);
      const cx = X(dateOf(r)), cy = Y(r.endurance);
      if (r.survey) svg("circle", { cx, cy, r: 5.5, fill: c, stroke: "#fff", "stroke-width": 2 }, s);
      else svg("circle", { cx, cy, r: 4.5, fill: "#fff", stroke: c, "stroke-width": 2 }, s);
      if (r.low_battery_at !== null) svg("circle", { cx, cy, r: 9, fill: "none", stroke: CRITICAL, "stroke-width": 1.5 }, s);
      hoverable(svg("circle", { cx, cy, r: 12, class: "hit" }, s), r, `${fmt(r.endurance)} min for a full battery`);
    }
  }

  function renderEnduranceLegend(pts) {
    const box = byId("legend-endurance");
    box.replaceChildren();
    const present = new Set(pts.map((r) => r.battery || ""));
    for (const name of [...names(), ""]) {
      if (!present.has(name)) continue;
      const item = node("span", {}, null, box);
      const k = svg("svg", { width: 22, height: 12, "aria-hidden": "true" }, item);
      if (name) svg("line", { x1: 1, x2: 21, y1: 6, y2: 6, stroke: colorOf(name), "stroke-width": 2 }, k);
      svg("circle", { cx: 11, cy: 6, r: 4.5, fill: colorOf(name), stroke: "#fff", "stroke-width": 1.5 }, k);
      item.append(document.createTextNode(groupName(name)));
    }
    const shapes = node("span", {}, null, box);
    const k = svg("svg", { width: 34, height: 12, "aria-hidden": "true" }, shapes);
    svg("circle", { cx: 7, cy: 6, r: 4.5, fill: NONE }, k);
    svg("circle", { cx: 24, cy: 6, r: 4, fill: "#fff", stroke: NONE, "stroke-width": 2 }, k);
    shapes.append(document.createTextNode("Survey / other flight"));
    if (pts.some((r) => r.low_battery_at !== null)) {
      const a = node("span", {}, null, box);
      const k2 = svg("svg", { width: 22, height: 22, "aria-hidden": "true" }, a);
      svg("circle", { cx: 11, cy: 11, r: 8, fill: "none", stroke: CRITICAL, "stroke-width": 1.5 }, k2);
      a.append(document.createTextNode("Low-battery alert during the flight"));
    }
  }

  // ---------- chart: discharge curves, one panel per battery ----------
  function renderDischarge(runs) {
    const box = byId("chart-discharge");
    box.replaceChildren();
    const usable = measurable(runs);
    if (!usable.length) { node("p", { className: "empty-state" }, "No flights long enough to measure in this range.", box); return; }
    const xmax = Math.ceil(Math.max(...usable.map((r) => r.airborne_s / 60)) / 5) * 5;
    // Lay out every panel first, then measure: the grid only settles once all panels exist.
    const panels = groups(usable).map((g) => {
      const panel = node("div", {}, null, box);
      const head = node("div", { className: "panel-head-m" }, null, panel);
      node("span", { className: "key-dot" }, null, head).style.setProperty("--key", colorOf(g.name));
      head.append(document.createTextNode(groupName(g.name)));
      const newest = g.runs[g.runs.length - 1];
      node("p", { className: "panel-sub" },
        `${g.runs.length} flight${g.runs.length === 1 ? "" : "s"}. Newest: ${newest.id}, ${newest.date.slice(0, 10)}, `
        + `${fmt(newest.endurance)} min for a full battery.`, panel);
      return [g, panel];
    });
    const W = Math.max(260, Math.min(...panels.map(([, p]) => p.clientWidth)));
    const H = Math.round(Math.min(300, Math.max(190, W * 0.42)));
    for (const [g, panel] of panels) drawDischargePanel(g, panel, W, H, xmax);
  }

  function drawDischargePanel(g, panel, W, H, xmax) {
    const m = { l: 34, r: 20, t: 8, b: 28 };
    const X = (v) => m.l + (v / xmax) * (W - m.l - m.r);
    const Y = (v) => m.t + (1 - v / 100) * (H - m.t - m.b);
    const s = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img",
      "aria-label": `Charge during each flight, ${groupName(g.name)}` }, panel);
    const axis = svg("g", { class: "axis" }, s);
    for (const v of [0, 25, 50, 75, 100]) {
      svg("line", { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v), stroke: v ? token("--grid") : token("--axis") }, axis);
      svg("text", { x: m.l - 6, y: Y(v) + 4, "text-anchor": "end" }, axis).textContent = `${v}%`;
    }
    for (let t = 0; t <= xmax; t += 5) {
      svg("text", { x: X(t), y: H - m.b + 16, "text-anchor": "middle" }, axis).textContent = t ? `${t} min` : "0";
    }
    const c = colorOf(g.name);
    g.runs.forEach((r, i) => {
      const isNewest = i === g.runs.length - 1;
      const d = r.curve.map(([t, p], k) => `${k ? "L" : "M"}${X(t)},${Y(p)}`).join("");
      const line = svg("path", { d, class: "flight-line", stroke: c, "stroke-opacity": isNewest ? 1 : 0.3 }, s);
      if (r.low_battery_min !== null) {
        svg("circle", { cx: X(r.low_battery_min), cy: Y(r.low_battery_at), r: 4.5, fill: CRITICAL,
          stroke: "#fff", "stroke-width": 2 }, s);
      }
      const hit = svg("path", { d, fill: "none", stroke: "transparent", "stroke-width": 12, class: "hit" }, s);
      hoverable(hit, r, `${fmt(r.drain, 1)} % per minute`, (hot) => {
        line.classList.toggle("is-hot", hot);
        line.setAttribute("stroke-opacity", hot || isNewest ? 1 : 0.3);
      });
    });
  }

  // ---------- table: also where flights get their battery ----------
  function renderTable(runs) {
    const table = byId("runs-table");
    table.replaceChildren();
    const rows = (byId("show-short").checked ? runs : measurable(runs)).slice().reverse();
    const head = node("tr", {}, null, node("thead", {}, null, table));
    for (const [label, num] of [["Flight"], ["Date"], ["Battery"], ["In the air", 1], ["Charge", 1],
      ["Used per minute", 1], ["Full battery lasts", 1], ["Max altitude", 1], ["Furthest", 1], ["Notes"]]) {
      node("th", { className: num ? "num" : "", scope: "col" }, label, head);
    }
    const body = node("tbody", {}, null, table);
    const options = [...new Set([...SUGGESTED_NAMES, ...names()])].sort();
    for (const r of rows) {
      const tr = node("tr", {}, null, body);
      node("td", {}, r.id, tr);
      node("td", {}, r.date, tr);
      const cell = node("td", {}, null, tr);
      const wrap = node("span", { className: "bat" }, null, cell);
      node("span", { className: "key-dot" }, null, wrap).style.setProperty("--key", colorOf(r.battery));
      const sel = node("select", { ariaLabel: `Battery for flight ${r.id}` }, null, wrap);
      node("option", { value: "" }, "Not labelled", sel);
      for (const n of options) node("option", { value: n, selected: n === r.battery }, `Battery ${n}`, sel);
      sel.addEventListener("change", () => setLabel(r.id, sel.value));
      node("td", { className: "num" }, `${fmt(r.airborne_s / 60, 1)} min`, tr);
      node("td", { className: "num" }, `${r.battery_start} → ${r.battery_end} %`, tr);
      node("td", { className: "num" }, `${fmt(r.drain, 1)} %`, tr);
      node("td", { className: "num" }, r.endurance === null ? "too short" : `${fmt(r.endurance)} min`, tr);
      node("td", { className: "num" }, `${fmt(r.max_altitude_m)} m`, tr);
      node("td", { className: "num" }, `${fmt(r.max_distance_m)} m`, tr);
      const notes = node("td", {}, r.survey ? "Survey" : "", tr);
      if (r.low_battery_at !== null) {
        notes.append(document.createTextNode(r.survey ? ", " : ""));
        node("span", { className: "alert" }, `low-battery alert at ${r.low_battery_at} %`, notes);
      }
    }
  }

  // ---------- pack check: what each battery reports about itself ----------
  // The gauge outline is a new pack's capacity. Inside: charge (pack colour), usable but empty
  // (a light step of the same colour), and capacity lost to wear (hatched grey).
  const PART_NAMES = [["charge", "Charge"], ["empty", "Usable, empty"], ["lost", "Lost to wear"]];
  let justChecked = null;

  function hatch(defs, id) {
    const pat = svg("pattern", { id, width: 6, height: 6, patternUnits: "userSpaceOnUse",
      patternTransform: "rotate(45)" }, defs);
    svg("rect", { width: 6, height: 6, fill: token("--wear-bg") }, pat);
    svg("line", { x1: 0, y1: 0, x2: 0, y2: 6, stroke: token("--wear"), "stroke-width": 2.5 }, pat);
  }

  function gaugeParts(p) {
    const health = Math.max(0, Math.min(100, p.health_pct ?? 100));
    const charge = p.charge_pct === null ? null : health * Math.max(0, Math.min(100, p.charge_pct)) / 100;
    return { health, charge, lost: 100 - health };
  }

  function drawGauge(parent, p, animate) {
    const W = 300, H = 40, nub = 8, body = W - nub - 2;
    const s = svg("svg", { viewBox: `0 0 ${W} ${H}`, class: "gauge", role: "img",
      "aria-label": `Pack ${p.name || p.serial}: ${p.charge_pct ?? "?"} % charge, health ${p.health_pct ?? "?"} %` }, parent);
    const id = `hatch-${String(p.serial).replace(/\W/g, "")}`;
    hatch(svg("defs", {}, s), id);
    const c = colorOf(p.name);
    const { health, charge, lost } = gaugeParts(p);
    const X = (pct) => 3 + (pct / 100) * (body - 6);
    const y = 3, h = H - 6;
    // Shell and terminal nub, in ink: the outline is the "new pack".
    svg("rect", { x: 1, y: 1, width: body, height: H - 2, rx: 7, fill: token("--surface"),
      stroke: token("--ink"), "stroke-width": 2 }, s);
    svg("rect", { x: body + 2, y: H / 2 - 7, width: nub - 2, height: 14, rx: 2, fill: token("--ink") }, s);
    const parts = svg("g", {}, s);
    const clip = svg("clipPath", { id: `${id}-clip` }, s);
    svg("rect", { x: 3, y, width: body - 6, height: h, rx: 5 }, clip);
    parts.setAttribute("clip-path", `url(#${id}-clip)`);
    // A 2px surface gap between the parts.
    svg("rect", { x: X(0), y, width: Math.max(0, X(health) - X(0) - 1), height: h,
      fill: c, "fill-opacity": 0.22 }, parts);
    if (lost > 0) svg("rect", { x: X(health) + 1, y, width: X(100) - X(health) - 1, height: h, fill: `url(#${id})` }, parts);
    if (charge !== null && charge > 0) {
      const fill = svg("rect", { x: X(0), y, width: Math.max(0, X(charge) - X(0) - (charge < health ? 1 : 0)),
        height: h, fill: c, class: animate ? "gauge-fill is-new" : "gauge-fill" }, parts);
      fill.style.transformOrigin = `${X(0)}px 0`;
    }
    return s;
  }

  function cellMeter(parent, v, lim) {
    // 3.3-4.4 V per cell: outside the track is a problem; the tick is this check.
    const W = 140, H = 34, lo = lim.cell_min - 0.1, hi = lim.cell_max + 0.1;
    const X = (x) => 4 + ((x - lo) / (hi - lo)) * (W - 8);
    const s = svg("svg", { viewBox: `0 0 ${W} ${H}`, class: "meter", "aria-hidden": "true" }, parent);
    svg("rect", { x: 4, y: 9, width: W - 8, height: 4, rx: 2, fill: token("--grid") }, s);
    svg("rect", { x: X(lim.cell_min), y: 9, width: X(lim.cell_max) - X(lim.cell_min), height: 4, rx: 2,
      fill: token("--axis") }, s);
    for (const v of [lim.cell_min, lim.cell_max]) {
      svg("text", { x: X(v), y: 31, "text-anchor": "middle", class: "meter-label" }, s).textContent = v.toFixed(1);
    }
    if (v === null) return s;
    const ok = v >= lim.cell_min && v <= lim.cell_max;
    const x = X(Math.max(lo, Math.min(hi, v)));
    svg("rect", { x: x - 1.5, y: 3, width: 3, height: 16, rx: 1.5, fill: ok ? token("--ink") : CRITICAL }, s);
    return s;
  }

  function sparkline(parent, hist) {
    const pts = hist.filter((h) => h.health_pct !== null);
    // A trend needs time: checks from one day only would draw a flat, meaningless line.
    if (new Set(pts.map((h) => h.time.slice(0, 10))).size < 2) return;
    const W = 140, H = 34, m = 4;
    const lo = Math.min(...pts.map((h) => h.health_pct)), hi = Math.max(...pts.map((h) => h.health_pct));
    const span = Math.max(5, hi - lo);
    const X = (i) => m + (i / (pts.length - 1)) * (W - 2 * m);
    const Y = (v) => m + (1 - (v - (hi - span)) / span) * (H - 2 * m);
    const wrap = node("div", { className: "spark" }, null, parent);
    const s = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img",
      "aria-label": `Health over ${pts.length} checks: ${pts.map((h) => h.health_pct).join(", ")} %` }, wrap);
    svg("polyline", { points: pts.map((h, i) => `${X(i)},${Y(h.health_pct)}`).join(" "), fill: "none",
      stroke: token("--ink"), "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }, s);
    const last = pts[pts.length - 1];
    svg("circle", { cx: X(pts.length - 1), cy: Y(last.health_pct), r: 4, fill: token("--ink"),
      stroke: "#fff", "stroke-width": 2 }, s);
    node("span", {}, `${pts[0].health_pct} → ${last.health_pct} % over ${pts.length} checks`, wrap);
  }

  const shortDate = (t) => new Date(t).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });

  function renderPackCard(box, p, lim) {
    const card = node("article", { className: "pack" }, null, box);
    card.style.setProperty("--key", colorOf(p.name));
    const head = node("header", { className: "pack-head" }, null, card);
    const title = node("h4", {}, null, head);
    node("span", { className: "key-dot" }, null, title);
    title.append(document.createTextNode(p.name ? `Pack ${p.name}` : "New pack"));
    node("small", { className: "serial" }, p.serial, title);
    const bad = p.problems.length > 0;
    const verdict = node("span", { className: `verdict ${bad ? "is-bad" : "is-ok"}` }, null, head);
    node("span", { className: "icon", ariaHidden: "true" }, bad ? "✕" : "✓", verdict);
    verdict.append(document.createTextNode(bad ? "Don't fly" : "OK to fly"));

    const hero = node("div", { className: "pack-hero" }, null, card);
    const hv = node("div", { className: "health" }, null, hero);
    node("b", {}, p.health_pct === null ? "–" : String(p.health_pct), hv);
    node("small", {}, p.health_pct === null ? "health unknown" : "% health", hv);
    drawGauge(hero, p, p.serial === justChecked);
    const parts = gaugeParts(p);
    const cap = node("div", { className: "gauge-caption" }, null, hero);
    node("span", {}, p.charge_pct === null ? "Charge ?" : `${p.charge_pct} % charged`, cap);
    if (parts.lost > 0) node("span", {}, `${fmt(parts.lost)} % of a new pack lost to wear`, cap);

    const facts = node("dl", { className: "facts" }, null, card);
    const fact = (label, value, extra) => {
      const d = node("div", {}, null, facts);
      node("dt", {}, label, d);
      const dd = node("dd", {}, value, d);
      if (extra) extra(dd);
    };
    fact("Voltage per cell", p.cell_v === null ? "?" : `${fmt(p.cell_v, 2)} V`, (dd) => cellMeter(dd, p.cell_v, lim));
    fact("Charge cycles", p.cycles === null ? "?" : String(p.cycles));
    fact("Gauge firmware", p.gauge_fw ? p.gauge_fw.replaceAll("_", " ") : "?");
    fact("Last check", shortDate(p.time));
    sparkline(card, p.history);

    if (p.problems.length || p.notes.length) {
      const ul = node("ul", { className: "pack-notes" }, null, card);
      for (const t of p.problems) node("li", { className: "is-bad" }, t, ul);
      for (const t of p.notes) node("li", {}, t, ul);
    }
    if (!p.name) {
      const f = node("form", { className: "name-pack" }, null, card);
      const input = node("input", { type: "text", maxLength: 20, placeholder: "Name, e.g. D",
        ariaLabel: `Name for pack ${p.serial}` }, null, f);
      node("button", { type: "submit", className: "secondary" }, "Name this pack", f);
      f.addEventListener("submit", async (e) => {
        e.preventDefault();
        if (!input.value.trim()) return;
        await postPack("/api/battery/name", { serial: p.serial, name: input.value });
      });
    }
  }

  function renderGaugeLegend(box) {
    box.replaceChildren();
    const c = SLOTS[0];
    for (const [k, label] of PART_NAMES) {
      const item = node("span", {}, null, box);
      const s = svg("svg", { width: 18, height: 12, "aria-hidden": "true" }, item);
      if (k === "lost") { hatch(svg("defs", {}, s), "legend-hatch"); }
      svg("rect", { x: 1, y: 1, width: 16, height: 10, rx: 2, stroke: token("--rule"),
        fill: k === "lost" ? "url(#legend-hatch)" : c, "fill-opacity": k === "empty" ? 0.22 : 1 }, s);
      item.append(document.createTextNode(label));
    }
    node("span", { className: "legend-note" }, "Outline: a new pack", box);
  }

  function renderVolt(checks, lim) {
    const fig = byId("volt-figure");
    const pts = checks.filter((c) => c.cell_v !== null && c.charge_pct !== null);
    fig.hidden = pts.length === 0;
    const box = byId("chart-volt");
    box.replaceChildren();
    const legend = byId("volt-legend");
    legend.replaceChildren();
    if (!pts.length) return;
    for (const name of [...new Set(pts.map((c) => c.name || ""))].sort((a, b) => (a ? 0 : 1) - (b ? 0 : 1) || a.localeCompare(b))) {
      const item = node("span", {}, null, legend);
      const k = svg("svg", { width: 14, height: 14, "aria-hidden": "true" }, item);
      svg("circle", { cx: 7, cy: 7, r: 5, fill: colorOf(name || null), stroke: "#fff", "stroke-width": 1.5 }, k);
      item.append(document.createTextNode(name ? `Pack ${name}` : "Not named"));
    }
    const W = Math.max(320, box.clientWidth), H = 260;
    const m = { l: 52, r: 24, t: 16, b: 40 };
    const vlo = Math.min(3.6, ...pts.map((c) => c.cell_v)) - 0.05, vhi = Math.max(4.4, ...pts.map((c) => c.cell_v)) + 0.02;
    const X = (v) => m.l + (v / 100) * (W - m.l - m.r);
    const Y = (v) => m.t + (1 - (v - vlo) / (vhi - vlo)) * (H - m.t - m.b);
    const s = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img",
      "aria-label": "Resting voltage per cell against charge, every pack check" }, box);
    const axis = svg("g", { class: "axis" }, s);
    for (let v = Math.ceil(vlo * 10) / 10; v <= vhi + 1e-9; v += 0.1) {
      const major = Math.abs(v * 10 - Math.round(v * 10)) < 1e-6 && Math.round(v * 10) % 2 === 0;
      svg("line", { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v), stroke: token("--grid") }, axis);
      if (major) svg("text", { x: m.l - 8, y: Y(v) + 4, "text-anchor": "end" }, axis).textContent = `${v.toFixed(1)} V`;
    }
    for (let p = 0; p <= 100; p += 25) {
      svg("line", { x1: X(p), x2: X(p), y1: H - m.b, y2: H - m.b + 5, stroke: token("--axis") }, axis);
      svg("text", { x: X(p), y: H - m.b + 19, "text-anchor": "middle" }, axis).textContent = `${p} %`;
    }
    svg("line", { x1: m.l, x2: W - m.r, y1: H - m.b, y2: H - m.b, stroke: token("--axis") }, axis);
    svg("text", { x: (m.l + W - m.r) / 2, y: H - 4, "text-anchor": "middle", class: "title" }, axis).textContent = "Charge the battery reports";

    // Reference zone: reads full, but below what a full cell holds.
    const zx = X(95), zy = Y(lim.full_cell_min);
    svg("rect", { x: zx, y: zy, width: X(100) - zx + 6, height: Math.max(0, H - m.b - zy), class: "zone" }, s);
    svg("text", { x: zx - 6, y: zy + 14, "text-anchor": "end", class: "zone-label" }, s).textContent = "Reads full, voltage low";

    const latest = new Map();
    for (const c of pts) latest.set(c.serial, c);
    for (const c of pts) {
      const isLatest = latest.get(c.serial) === c;
      const cx = X(c.charge_pct), cy = Y(c.cell_v);
      svg("circle", { cx, cy, r: isLatest ? 6.5 : 4.5, fill: colorOf(c.name), "fill-opacity": isLatest ? 1 : 0.45,
        stroke: "#fff", "stroke-width": 2 }, s);
      if (isLatest && c.name) {
        svg("text", { x: cx - 10, y: cy + 4, "text-anchor": "end", class: "ref-label" }, s).textContent = c.name;
      }
      const hit = svg("circle", { cx, cy, r: 12, class: "hit", tabindex: 0,
        "aria-label": `Pack ${c.name || c.serial}, ${shortDate(c.time)}: ${c.charge_pct} %, ${fmt(c.cell_v, 2)} V per cell` }, s);
      const on = (e) => {
        tip.replaceChildren();
        node("b", {}, `${fmt(c.cell_v, 2)} V per cell`, tip);
        const who = node("div", {}, null, tip);
        node("span", { className: "key" }, null, who).style.background = colorOf(c.name);
        who.append(document.createTextNode(c.name ? `Pack ${c.name}` : `Pack ${c.serial}`));
        node("div", {}, `${c.charge_pct} % charge, health ${c.health_pct ?? "?"} %`, tip);
        node("div", {}, shortDate(c.time), tip);
        tip.hidden = false;
        const box2 = e.target.getBoundingClientRect();
        const x = e.clientX ?? box2.left + box2.width / 2, y = e.clientY ?? box2.top;
        tip.style.left = `${Math.min(window.innerWidth - tip.offsetWidth - 8, x + 14)}px`;
        tip.style.top = `${Math.max(8, y - tip.offsetHeight - 10)}px`;
      };
      hit.addEventListener("pointermove", on);
      hit.addEventListener("focus", on);
      hit.addEventListener("pointerleave", hideTip);
      hit.addEventListener("blur", hideTip);
    }
  }

  function renderPacks() {
    const box = byId("packs");
    box.replaceChildren();
    if (!packData) return;
    byId("no-olympe").hidden = packData.can_check;
    document.querySelector(".packs-actions").hidden = !packData.can_check;
    renderGaugeLegend(byId("gauge-legend"));
    if (!packData.packs.length) {
      byId("gauge-legend").replaceChildren();
      if (packData.can_check) {
        node("p", { className: "empty-state" }, "No packs checked yet. Put a pack in the drone and press "
          + "Check the pack in the drone.", box);
      }
    }
    for (const p of packData.packs) renderPackCard(box, p, packData.limits);
    renderVolt(packData.checks, packData.limits);
    justChecked = null; // the fill animates once, right after its check
  }

  async function postPack(url, body) {
    const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) });
    const out = await res.json();
    if (!res.ok) throw new Error(out.error || `HTTP ${res.status}`);
    packData = out;
    render();
    return out;
  }

  byId("check-pack").addEventListener("click", async () => {
    const button = byId("check-pack");
    const status = byId("check-status");
    const link = document.querySelector('input[name="link"]:checked').value;
    button.disabled = true;
    status.className = "check-status is-busy";
    status.textContent = "Connecting to the drone and reading the battery. This can take up to a minute.";
    try {
      const res = await fetch("/api/battery/check", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ link }) });
      const out = await res.json();
      if (!res.ok) throw new Error(out.error || `HTTP ${res.status}`);
      packData = out;
      justChecked = out.checked;
      const p = out.packs.find((x) => x.serial === out.checked);
      status.className = "check-status";
      status.textContent = p ? `Checked ${p.name ? `pack ${p.name}` : `a new pack (serial ${p.serial})`} at `
        + `${new Date(p.time).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}.` : "Checked.";
      render();
      byId("packs").querySelector(".pack .gauge-fill.is-new")?.closest(".pack")
        ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    } catch (err) {
      status.className = "check-status is-error";
      status.textContent = `Not checked: ${err.message}`;
    } finally {
      button.disabled = false;
    }
  });

  // ---------- data ----------
  function render() {
    renderPacks();
    if (!data) return;
    const runs = visibleRuns();
    if (!data.runs.length) {
      byId("tiles").replaceChildren(node("p", { className: "empty-state" },
        "No flight logs yet. Plug in the iPhone and press Copy new logs from iPhone."));
      for (const id of ["chart-endurance", "chart-discharge", "legend-endurance", "runs-table"]) byId(id).replaceChildren();
      return;
    }
    renderTiles(runs);
    renderEndurance(runs);
    renderDischarge(runs);
    renderTable(runs);
  }

  async function load() {
    const status = byId("runs-status");
    loading = loading || (async () => {
      try {
        const [res, bat] = await Promise.all([fetch("/api/runs"), fetch("/api/battery")]);
        data = await res.json();
        packData = await bat.json();
        status.textContent = `${data.runs.length} flights in ${data.dir.split("/").pop()}/`;
        render();
      } catch {
        status.textContent = "Couldn't read the flight logs. Is anafi-gui still running?";
      } finally {
        loading = null;
      }
    })();
    return loading;
  }

  async function setLabel(flight, battery) {
    await fetch("/api/runs/label", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ flight, battery }) });
    const y = window.scrollY;
    await load();
    window.scrollTo(0, y);
  }

  byId("pull-runs").addEventListener("click", async () => {
    const status = byId("runs-status");
    status.textContent = "Copying new logs from the iPhone…";
    try {
      const res = await fetch("/api/runs/pull", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      const body = await res.json();
      if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
      await load();
      status.textContent = body.new.length ? `Copied ${body.new.length} new flight log${body.new.length === 1 ? "" : "s"}.`
        : "No new flight logs on the iPhone.";
    } catch (err) {
      status.textContent = `Not copied: ${err.message}`;
    }
  });
  for (const r of document.querySelectorAll('input[name="range"]')) r.addEventListener("change", render);
  byId("show-short").addEventListener("change", render);
  let resizeTimer = null;
  window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(render, 150); });
  window.addEventListener("viewchange", (e) => { if (e.detail === "batteries") (data ? render() : load()); });
  if (location.hash === "#batteries") load();
})();
