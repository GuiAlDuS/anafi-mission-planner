"use strict";

const $ = (id) => document.getElementById(id);
const PARAMS = ["altitude", "speed", "front", "side", "angle-mode", "angle", "end", "endurance", "reserve"];
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

// Base maps come from the server (it fetches and caches the tiles, so areas seen at home work
// offline in the field). The choice is remembered per browser.
const BASEMAP_STORE = "anafi-planner.basemap";
const LAYERS_ICON = '<svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true"><path d="M9 2 16 6 9 10 2 6Z" '
  + 'fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M2.5 9.5 9 13.2 15.5 9.5" '
  + 'fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/></svg>';

// Your layers over the base map: orthophotos (your fields) and WMS layers (e.g. government imagery).
const LAYERS_STORE = "anafi-planner.orthos"; // ids of the layers switched on
const overlays = new Map(); // id -> { kind: "ortho" | "wms", meta, layer }
let orthoState = { available: false, missing: "", items: [] };
let wmsItems = [];
function visibleOverlays() { return [...overlays.values()].filter((o) => map.hasLayer(o.layer)); }
function saveLayerVisibility() {
  try { localStorage.setItem(LAYERS_STORE, JSON.stringify(visibleOverlays().map((o) => o.meta.id))); } catch { /* blocked */ }
}
function overlayBounds(meta) {
  const b = meta.bounds || meta.bbox;
  return b ? L.latLngBounds([b[0], b[1]], [b[2], b[3]]) : null;
}

function syncOverlays(kind, items, showIds) {
  for (const [id, o] of overlays) {
    if (o.kind === kind && !items.some((m) => m.id === id)) { o.layer.remove(); overlays.delete(id); }
  }
  for (const meta of items) {
    if (meta.error || overlays.has(meta.id)) continue;
    const bounds = overlayBounds(meta);
    const layer = kind === "ortho"
      ? L.tileLayer(`/tiles/ortho/${encodeURIComponent(meta.id)}/{z}/{x}/{y}`, {
        maxZoom: 22, maxNativeZoom: Math.min(22, meta.max_zoom), bounds, zIndex: 5, attribution: "Your orthophoto" })
      : L.tileLayer(`/tiles/wms/${encodeURIComponent(meta.id)}/{z}/{x}/{y}`, {
        maxZoom: 22, maxNativeZoom: 21, zIndex: 4, attribution: meta.service, ...(bounds ? { bounds } : {}) });
    overlays.set(meta.id, { kind, meta, layer });
    if (showIds.includes(meta.id)) layer.addTo(map);
  }
}

