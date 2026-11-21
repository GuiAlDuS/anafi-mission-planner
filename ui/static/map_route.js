"use strict";
// Plan tab, drawing on the map: area, takeoff point, photos, route, preview, map key.
// Plain scripts sharing one scope, loaded in order by index.html (see app.js).

const areaLayer = L.layerGroup().addTo(map);
const routeLayer = L.layerGroup().addTo(map);
const photoLayer = L.layerGroup().addTo(map);
map.createPane("photos").style.zIndex = 450; // above the route (400), below markers (600)
const photoRenderer = L.canvas({ padding: 0.3, pane: "photos" }); // thousands of dots stay fast on canvas
const vertexLayer = L.layerGroup().addTo(map);
let homeMarker = null;

// Corners get a grab area bigger than the square you see; side handles add a corner.
const vertexIcon = (first) => L.divIcon({
  className: "vertex-hit", html: `<div class="vertex${first ? " is-first" : ""}"></div>`, iconSize: [26, 26],
});
const midIcon = L.divIcon({ className: "vertex-hit", html: '<div class="vertex-mid">+</div>', iconSize: [24, 24] });
let areaShapes = []; // outline and fill, updated in place while a corner is dragged

// Undo for area edits (Ctrl+Z or the Undo link): snapshots taken before each change.
const areaHistory = [];
function rememberArea() {
  areaHistory.push(state.area.map((p) => [...p]));
  if (areaHistory.length > 50) areaHistory.shift();
  $("undo").hidden = false;
}
function undoArea() {
  if (!areaHistory.length || state.mode === "drawing") return;
  state.area = areaHistory.pop();
  $("undo").hidden = areaHistory.length === 0;
  drawArea();
  replanSoon();
}

function midpoint(a, b) { return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]; }
const homeIcon = L.divIcon({ className: "", html: '<div class="home-mark" title="Takeoff">H</div>', iconSize: [26, 26] });

function drawArea() {
  areaLayer.clearLayers();
  vertexLayer.clearLayers();
  areaShapes = [];
  const pts = state.area;
  const closed = state.mode !== "drawing" && pts.length >= 3;
  if (pts.length >= 2) {
    const shape = closed ? L.polygon : L.polyline;
    // Wider than the route's white casing (5 px) so the yellow shows on both sides where they cross.
    areaShapes = [
      shape(pts, { color: "#13201B", weight: 11, opacity: 0.75, fill: false, interactive: false }).addTo(areaLayer),
      shape(pts, { color: "#FFD200", weight: 7, fillColor: "#FFD200", fillOpacity: 0.07, interactive: false }).addTo(areaLayer),
    ];
  }
  // While dragging, move only the outline: redrawing everything would remove the marker being dragged.
  const reshape = () => areaShapes.forEach((sh) => sh.setLatLngs(state.area));
  const mids = [];
  const removeCorner = (i) => {
    if (state.mode === "drawing" || state.area.length <= 3) {
      if (state.mode !== "drawing") $("hint").textContent = "An area needs at least 3 corners.";
      return;
    }
    rememberArea();
    state.area.splice(i, 1);
    drawArea();
    replanSoon();
  };
  pts.forEach((p, i) => {
    const m = L.marker(p, { icon: vertexIcon(i === 0 && state.mode === "drawing"), draggable: true, keyboard: false,
      zIndexOffset: 900, title: closed ? "Drag to move; double-click or right-click to remove" : "" });
    m.on("dragstart", () => { rememberArea(); mids.forEach((mm) => mm.remove()); });
    m.on("drag", (e) => { state.area[i] = toPoint(e.latlng); reshape(); });
    m.on("dragend", () => { drawArea(); replanSoon(); });
    m.on("click", () => {
      if (state.mode === "drawing" && i === 0 && pts.length >= 3) finishDrawing();
    });
    m.on("dblclick", (e) => { L.DomEvent.stop(e); removeCorner(i); });
    m.on("contextmenu", (e) => { L.DomEvent.stop(e); removeCorner(i); });
    m.addTo(vertexLayer);
  });
  if (!closed) return;
  // A handle in the middle of each side: drag it (or click it) to add a corner there.
  pts.forEach((p, i) => {
    const j = (i + 1) % pts.length;
    const h = L.marker(midpoint(p, pts[j]), { icon: midIcon, draggable: true, keyboard: false, zIndexOffset: 800,
      title: "Drag or click to add a corner here" });
    let added = false;
    const add = (ll) => {
      rememberArea();
      state.area.splice(i + 1, 0, ll);
      added = true;
    };
    h.on("dragstart", (e) => {
      add(toPoint(e.target.getLatLng()));
      mids.filter((mm) => mm !== h).forEach((mm) => mm.remove());
    });
    h.on("drag", (e) => { state.area[i + 1] = toPoint(e.latlng); reshape(); });
    h.on("dragend", () => { drawArea(); replanSoon(); });
    h.on("click", (e) => {
      L.DomEvent.stop(e);
      if (added) return;
      add(midpoint(state.area[i], state.area[(i + 1) % state.area.length]));
      drawArea();
      replanSoon();
    });
    mids.push(h.addTo(vertexLayer));
  });
}

