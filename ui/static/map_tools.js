"use strict";
// Plan tab, map tools: help bar, drawing and editing the area, keyboard, GeoJSON.
// Plain scripts sharing one scope, loaded in order by index.html (see app.js).

// ---------- help bar at the bottom of the map: what you can do right now ----------
const HELP_STORE = "anafi-planner.edit-help";
const helpBar = L.DomUtil.create("div", "map-help", map.getContainer());
helpBar.setAttribute("aria-live", "polite");
L.DomEvent.disableClickPropagation(helpBar);
let editHelpOpen = true;
try { editHelpOpen = localStorage.getItem(HELP_STORE) !== "0"; } catch { /* storage blocked */ }
const HELP = {
  drawing: ["Drawing the area", [
    ["Click", "add a corner"], ["Drag a corner", "move it"], ["Backspace", "remove the last corner"],
    ["Click the first corner", "close the area"], ["Enter or Esc", "close the area"]]],
  home: ["Takeoff point", [["Click", "where the drone will take off"], ["Esc", "cancel"]]],
  editing: ["Editing the area", [
    ["Drag a corner", "move it"], ["Drag or click the + in a side", "add a corner"],
    ["Double-click or right-click a corner", "remove it"], ["Ctrl+Z", "undo"], ["Drag the H", "move the takeoff"]]],
};
function updateHelp() {
  const kind = state.mode === "drawing" ? "drawing" : state.mode === "home" ? "home"
    : state.area.length >= 3 && editHelpOpen ? "editing" : null;
  helpBar.replaceChildren();
  helpBar.hidden = !kind;
  $("help").hidden = kind !== null || state.area.length < 3;
  if (!kind) return;
  const [title, items] = HELP[kind];
  const h = document.createElement("b");
  h.textContent = title;
  const list = document.createElement("ul");
  for (const [key, what] of items) {
    const li = document.createElement("li");
    const k = document.createElement("kbd");
    k.textContent = key;
    li.append(k, ` ${what}`);
    list.append(li);
  }
  helpBar.append(h, list);
  if (kind === "editing") {
    const x = document.createElement("button");
    Object.assign(x, { type: "button", className: "map-help-close", textContent: "×", title: "Hide this help" });
    x.setAttribute("aria-label", "Hide the editing help");
    x.addEventListener("click", () => setEditHelp(false));
    helpBar.append(x);
  }
}
function setEditHelp(on) {
  editHelpOpen = on;
  try { localStorage.setItem(HELP_STORE, on ? "1" : "0"); } catch { /* storage blocked */ }
  updateHelp();
}

// ---------- tools ----------
function setMode(mode) {
  state.mode = mode;
  $("map").classList.toggle("is-drawing", mode !== "idle");
  $("draw").classList.toggle("is-active", mode === "drawing");
  $("draw").textContent = mode === "drawing" ? "Finish area" : "Draw area";
  $("set-home").classList.toggle("is-active", mode === "home");
  const hints = {
    drawing: "Click each corner of the area. Click the first corner or Finish area to close it.",
    home: "Click where the drone will take off.",
    idle: state.area.length >= 3
      ? "Drag a corner to move it, drag or click the + in a side to add one, double-click or right-click a corner to remove it."
      : "Draw the survey area: click its corners on the map.",
  };
  $("hint").textContent = hints[mode];
  drawArea();
  updateHelp();
}

function finishDrawing() {
  if (state.area.length < 3) { $("hint").textContent = "An area needs at least 3 corners."; return; }
  setMode(state.home ? "idle" : "home");
  if (state.mode === "home") $("hint").textContent = "Area closed. Now click where the drone will take off.";
  replanSoon();
}

$("draw").addEventListener("click", () => {
  if (state.mode === "drawing") return finishDrawing();
  if (state.area.length) rememberArea();
  state.area = [];
  state.plan = null;
  resetFromLine();
  drawRoute(null);
  renderPlan(null);
  setMode("drawing");
});
$("set-home").addEventListener("click", () => setMode(state.mode === "home" ? "idle" : "home"));
$("clear").addEventListener("click", () => {
  if (state.area.length) rememberArea();
  state.area = []; state.home = null; state.plan = null;
  resetFromLine();
  drawHome(); drawRoute(null); renderPlan(null); setMode("idle"); saveState();
});

map.on("click", (e) => {
  const p = toPoint(e.latlng);
  if (state.mode === "drawing") { state.area.push(p); drawArea(); }
  else if (state.mode === "home") { state.home = p; drawHome(); setMode("idle"); replanSoon(); }
});
map.on("moveend", saveState);
map.on("zoomend", () => drawRoute(state.plan)); // chevrons and badges are placed in screen space
$("show-photos").addEventListener("change", () => { drawPhotos(state.plan); saveState(); });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && state.mode !== "idle") state.mode === "drawing" ? finishDrawing() : setMode("idle");
  if (state.mode === "drawing" && !e.target.matches("input, textarea, select")) {
    if (e.key === "Enter") { e.preventDefault(); finishDrawing(); }
    if (e.key === "Backspace" && state.area.length) { e.preventDefault(); state.area.pop(); drawArea(); }
  }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z" && !e.target.matches("input, textarea, select")) {
    e.preventDefault();
    undoArea();
  }
});
$("undo").addEventListener("click", undoArea);
$("help").addEventListener("click", () => setEditHelp(true));

$("load").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  try {
    const ring = firstRing(JSON.parse(await file.text()));
    state.area = ring;
    state.plan = null;
    resetFromLine();
    setMode(state.home ? "idle" : "home");
    if (state.mode === "home") $("hint").textContent = `Loaded ${file.name}. Now click where the drone will take off.`;
    map.fitBounds(L.latLngBounds(ring), { padding: [40, 40] });
    replanSoon();
  } catch (err) {
    $("hint").textContent = `Couldn't load ${file.name}: ${err.message}`;
  }
});

function firstRing(obj) {
  const geoms = obj.type === "FeatureCollection" ? obj.features.map((f) => f.geometry)
    : obj.type === "Feature" ? [obj.geometry] : [obj];
  for (const g of geoms) {
    if (!g) continue;
    const ring = g.type === "Polygon" ? g.coordinates[0] : g.type === "MultiPolygon" ? g.coordinates[0][0] : null;
    if (!ring) continue;
    if (ring.some(([lon, lat]) => Math.abs(lon) > 180 || Math.abs(lat) > 90)) {
      throw new Error("coordinates aren't longitude/latitude degrees; export it as EPSG:4326");
    }
    const pts = ring.map(([lon, lat]) => [lat, lon]);
    const [a, b] = [pts[0], pts[pts.length - 1]];
    if (a[0] === b[0] && a[1] === b[1]) pts.pop();
    return pts;
  }
  throw new Error("no polygon in the file");
}

document.querySelector(".panel").addEventListener("input", (e) => {
  const k = e.target.name || e.target.id;
  if (k === "angle-mode") syncAngleMode();
  if (PARAMS.includes(k) || k === "from_line") replanSoon();
});