function setupBaseMaps(sources) {
  if (!sources?.length) sources = [{ id: "imagery", label: "Esri satellite", max_zoom: 19, note: "",
    attribution: "Imagery © Esri, Maxar, Earthstar Geographics" }];
  const layers = new Map(sources.map((t) => [t.id, L.tileLayer(`/tiles/${t.id}/{z}/{x}/{y}`, {
    maxZoom: 22, maxNativeZoom: t.max_zoom, attribution: t.attribution })]));
  let chosen = null;
  try { chosen = localStorage.getItem(BASEMAP_STORE); } catch { /* storage blocked */ }
  let current = layers.has(chosen) ? chosen : sources[0].id;
  layers.get(current).addTo(map);
  document.body.dataset.basemap = current;

  // One compact button under the zoom buttons; the list opens only when asked for.
  const ctl = L.control({ position: "topleft" });
  ctl.onAdd = () => {
    const box = L.DomUtil.create("div", "basemap");
    const btn = L.DomUtil.create("button", "basemap-button", box);
    btn.type = "button";
    btn.setAttribute("aria-haspopup", "true");
    btn.setAttribute("aria-expanded", "false");
    const list = L.DomUtil.create("div", "basemap-list", box);
    list.setAttribute("role", "menu");
    list.hidden = true;
    const label = () => sources.find((t) => t.id === current).label + (visibleOverlays().length ? " + yours" : "");
    const paint = () => { btn.innerHTML = `${LAYERS_ICON}<span></span>`; btn.querySelector("span").textContent = label();
      btn.title = `Base map: ${label()}`; };
    const entry = (role, title, note) => {
      const it = L.DomUtil.create("button", "basemap-item", list);
      it.type = "button";
      it.setAttribute("role", role);
      const name = document.createElement("b");
      name.textContent = title;
      it.append(name);
      if (note) { const n = document.createElement("small"); n.textContent = note; it.append(n); }
      return it;
    };
    const baseHead = L.DomUtil.create("div", "basemap-head is-first", list);
    baseHead.textContent = "Base map";
    const items = sources.map((t) => {
      const it = entry("menuitemradio", t.label, t.note);
      it.addEventListener("click", () => { choose(t.id); close(); btn.focus(); });
      return [t.id, it];
    });
    const mark = () => items.forEach(([id, it]) => it.setAttribute("aria-checked", String(id === current)));

    // Your layers: orthophotos and WMS layers
    const head = L.DomUtil.create("div", "basemap-head", list);
    head.textContent = "Your layers";
    const orthoBox = L.DomUtil.create("div", "basemap-orthos", list);
    const file = L.DomUtil.create("input", "", box);
    file.type = "file";
    file.accept = ".tif,.tiff,image/tiff";
    file.hidden = true;
    const status = L.DomUtil.create("p", "basemap-status", list);
    status.setAttribute("aria-live", "polite");

    function layerRow(kind, meta, title, note, removeUrl, confirmText) {
      const row = L.DomUtil.create("div", "ortho-row", orthoBox);
      const o = overlays.get(meta.id);
      const it = entry("menuitemcheckbox", title, note);
      row.append(it);
      const on = map.hasLayer(o.layer);
      it.setAttribute("aria-checked", String(on));
      it.title = on ? "Turn this layer off" : "Turn this layer on";
      // A visible on/off switch: the state is in the words too, not only in the knob's position.
      const sw = document.createElement("span");
      sw.className = "switch";
      sw.setAttribute("aria-hidden", "true");
      const track = document.createElement("span");
      track.className = "track";
      track.append(document.createElement("span"));
      const word = document.createElement("span");
      word.className = "state";
      word.textContent = on ? "On" : "Off";
      sw.append(track, word);
      it.append(sw);
      it.addEventListener("click", () => {
        if (map.hasLayer(o.layer)) o.layer.remove();
        else {
          o.layer.addTo(map);
          const b = overlayBounds(meta);
          if (b && !map.getBounds().intersects(b)) map.fitBounds(b, { padding: [30, 30] });
          revealInfo();
        }
        saveLayerVisibility(); renderLayers(); paint(); updateImageryInfo();
      });
      const rm = L.DomUtil.create("button", "text-button ortho-remove", row);
      rm.type = "button";
      rm.textContent = "Remove";
      rm.setAttribute("aria-label", `Remove ${title}`);
      rm.addEventListener("click", async () => {
        if (!confirm(confirmText)) return;
        const res2 = await fetch(removeUrl, { method: "POST",
          headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: meta.id }) });
        const out = await res2.json();
        if (!res2.ok) { status.textContent = out.error; return; }
        if (kind === "ortho") orthoState.items = out.items; else wmsItems = out.items;
        syncOverlays(kind, out.items, []);
        saveLayerVisibility(); renderLayers(); paint(); updateImageryInfo();
      });
    }

    function renderLayers() {
      orthoBox.replaceChildren();
      for (const meta of wmsItems) {
        layerRow("wms", meta, meta.title, `WMS: ${meta.service}`, "/api/wms/remove",
          `Remove the WMS layer "${meta.title}" from the planner?`);
      }
      for (const meta of orthoState.items) {
        const row = L.DomUtil.create("div", "ortho-row", orthoBox);
        if (meta.error) {
          const bad = document.createElement("p");
          bad.className = "basemap-status is-error";
          bad.textContent = `${meta.name}: ${meta.error}`;
          row.append(bad);
          continue;
        }
        row.remove();
        const res = meta.resolution_m < 1 ? `${Math.round(meta.resolution_m * 100)} cm` : `${meta.resolution_m.toFixed(1)} m`;
        layerRow("ortho", meta, meta.name, `${niceDate(meta.date)}, ${res} per pixel`, "/api/orthophotos/remove",
          `Remove ${meta.name} from the planner? Your original file isn't touched.`);
      }
      if (!wmsItems.length && !orthoState.items.length) {
        const none = document.createElement("p");
        none.className = "basemap-empty";
        none.textContent = "None yet. Add your orthophoto or a WMS layer below; it shows over the base map.";
        orthoBox.append(none);
      }
      const addHead = document.createElement("div");
      addHead.className = "basemap-head";
      addHead.textContent = "Add a layer";
      orthoBox.append(addHead);
      const load = entry("menuitem", "Load your own orthophoto…",
        orthoState.available ? "A GeoTIFF, e.g. from WebODM" : orthoState.missing);
      orthoBox.append(load);
      load.classList.add("ortho-load");
      load.disabled = !orthoState.available;
      load.addEventListener("click", () => file.click());
      const add = entry("menuitem", "Add a WMS layer…", "A map service by its web address, e.g. national orthophotos");
      orthoBox.append(add);
      add.classList.add("ortho-load");
      add.setAttribute("aria-expanded", String(!wmsForm.hidden));
      add.addEventListener("click", () => { wmsForm.hidden = !wmsForm.hidden; renderLayers(); if (!wmsForm.hidden) wmsUrl.focus(); });
      orthoBox.append(wmsForm);
    }

    // Add a WMS layer: address -> its layer list -> pick one.
    const wmsForm = document.createElement("form");
    wmsForm.className = "wms-form";
    wmsForm.hidden = true;
    const wmsUrl = document.createElement("input");
    Object.assign(wmsUrl, { type: "url", required: true, placeholder: "https://… WMS address" });
    wmsUrl.setAttribute("aria-label", "WMS address");
    const findBtn = document.createElement("button");
    Object.assign(findBtn, { type: "submit", className: "secondary", textContent: "Find layers" });
    const pick = document.createElement("select");
    pick.setAttribute("aria-label", "WMS layer");
    pick.hidden = true;
    const addBtn = document.createElement("button");
    Object.assign(addBtn, { type: "button", className: "primary", textContent: "Add layer", hidden: true });
    wmsForm.append(wmsUrl, findBtn, pick, addBtn);
    let caps = null;
    wmsForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      status.textContent = "Reading the layer list…";
      pick.hidden = addBtn.hidden = true;
      try {
        const res = await fetch("/api/wms/capabilities", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url: wmsUrl.value }) });
        caps = await res.json();
        if (!res.ok) throw new Error(caps.error);
        pick.replaceChildren(...caps.layers.map((lyr) => {
          const o = document.createElement("option");
          o.value = lyr.name;
          o.textContent = lyr.crs ? lyr.title : `${lyr.title} (not in web map projection)`;
          o.disabled = !lyr.crs;
          return o;
        }));
        const first = caps.layers.find((lyr) => lyr.crs);
        if (first) pick.value = first.name;
        pick.hidden = addBtn.hidden = false;
        status.textContent = `${caps.title}: ${caps.layers.length} layer${caps.layers.length === 1 ? "" : "s"}. Pick one.`;
      } catch (err) {
        status.textContent = `Not found: ${err.message}`;
      }
    });
    addBtn.addEventListener("click", async () => {
      try {
        const res = await fetch("/api/wms/add", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url: caps.url, name: pick.value }) });
        const out = await res.json();
        if (!res.ok) throw new Error(out.error);
        wmsItems = [...wmsItems.filter((w) => w.id !== out.id), out];
        syncOverlays("wms", wmsItems, [out.id]);
        status.textContent = `Added ${out.title}. Areas you view are saved for offline use.`;
        wmsForm.hidden = true;
        pick.hidden = addBtn.hidden = true;
        wmsUrl.value = "";
        revealInfo();
        saveLayerVisibility(); renderLayers(); paint(); updateImageryInfo();
        close(); btn.focus();
      } catch (err) {
        status.textContent = `Not added: ${err.message}`;
      }
    });

    file.addEventListener("change", () => {
      const f = file.files[0];
      file.value = "";
      if (!f) return;
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/orthophotos/upload");
      xhr.setRequestHeader("X-Filename", encodeURIComponent(f.name));
      const d = new Date(f.lastModified); // local date: an evening flight is already tomorrow in UTC
      xhr.setRequestHeader("X-File-Date",
        `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`);
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) status.textContent = `Loading ${f.name}: ${Math.round(100 * e.loaded / e.total)} %`;
      };
      xhr.upload.onload = () => { status.textContent = `Preparing ${f.name} for the map…`; };
      xhr.onload = () => {
        let out = {};
        try { out = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
        if (xhr.status !== 200) { status.textContent = `Not loaded: ${out.error || `HTTP ${xhr.status}`}`; return; }
        status.textContent = `Loaded ${out.name}.`;
        orthoState.items = [...orthoState.items, out];
        syncOverlays("ortho", orthoState.items, [out.id]);
        map.fitBounds(overlayBounds(out), { padding: [30, 30] });
        revealInfo();
        saveLayerVisibility(); renderLayers(); paint(); updateImageryInfo();
        close();
      };
      xhr.onerror = () => { status.textContent = "Not loaded: lost the connection to anafi-gui."; };
      status.textContent = `Loading ${f.name}…`;
      xhr.send(f);
    });

    const focusables = () => [...list.querySelectorAll("button:not(:disabled)")];
    // While the list is open the image note (under the button) would peek out beside it: hide it.
    const open = () => { list.hidden = false; btn.setAttribute("aria-expanded", "true");
      document.body.classList.add("basemap-open");
      (items.find(([id]) => id === current)?.[1] || items[0][1]).focus(); };
    const close = () => { list.hidden = true; btn.setAttribute("aria-expanded", "false");
      document.body.classList.remove("basemap-open"); };
    function choose(id) {
      if (id === current) return;
      map.removeLayer(layers.get(current));
      current = id;
      layers.get(id).addTo(map);
      document.body.dataset.basemap = id;
      try { localStorage.setItem(BASEMAP_STORE, id); } catch { /* storage blocked */ }
      paint(); mark();
      revealInfo();
    }
    btn.addEventListener("click", () => (list.hidden ? open() : close()));
    box.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !list.hidden) { close(); btn.focus(); }
      if (e.target.matches("input, select")) return; // arrows belong to the field
      if ((e.key === "ArrowDown" || e.key === "ArrowUp") && !list.hidden) {
        e.preventDefault();
        const els = focusables();
        const i = els.indexOf(document.activeElement);
        els[(i + (e.key === "ArrowDown" ? 1 : els.length - 1)) % els.length].focus();
      }
    });
    document.addEventListener("pointerdown", (e) => { if (!box.contains(e.target)) close(); });
    // Escape closes the list wherever focus is (e.g. after the WMS form disappears).
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !list.hidden) { close(); btn.focus(); } });

    let shown = [];
    try { shown = JSON.parse(localStorage.getItem(LAYERS_STORE) || "[]"); } catch { /* blocked */ }
    Promise.all([fetch("/api/orthophotos").then((r) => r.json()), fetch("/api/wms").then((r) => r.json())])
      .then(([st, w]) => {
        orthoState = st;
        wmsItems = w.items;
        syncOverlays("ortho", st.items, shown);
        syncOverlays("wms", w.items, shown);
        renderLayers(); paint(); updateImageryInfo();
      }).catch(() => renderLayers());
    renderLayers(); paint(); mark();
    L.DomEvent.disableClickPropagation(box);
    L.DomEvent.disableScrollPropagation(box);
    return box;
  };
  ctl.addTo(map);
}