function drawHome() {
  if (homeMarker) { homeMarker.remove(); homeMarker = null; }
  if (!state.home) return;
  homeMarker = L.marker(state.home, { icon: homeIcon, draggable: true, zIndexOffset: 1000 });
  if (document.body.dataset.view !== "fly") homeMarker.addTo(map); // Fly shows the flown mission's home
  homeMarker.on("dragend", (e) => {
    state.home = toPoint(e.target.getLatLng());
    replanSoon();
  });
}

function metresPerPixel() {
  return 40075016.686 * Math.cos(map.getCenter().lat * Math.PI / 180) / 2 ** (map.getZoom() + 8);
}

let photoGroups = {}; // flight number -> photo dots, for highlighting

function drawPhotos(plan) {
  photoLayer.clearLayers();
  photoGroups = {};
  if (!plan || !$("show-photos").checked) return;
  // Size the dots by how far apart they are on screen; once they'd merge, the route says enough.
  const gapPx = plan.photo_spacing_m / metresPerPixel();
  if (gapPx < 7) return;
  const r = Math.min(6, Math.max(2, gapPx / 5));
  for (const f of plan.geojson.features) {
    if (f.properties.kind !== "photos") continue;
    const g = photoGroups[f.properties.flight] = L.layerGroup().addTo(photoLayer);
    for (const [lon, lat] of f.geometry.coordinates) {
      L.circleMarker([lat, lon], {
        renderer: photoRenderer, radius: r, weight: r > 2.5 ? 2 : 1, color: "#13201B", fillColor: "#FFFFFF",
        fillOpacity: 1, interactive: false,
      }).addTo(g);
    }
  }
}

// Route encoding: solid = photo line, thin dashed = transit (no photos), one chevron per
// leg for direction, numbered badge where each photo line starts (numbers run across flights).
const ROUTE = "#D1119B";
const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
let flightGroups = {}; // flight number -> layer group, for highlighting from the list

function flightLegs(plan) {
  // plan.geojson flight paths are home, s1, e1, s2, e2, ..., home
  const legs = [];
  let lineNo = (plan.from_line || 1) - 1;
  const totalLines = plan.total_lines || plan.lines;
  const land = getParam("end") === "land";
  for (const f of plan.geojson.features) {
    if (f.properties.kind !== "flight") continue;
    const n = f.properties.flight;
    const pts = f.geometry.coordinates.map(([lon, lat]) => L.latLng(lat, lon));
    const last = land ? pts.length - 2 : pts.length - 1; // with "land", the flight ends on the last line
    for (let i = 0; i < last; i++) {
      const photo = i % 2 === 1; // odd segments run s -> e along a photo line
      legs.push({
        flight: n, from: pts[i], to: pts[i + 1], photo,
        line: photo ? ++lineNo : null, totalLines,
        kind: photo ? "line" : i === 0 ? "out" : i === pts.length - 2 ? "back" : "turn",
      });
    }
    if (land) legs.push({ flight: n, landAt: pts[last] });
  }
  return legs;
}

function screenAngle(a, b) {
  const p = map.latLngToLayerPoint(a), q = map.latLngToLayerPoint(b);
  return Math.atan2(q.y - p.y, q.x - p.x) * 180 / Math.PI;
}

function chevron(a, b, group, spacing) {
  // Halfway along, or for photo lines halfway between the two photos nearest the middle,
  // so the arrow never sits on a dot.
  const len = a.distanceTo(b);
  let f = 0.5;
  if (spacing && len > spacing) f = Math.min(1, (Math.floor(len / spacing / 2) + 0.5) * spacing / len);
  const at = L.latLng(a.lat + f * (b.lat - a.lat), a.lng + f * (b.lng - a.lng));
  const html = `<svg class="chevron" viewBox="0 0 22 22" style="transform: rotate(${screenAngle(a, b)}deg)">
    <path d="M4 3 L19 11 L4 19 L8 11 Z" fill="#13201B" stroke="#FFFFFF" stroke-width="1.5" stroke-linejoin="round"/></svg>`;
  L.marker(at, { icon: L.divIcon({ className: "", html, iconSize: [26, 26] }), interactive: false, keyboard: false })
    .addTo(group);
}

