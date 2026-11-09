"use strict";
// Views (Plan / Fly / Batteries) and start-up. Loaded last of the planner scripts.
// Plain scripts sharing one scope, loaded in order by index.html (see app.js).

// ---------- views (Plan / Batteries) ----------
// Plan's own map layers: hidden while flying, so the map shows only the mission being flown.
function showPlanLayers(on) {
  for (const layer of [areaLayer, routeLayer, photoLayer, vertexLayer, homeMarker]) {
    if (!layer) continue;
    if (on && !map.hasLayer(layer)) layer.addTo(map);
    if (!on && map.hasLayer(layer)) layer.remove();
  }
}

function setView() {
  const view = { "#batteries": "batteries", "#fly": "fly" }[location.hash] || "plan";
  document.body.dataset.view = view;
  $("batteries-view").hidden = view !== "batteries";
  $("plan-panel").hidden = view === "fly";
  $("fly-panel").hidden = view !== "fly";
  if (view === "fly" && state.mode !== "idle") setMode("idle");
  showPlanLayers(view !== "fly");
  for (const a of document.querySelectorAll(".mode")) {
    const on = a.dataset.view === view;
    a.classList.toggle("is-active", on);
    if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  }
  if (view !== "batteries") map.invalidateSize(); // the map had no size while hidden
  window.dispatchEvent(new CustomEvent("viewchange", { detail: view }));
}
window.addEventListener("hashchange", setView);

// ---------- start ----------
async function start() {
  try {
    const cfg = await (await fetch("/api/config")).json();
    setupBaseMaps(cfg.tiles);
    setupImageryInfo();
    buildChips("altitude", cfg.altitudes.map((a) => ({ value: a.m, ...a })),
      (a) => `${a.m}<small>${fmt(a.gsd_cm, 1)} cm</small>`);
    buildChips("speed", cfg.speeds.map((v) => ({ value: v })), (v) => `${v.value}<small>m/s</small>`);
    $("endurance").value = cfg.endurance_min;
    $("reserve").value = cfg.reserve_pct;
    $("photo_gap").value = cfg.min_photo_interval_s;
  } catch {
    if (!document.body.dataset.basemap) setupBaseMaps(null);
    $("hint").textContent = "Couldn't reach the planner. Is anafi-gui still running?";
  }
  loadState();
  syncAngleMode();
  if (state.area.length >= 3) map.fitBounds(L.latLngBounds(state.area.concat(state.home ? [state.home] : [])), { padding: [40, 40] });
  else if (state.view) map.setView([state.view[0], state.view[1]], state.view[2]);
  else map.setView([20, 0], 2); // whole world until you draw an area
  drawHome();
  setMode("idle");
  replanSoon();
  checkPhone();
  loadMissionList();
  setView();
}
start();