// Imagery info: when and how sharp the satellite image is where you're planning. Esri mixes
// captures by zoom level (the closest zoom can be years older), so it follows the zoom.
const imageryCache = new Map();
let imageryTimer = null;
let imageryBox = null;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const niceDate = (iso) => { const [y, m, d] = iso.split("-").map(Number); return `${d} ${MONTHS[m - 1]} ${y}`; };
function ageText(iso) {
  const months = Math.max(0, Math.round((Date.now() - new Date(iso)) / (30.44 * 864e5)));
  if (months < 1) return "this month";
  if (months < 12) return `${months} month${months === 1 ? "" : "s"} old`;
  const y = Math.floor(months / 12), m = months % 12;
  return `${y} year${y === 1 ? "" : "s"}${m ? ` ${m} month${m === 1 ? "" : "s"}` : ""} old`;
}

const INFO_STORE = "anafi-planner.info-open";
let infoOpen = true;
try { infoOpen = localStorage.getItem(INFO_STORE) !== "0"; } catch { /* storage blocked */ }
function setInfoOpen(on) {
  infoOpen = on;
  try { localStorage.setItem(INFO_STORE, on ? "1" : "0"); } catch { /* storage blocked */ }
  updateImageryInfo();
}
const revealInfo = () => setInfoOpen(true); // a new image on the map: worth a look