function lineBadge(leg, group) {
  // Sit just before the line start, so it reads "enter here" and doesn't cover the first photo.
  const p = map.latLngToLayerPoint(leg.from), q = map.latLngToLayerPoint(leg.to);
  const len = Math.hypot(q.x - p.x, q.y - p.y) || 1;
  const at = map.layerPointToLatLng(L.point(p.x - (q.x - p.x) / len * 16, p.y - (q.y - p.y) / len * 16));
  L.marker(at, {
    icon: L.divIcon({ className: "", html: `<div class="line-badge${leg.done ? " is-done" : ""}">${leg.line}</div>`, iconSize: [22, 22] }),
    interactive: false, keyboard: false, zIndexOffset: 500,
  }).addTo(group);
}

function lineTooltip(leg) {
  const metres = leg.from.distanceTo(leg.to);
  const spacing = state.plan.photo_spacing_m;
  const photos = Math.floor(metres / spacing) + 1;
  const heading = (L.CRS.EPSG3857.project(leg.to).subtract(L.CRS.EPSG3857.project(leg.from)));
  const deg = (Math.atan2(heading.x, heading.y) * 180 / Math.PI + 360) % 360;
  return `Line ${leg.line} of ${leg.totalLines}, flight ${leg.flight}: heading ${fmt(deg)}°, `
    + `${fmt(metres)} m, ${photos} photos`;
}

function drawRoute(plan) {
  routeLayer.clearLayers();
  flightGroups = {};
  drawPhotos(plan);
  if (!plan) return;
  const legs = flightLegs(plan);
  drawDoneLines(plan);
  // Number every line (and give each an arrow) only when they're far enough apart on screen;
  // otherwise just each flight's first line.
  const spacingPx = plan.line_spacing_m / metresPerPixel();
  const numberAll = spacingPx >= 24;
  const firstLine = {};
  for (const leg of legs) {
    const group = flightGroups[leg.flight] || (flightGroups[leg.flight] = L.layerGroup().addTo(routeLayer));
    if (leg.landAt) {
      L.marker(leg.landAt, {
        icon: L.divIcon({ className: "", html: '<div class="land-mark" title="Lands here">L</div>', iconSize: [24, 24] }),
        interactive: false, keyboard: false, zIndexOffset: 600,
      }).addTo(group);
      continue;
    }
    const ab = [leg.from, leg.to];
    if (leg.photo) {
      L.polyline(ab, { color: "#FFFFFF", weight: 5, opacity: 0.55, interactive: false }).addTo(group);
      L.polyline(ab, { color: ROUTE, weight: 3, opacity: 0.95, interactive: false }).addTo(group);
      // Wide invisible hit line: easier to hover than the 3px stroke.
      L.polyline(ab, { color: "#000", weight: 16, opacity: 0 })
        .bindTooltip(() => lineTooltip(leg), { sticky: true, className: "route-tip" }).addTo(group);
      leg.first = !(leg.flight in firstLine);
      if (numberAll || leg.first) { lineBadge(leg, group); firstLine[leg.flight] = true; }
    } else {
      L.polyline(ab, { color: "#FFFFFF", weight: 4, opacity: 0.5, interactive: false }).addTo(group);
      L.polyline(ab, { color: ROUTE, weight: 2, opacity: 0.95, dashArray: "6 6", interactive: false }).addTo(group);
    }
    if ((leg.photo && (numberAll || leg.first)) || leg.kind === "out" || leg.kind === "back") {
      chevron(leg.from, leg.to, group, leg.photo ? plan.photo_spacing_m : null);
    }
  }
}

function drawDoneLines(plan) {
  // Lines left out with "Start at line": grey, numbered, not part of any flight.
  const done = plan.geojson.features.filter((f) => f.properties.kind === "done_line");
  if (!done.length) return;
  const group = L.layerGroup().addTo(routeLayer);
  const numberAll = plan.line_spacing_m / metresPerPixel() >= 24; // as for the route: only when legible
  for (const f of done) {
    const [a, b] = f.geometry.coordinates.map(([lon, lat]) => L.latLng(lat, lon));
    L.polyline([a, b], { color: "#FFFFFF", weight: 4, opacity: 0.5, interactive: false }).addTo(group);
    L.polyline([a, b], { color: "#6B7570", weight: 2, opacity: 0.9, dashArray: "2 5", interactive: false })
      .addTo(group);
    L.polyline([a, b], { color: "#000", weight: 16, opacity: 0 })
      .bindTooltip(`Line ${f.properties.line}: left out (start at line ${plan.from_line})`,
        { sticky: true, className: "route-tip" }).addTo(group);
    if (numberAll || f.properties.line === 1) lineBadge({ from: a, to: b, line: f.properties.line, done: true }, group);
  }
}

