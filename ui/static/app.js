"use strict";
// Plan tab, shared parts: helpers, parameters, saved state and the map. The planner is split
// into plain scripts that share one scope, loaded in this order by index.html: app.js,
// map_layers.js, map_route.js, map_tools.js, plan_panel.js, main.js (then batteries.js, fly.js).
// Code that runs at load time may only use names from its own file or earlier ones.

const $ = (id) => document.getElementById(id);
const PARAMS = ["altitude", "speed", "front", "side", "photo_gap", "angle-mode", "angle", "end", "endurance", "reserve"];
const CHOICES = ["altitude", "speed", "angle-mode"]; // radio groups; the rest are plain inputs
const STORE = "anafi-planner.v1";
const STATUS_WORDS = { ok: "fits", tight: "tight, no wind margin", exceeds: "too long for one battery" };

// ---------- parameters ----------
function getParam(k) {
  if (!CHOICES.includes(k)) return $(k).value;
  const checked = document.querySelector(`input[name="${k}"]:checked`);
  return checked ? checked.value : null;
}

function setParam(k, v) {
  if (!CHOICES.includes(k)) { $(k).value = v; return; }
  const radios = [...document.querySelectorAll(`input[name="${k}"]`)];
  if (!radios.length) return;
  // Older saved values may not be among the choices: take the nearest one.
  const num = Number(v);
  const pick = radios.find((r) => r.value === String(v))
    || (Number.isFinite(num) && radios.reduce((a, b) => (Math.abs(b.value - num) < Math.abs(a.value - num) ? b : a)))
    || radios[0];
  pick.checked = true;
}

function buildChips(id, values, label) {
  const box = $(id);
  box.innerHTML = values.map((v) => `
    <label class="chip"><input type="radio" name="${id}" value="${v.value}"><span>${label(v)}</span></label>`).join("");
  setParam(id, box.dataset.default);
}

// "Start at line" belongs to one survey: never remembered, back to 1 for any other area.
function fromLine() { return Math.max(1, Math.round(Number($("from_line").value)) || 1); }
function resetFromLine(n = 1) { $("from_line").value = n; $("resume-box").open = n > 1; }

function syncAngleMode() {
  const set = getParam("angle-mode") === "set";
  $("angle").disabled = !set;
  if (set && $("angle").value === "" && state.plan) $("angle").value = Math.round(state.plan.angle_deg) % 180;
}

// ---------- state ----------
const state = { area: [], home: null, mode: "idle", plan: null, view: null };

function loadState() {
  try {
    const s = JSON.parse(localStorage.getItem(STORE) || "{}");
    state.area = s.area || [];
    state.home = s.home || null;
    state.view = s.view || null;
    if (typeof s.showPhotos === "boolean") $("show-photos").checked = s.showPhotos;
    // 21 min was the default before the flight logs showed today's batteries last about 15.
    if (s.params && String(s.params.endurance) === "21") delete s.params.endurance;
    for (const k of PARAMS) if (s.params && k in s.params) setParam(k, s.params[k]);
  } catch { /* first run or storage blocked */ }
}

function saveState() {
  const params = Object.fromEntries(PARAMS.map((k) => [k, getParam(k)]));
  const c = map.getCenter();
  try {
    localStorage.setItem(STORE, JSON.stringify({
      area: state.area, home: state.home, params, view: [c.lat, c.lng, map.getZoom()],
      showPhotos: $("show-photos").checked,
    }));
  } catch { /* storage blocked: nothing to remember */ }
}

// ---------- map ----------
const map = L.map("map", { zoomControl: true, attributionControl: true });
map.attributionControl.setPrefix('<a href="https://leafletjs.com">Leaflet</a>');
L.control.scale({ imperial: false, position: "bottomright" }).addTo(map);