// Folded: a small "Image info" button; open: the note with a close button.
function finishInfo() {
  const box = imageryBox;
  if (box.hidden) return;
  if (!infoOpen) {
    const b = document.createElement("button");
    Object.assign(b, { type: "button", className: "imagery-toggle", textContent: "Image info" });
    b.addEventListener("click", () => setInfoOpen(true));
    box.replaceChildren(b);
    box.classList.add("is-folded");
    return;
  }
  box.classList.remove("is-folded");
  const x = document.createElement("button");
  Object.assign(x, { type: "button", className: "imagery-close", textContent: "×", title: "Hide image info" });
  x.setAttribute("aria-label", "Hide image info");
  x.addEventListener("click", () => setInfoOpen(false));
  box.prepend(x);
}

function setupImageryInfo() {
  const ctl = L.control({ position: "topleft" });
  ctl.onAdd = () => {
    imageryBox = L.DomUtil.create("div", "imagery-info");
    imageryBox.setAttribute("aria-live", "polite");
    imageryBox.hidden = true;
    L.DomEvent.disableClickPropagation(imageryBox);
    return imageryBox;
  };
  ctl.addTo(map);
  map.on("moveend", updateImageryInfo);
  updateImageryInfo();
}

function updateImageryInfo() {
  if (!imageryBox) return;
  clearTimeout(imageryTimer);
  if (document.body.dataset.basemap !== "imagery" && !visibleOverlays().length) { imageryBox.hidden = true; return; }
  imageryTimer = setTimeout(async () => {
    let at = map.getCenter(), where = "map centre";
    if (state.area.length >= 3) {
      const c = L.latLngBounds(state.area).getCenter();
      if (map.getBounds().contains(c)) { at = c; where = "survey area"; }
    }
    const own = visibleOverlays().sort((a, b) => (a.kind === "ortho" ? -1 : 1) - (b.kind === "ortho" ? -1 : 1))
      .find((o) => !overlayBounds(o.meta) || overlayBounds(o.meta).contains(at));
    if (own) {
      if (own.kind === "ortho") buildOrthoInfo(own.meta, where); else buildWmsInfo(own.meta, where);
      finishInfo();
      return;
    }
    if (document.body.dataset.basemap !== "imagery") { imageryBox.hidden = true; return; }
    const key = `${at.lat.toFixed(3)},${at.lng.toFixed(3)}`;
    let info = imageryCache.get(key);
    if (!info) {
      try {
        info = await (await fetch(`/api/imagery?lat=${at.lat.toFixed(5)}&lon=${at.lng.toFixed(5)}`)).json();
        if (info.captures?.length) imageryCache.set(key, info);
      } catch { info = { captures: [], offline: true }; }
    }
    buildImageryInfo(info, where);
    finishInfo();
  }, 350);
}