function highlightFlight(n) {
  for (const [k, g] of Object.entries(flightGroups)) {
    const dim = n !== null && Number(k) !== n;
    g.eachLayer((l) => {
      if (l instanceof L.Polyline) {
        if (l.options.baseOpacity === undefined) l.options.baseOpacity = l.options.opacity;
        if (l.options.baseOpacity > 0) l.setStyle({ opacity: dim ? 0.15 : l.options.baseOpacity }); // skip hit lines
      } else if (l.getElement && l.getElement()) {
        l.getElement().style.opacity = dim ? 0.25 : "";
      }
    });
  }
  for (const [k, g] of Object.entries(photoGroups)) {
    const dim = n !== null && Number(k) !== n;
    g.eachLayer((l) => l.setStyle({ opacity: dim ? 0.2 : 1, fillOpacity: dim ? 0.2 : 1 }));
  }
}

// Preview: a dot flies one flight's route, the only animation on the page, and only on request.
let preview = null;
function previewFlight(n) {
  if (preview) { cancelAnimationFrame(preview.raf); preview.marker.remove(); preview = null; }
  const legs = flightLegs(state.plan).filter((l) => l.flight === n && !l.landAt);
  if (!legs.length) return;
  const pts = [legs[0].from, ...legs.map((l) => l.to)];
  const cum = [0];
  for (let i = 1; i < pts.length; i++) cum.push(cum[i - 1] + pts[i - 1].distanceTo(pts[i]));
  const total = cum[cum.length - 1];
  const marker = L.marker(pts[0], {
    icon: L.divIcon({ className: "", html: '<div class="drone-dot"></div>', iconSize: [18, 18] }),
    interactive: false, keyboard: false, zIndexOffset: 2000,
  }).addTo(map);
  highlightFlight(n);
  const duration = Math.min(14000, Math.max(6000, total * 4)); // about 4 ms per metre flown
  const t0 = performance.now();
  preview = { marker, raf: 0 };
  const step = (now) => {
    const d = Math.min(1, (now - t0) / duration) * total;
    let i = 1;
    while (i < cum.length - 1 && cum[i] < d) i++;
    const f = (d - cum[i - 1]) / ((cum[i] - cum[i - 1]) || 1);
    marker.setLatLng(L.latLng(pts[i - 1].lat + f * (pts[i].lat - pts[i - 1].lat),
      pts[i - 1].lng + f * (pts[i].lng - pts[i - 1].lng)));
    if (d < total) preview.raf = requestAnimationFrame(step);
    else setTimeout(() => { marker.remove(); highlightFlight(null); preview = null; }, 600);
  };
  preview.raf = requestAnimationFrame(step);
}

// Map key: what the marks mean. Folds into a small button; open/closed is remembered.
const KEY_STORE = "anafi-planner.key-open";
const legend = L.control({ position: "bottomleft" });
legend.onAdd = () => {
  const box = L.DomUtil.create("div", "mapkey");
  const btn = L.DomUtil.create("button", "mapkey-button", box);
  btn.type = "button";
  btn.setAttribute("aria-controls", "map-key");
  const div = L.DomUtil.create("div", "legend", box);
  div.id = "map-key";
  div.innerHTML = `
    <div><svg width="28" height="10"><line x1="2" y1="5" x2="26" y2="5" stroke="${ROUTE}" stroke-width="3"/></svg>Photo line</div>
    <div><svg width="28" height="12"><line x1="2" y1="6" x2="26" y2="6" stroke="#13201B" stroke-width="9" stroke-opacity="0.75"/><line x1="2" y1="6" x2="26" y2="6" stroke="#FFD200" stroke-width="5"/></svg>Survey area</div>
    <div><svg width="28" height="10"><line x1="2" y1="5" x2="26" y2="5" stroke="${ROUTE}" stroke-width="2" stroke-dasharray="6 6"/></svg>Transit, no photos</div>
    <div><svg width="28" height="12"><path d="M7 0.5 L21 6 L7 11.5 L10 6 Z" fill="#13201B" stroke="#fff" stroke-width="1.2" stroke-linejoin="round"/></svg>Direction</div>
    <div><span class="line-badge small">3</span>Line order (starts here)</div>
    <div><svg width="28" height="12"><circle cx="14" cy="6" r="4" fill="#fff" stroke="#13201B" stroke-width="2"/></svg>Photo</div>`;
  let open = true;
  try { open = localStorage.getItem(KEY_STORE) !== "0"; } catch { /* storage blocked */ }
  const show = (on) => {
    open = on;
    div.hidden = !on;
    btn.setAttribute("aria-expanded", String(on));
    btn.textContent = on ? "Hide key" : "Map key";
    try { localStorage.setItem(KEY_STORE, on ? "1" : "0"); } catch { /* storage blocked */ }
  };
  btn.addEventListener("click", () => show(!open));
  show(open);
  L.DomEvent.disableClickPropagation(box);
  return box;
};
legend.addTo(map);
