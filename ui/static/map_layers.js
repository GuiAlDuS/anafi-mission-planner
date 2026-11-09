"use strict";
// Plan tab, map layers: base maps, your orthophotos and WMS layers, image info.
// Plain scripts sharing one scope, loaded in order by index.html (see app.js).

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