function buildWmsInfo(meta, where) {
  const box = imageryBox;
  box.replaceChildren();
  box.hidden = false;
  box.classList.remove("is-old");
  const lead = document.createElement("p");
  lead.className = "img-lead";
  lead.textContent = meta.title;
  const facts = document.createElement("p");
  facts.className = "img-facts";
  facts.textContent = `WMS layer from ${meta.service}. At the ${where}.`;
  box.append(lead, facts);
  if (meta.abstract && meta.abstract !== meta.title) {
    const more = document.createElement("p");
    more.className = "img-facts";
    more.textContent = meta.abstract.length > 160 ? `${meta.abstract.slice(0, 157)}…` : meta.abstract;
    box.append(more);
  }
}

function buildOrthoInfo(meta, where) {
  const box = imageryBox;
  box.replaceChildren();
  box.hidden = false;
  box.classList.remove("is-old");
  const lead = document.createElement("p");
  lead.className = "img-lead";
  lead.textContent = `Your orthophoto, ${meta.date_kind === "file" ? "file from " : ""}${niceDate(meta.date)}`;
  const age = document.createElement("span");
  age.textContent = ageText(meta.date);
  lead.append(" ", age);
  const facts = document.createElement("p");
  facts.className = "img-facts";
  const res = meta.resolution_m < 1 ? `${Math.round(meta.resolution_m * 100)} cm` : `${meta.resolution_m.toFixed(1)} m`;
  const from = meta.date_kind === "name" ? " Date taken from the file name." : "";
  facts.textContent = `${meta.name}, ${res} per pixel. At the ${where}.${from}`;
  box.append(lead, facts);
}

function buildImageryInfo(info, where) {
  const box = imageryBox;
  box.replaceChildren();
  if (document.body.dataset.basemap !== "imagery") { box.hidden = true; return; }
  const z = Math.min(Math.round(map.getZoom()), 19);
  const caps = info.captures || [];
  const cap = caps.find((c) => c.from_zoom <= z && z <= c.to_zoom);
  const line = (cls, text) => { const p = document.createElement("p"); p.className = cls; p.textContent = text; box.append(p); return p; };
  box.hidden = false;
  box.classList.remove("is-old");
  if (!cap) {
    line("img-lead", info.offline ? "Image date unknown offline" : "No image details here");
    return;
  }
  if (!cap.date) {
    line("img-lead", "Zoom in to see when the image was taken");
    return;
  }
  const lead = line("img-lead", `Image from ${niceDate(cap.date)}`);
  const age = document.createElement("span");
  age.textContent = ageText(cap.date);
  lead.append(" ", age);
  const facts = [cap.sensor, cap.resolution_m !== null ? `${cap.resolution_m} m per pixel` : null,
    cap.accuracy_m !== null ? `placed within ${Math.round(cap.accuracy_m)} m` : null].filter(Boolean);
  line("img-facts", `${facts.join(", ")}. At the ${where}.`);
  const closer = caps.find((c) => c.from_zoom > z && c.date && c.date !== cap.date);
  if (closer) line("img-facts", `Closer zoom shows a ${closer.date.slice(0, 4)} image.`);
  const years = (Date.now() - new Date(cap.date)) / (365.25 * 864e5);
  if (years >= 3) {
    box.classList.add("is-old");
    line("img-warn", "Old image: check for new trees, buildings or wires.");
  }
}

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
    m.on("drag", (e) => { state.area[i] = [e.latlng.lat, e.latlng.lng]; reshape(); });
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
      const ll = e.target.getLatLng();
      add([ll.lat, ll.lng]);
      mids.filter((mm) => mm !== h).forEach((mm) => mm.remove());
    });
    h.on("drag", (e) => { state.area[i + 1] = [e.latlng.lat, e.latlng.lng]; reshape(); });
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
  homeMarker = L.marker(state.home, { icon: homeIcon, draggable: true, zIndexOffset: 1000 }).addTo(map);
  homeMarker.on("dragend", (e) => {
    const ll = e.target.getLatLng();
    state.home = [ll.lat, ll.lng];
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
  let lineNo = 0;
  const totalLines = plan.lines;
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
    icon: L.divIcon({ className: "", html: `<div class="line-badge">${leg.line}</div>`, iconSize: [22, 22] }),
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
  drawRoute(null);
  renderPlan(null);
  setMode("drawing");
});
$("set-home").addEventListener("click", () => setMode(state.mode === "home" ? "idle" : "home"));
$("clear").addEventListener("click", () => {
  if (state.area.length) rememberArea();
  state.area = []; state.home = null; state.plan = null;
  drawHome(); drawRoute(null); renderPlan(null); setMode("idle"); saveState();
});

map.on("click", (e) => {
  const p = [e.latlng.lat, e.latlng.lng];
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
  if (PARAMS.includes(k)) replanSoon();
});

// ---------- planning ----------
let timer = null;
let requestId = 0;

function request() {
  const params = Object.fromEntries(PARAMS.map((k) => [k, getParam(k)]));
  if (params["angle-mode"] !== "set" || params.angle === "") params.angle = null;
  delete params["angle-mode"];
  return { area: state.area, home: state.home, ...params };
}

function replanSoon() {
  saveState();
  updateImageryInfo();
  clearTimeout(timer);
  if (state.area.length < 3 || state.mode === "drawing") return;
  timer = setTimeout(replan, 250);
}

async function replan() {
  const id = ++requestId;
  try {
    const res = await fetch("/api/plan", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request()),
    });
    const body = await res.json();
    if (id !== requestId) return; // a newer request is on its way
    if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
    state.plan = body;
    drawRoute(body);
    renderPlan(body);
  } catch (err) {
    if (id !== requestId) return;
    state.plan = null;
    drawRoute(null);
    renderPlan(null, err.message);
  }
}

function mmss(s) {
  const t = Math.round(s);
  const pad = (n) => String(n).padStart(2, "0");
  return t >= 3600 ? `${Math.floor(t / 3600)} h ${pad(Math.floor(t / 60) % 60)} min` : `${Math.floor(t / 60)}:${pad(t % 60)}`;
}
const fmt = (v, d = 0) => Number(v).toFixed(d);

function renderPlan(p, error) {
  const readout = $("readout");
  const flights = $("flights");
  const notes = $("notes");
  flights.innerHTML = "";
  notes.innerHTML = "";
  $("save").disabled = !p;
  updateSend();
  $("angle-note").textContent = "";
  if (!p) {
    readout.className = error ? "readout is-error" : "readout is-empty";
    readout.textContent = error || "Draw an area to see flight time, photos and batteries.";
    $("summary").textContent = "The grid and the split into batteries appear here once the area is drawn.";
    return;
  }
  readout.className = "readout";
  readout.innerHTML = `
    <div class="stat"><b>${mmss(p.total_s)}</b><small>flight time</small></div>
    <div class="stat"><b>${p.photos}</b><small>photos</small></div>
    <div class="stat"><b>${p.flights.length}</b><small>${p.flights.length === 1 ? "battery" : "batteries"}</small></div>`;
  $("summary").textContent = `${fmt(p.gsd_cm, 1)} cm per pixel. ${p.lines} lines, ${fmt(p.line_spacing_m, 1)} m apart `
    + `(side overlap ${fmt(p.side_overlap * 100)}%). A photo every ${fmt(p.photo_spacing_m, 1)} m; `
    + `each covers ${fmt(p.footprint_m[0])} × ${fmt(p.footprint_m[1])} m.`;
  if (getParam("angle-mode") === "auto") {
    $("angle").placeholder = fmt(p.angle_deg % 180);
    $("angle-note").textContent = `Auto picked ${fmt(p.angle_deg % 180)}° from north, the quickest direction for this area.`;
  }
  for (const f of p.flights) {
    const li = document.createElement("li");
    li.className = "flight";
    const lines = f.first_line === f.last_line ? `line ${f.first_line}` : `lines ${f.first_line}–${f.last_line}`;
    li.innerHTML = `
      <h3>Flight ${f.n}</h3><span class="meta">${lines}</span><span class="time">${mmss(f.total_s)}</span>
      <span class="battery">Battery about ${fmt(f.battery_frac * 100)}%,
        <span class="badge ${f.status}">${STATUS_WORDS[f.status] || f.status}</span>.
        Up to ${fmt(f.max_distance_m)} m from takeoff.</span>
      <button class="text-button preview" type="button">Preview route</button>`;
    li.addEventListener("mouseenter", () => { if (!preview) highlightFlight(f.n); });
    li.addEventListener("mouseleave", () => { if (!preview) highlightFlight(null); });
    li.querySelector(".preview").addEventListener("click", () => {
      if (prefersReducedMotion) { highlightFlight(f.n); setTimeout(() => highlightFlight(null), 2000); }
      else previewFlight(f.n);
    });
    flights.append(li);
    for (const prob of f.problems) notes.insertAdjacentHTML("beforeend", `<li class="error">Flight ${f.n}: ${escapeHtml(prob)}</li>`);
  }
  for (const n of p.notes) notes.insertAdjacentHTML("beforeend", `<li>${escapeHtml(n)}</li>`);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---------- save ----------
$("save-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const saved = $("saved");
  try {
    const res = await fetch("/api/save", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...request(), name: $("name").value }),
    });
    const body = await res.json();
    if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
    const n = body.files.length;
    saved.textContent = `Saved ${n} mission file${n === 1 ? "" : "s"} in ${body.folder}/, `
      + `plus ${n === 1 ? "a FreeFlight 6 copy" : "FreeFlight 6 copies"} in ${body.folder}/freeflight/.`;
    loadMissionList();
  } catch (err) {
    saved.textContent = `Not saved: ${err.message}`;
  }
});

// ---------- saved missions: open or delete ----------
const missionName = (v) => String(v || "").trim().replace(/[^A-Za-z0-9_-]+/g, "_").replace(/^_+|_+$/g, "");

async function loadMissionList(list = null) {
  const ul = $("missions");
  let missions = list;
  if (!missions) {
    try { ({ missions } = await (await fetch("/api/missions")).json()); } catch { return; } // planner unreachable
  }
  ul.replaceChildren();
  if (!missions.length) {
    const li = document.createElement("li");
    li.className = "missions-empty";
    li.textContent = "None yet. Draw an area, name it and press Save mission.";
    ul.append(li);
    return;
  }
  const current = missionName($("name").value);
  for (const m of missions) {
    const li = document.createElement("li");
    const isCurrent = m.name === current;
    li.classList.toggle("is-current", isCurrent);
    if (isCurrent) li.setAttribute("aria-current", "true");
    const text = document.createElement("div");
    const name = document.createElement("b");
    name.textContent = m.name;
    const when = new Date(m.saved * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
    const meta = document.createElement("small");
    meta.textContent = `${m.altitude ?? "?"} m, ${m.speed ?? "?"} m/s, `
      + `${m.flights} flight${m.flights === 1 ? "" : "s"}, saved ${when}${isCurrent ? ". On the map" : ""}`;
    text.append(name, meta);
    const open = document.createElement("button");
    Object.assign(open, { type: "button", className: "text-button", textContent: "Open" });
    open.setAttribute("aria-label", `Open ${m.name}`);
    open.addEventListener("click", () => openMission(m.name));
    const del = document.createElement("button");
    Object.assign(del, { type: "button", className: "text-button is-danger", textContent: "Delete" });
    del.setAttribute("aria-label", `Delete ${m.name}`);
    del.addEventListener("click", () => deleteMission(m.name));
    li.append(text, open, del);
    ul.append(li);
  }
}

async function openMission(name) {
  try {
    const res = await fetch(`/api/missions/${encodeURIComponent(name)}`);
    const m = await res.json();
    if (!res.ok) throw new Error(m.error || `HTTP ${res.status}`);
    if (state.area.length) rememberArea(); // Undo brings back what was on the map
    state.area = m.area;
    state.home = m.home || null;
    for (const k of ["altitude", "speed", "front", "side", "end", "endurance", "reserve"]) {
      if (m[k] !== undefined && m[k] !== null && m[k] !== "") setParam(k, m[k]);
    }
    setParam("angle-mode", m.angle === null || m.angle === undefined || m.angle === "" ? "auto" : "set");
    if (m.angle !== null && m.angle !== undefined) setParam("angle", m.angle);
    syncAngleMode();
    $("name").value = m.name;
    state.plan = null;
    drawHome();
    setMode("idle");
    map.fitBounds(L.latLngBounds(state.area.concat(state.home ? [state.home] : [])), { padding: [40, 40] });
    replanSoon();
    $("saved").textContent = `Opened ${m.name}. Saving again under the same name replaces it.`;
    loadMissionList();
  } catch (err) {
    $("saved").textContent = `Couldn't open ${name}: ${err.message}`;
  }
}

async function deleteMission(name) {
  if (!confirm(`Move the mission "${name}" to the trash?\n\nYou can restore it from the trash. `
    + "Plans already sent to FreeFlight stay on the iPhone.")) return;
  try {
    const res = await fetch("/api/missions/delete", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }) });
    const out = await res.json();
    if (!res.ok) throw new Error(out.error || `HTTP ${res.status}`);
    $("saved").textContent = `Moved ${name} to the trash.`
      + (missionName($("name").value) === name ? " It's still on the map: Save mission keeps it again." : "");
    loadMissionList(out.missions);
  } catch (err) {
    $("saved").textContent = `Not deleted: ${err.message}`;
  }
}

// ---------- iPhone ----------
const phone = { connected: false, name: null };

function updateSend() {
  $("send").disabled = !(state.plan && phone.connected);
}

async function checkPhone() {
  if (document.hidden) return;
  try {
    const s = await (await fetch("/api/iphone")).json();
    phone.connected = s.connected;
    phone.name = s.name;
    $("phone").className = s.connected ? "phone is-on" : "phone";
    $("phone").textContent = s.connected ? `${s.name} connected`
      : s.message || "Plug in the iPhone to send plans to FreeFlight";
  } catch { /* server gone; the plan request will say so */ }
  updateSend();
}

$("send").addEventListener("click", async () => {
  const saved = $("saved");
  $("send").disabled = true;
  saved.textContent = "Sending to FreeFlight…";
  try {
    const res = await fetch("/api/iphone/send", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...request(), name: $("name").value }),
    });
    const body = await res.json();
    if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
    const n = body.titles.length;
    saved.textContent = `Sent ${n === 1 ? "plan" : `${n} plans`} to FreeFlight on ${body.phone}: `
      + `${body.titles.join(", ")}. Close and reopen FreeFlight to see ${n === 1 ? "it" : "them"}, `
      + `then set the camera to GPS lapse on ${n === 1 ? "the plan" : "each plan"}.`;
    loadMissionList();
  } catch (err) {
    saved.textContent = `Not sent: ${err.message}`;
  } finally {
    updateSend();
  }
});

setInterval(checkPhone, 4000);
document.addEventListener("visibilitychange", checkPhone);

// ---------- views (Plan / Batteries) ----------
function setView() {
  const view = location.hash === "#batteries" ? "batteries" : "plan";
  document.body.dataset.view = view;
  $("batteries-view").hidden = view !== "batteries";
  for (const a of document.querySelectorAll(".mode")) {
    const on = a.dataset.view === view;
    a.classList.toggle("is-active", on);
    if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  }
  if (view === "plan") map.invalidateSize(); // the map had no size while hidden
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
