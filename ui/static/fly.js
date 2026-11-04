"use strict";
// Fly view: connect to the drone, load a saved mission, take off with a press-and-hold,
// watch it live and bring it home. The server holds the drone link (ui/fly.py); this page
// polls /api/fly and sends commands, so closing it doesn't touch the flight.
(() => {
  const byId = (id) => document.getElementById(id);
  const HOLD_MS = 2000;
  const POLL_MS = 1000;

  let st = null; // last /api/fly state
  let lastNote = 0;
  let missions = [];
  let polling = null;
  let busy = false; // a command is on its way
  const trail = []; // flown positions this flight
  let fittedFor = null; // mission uid the map was fitted to
  let connectingSince = null; // when this page saw the link start connecting

  // ---------- map ----------
  const flyLayer = L.layerGroup();
  // Same encoding as Plan: solid where photos are taken, thin dashed for transit.
  const routeLayer = L.layerGroup();
  const trailCase = L.polyline([], { color: "#FFFFFF", weight: 6, opacity: 0.7, interactive: false });
  const trailLine = L.polyline([], { color: "#13201B", weight: 3, opacity: 0.95, interactive: false });
  const fence = L.circle([0, 0], { radius: 1, color: "#13201B", weight: 1.5, opacity: 0.6, dashArray: "4 6",
    fill: false, interactive: false });
  const homeMark = L.marker([0, 0], {
    icon: L.divIcon({ className: "", html: '<div class="home-mark" title="Home">H</div>', iconSize: [26, 26] }),
    interactive: false, keyboard: false, zIndexOffset: 900 });
  // The drone: a hi-vis arrow pointing where it faces (nothing else on this map is yellow).
  const DRONE_SVG = `<svg class="drone-arrow" viewBox="0 0 40 40" aria-hidden="true">
    <circle cx="20" cy="20" r="17" fill="#13201B" fill-opacity="0.35" stroke="#FFFFFF" stroke-width="2"/>
    <path d="M20 4 L31 31 L20 25 L9 31 Z" fill="#FFD200" stroke="#13201B" stroke-width="2.5" stroke-linejoin="round"/>
  </svg>`;
  const droneMark = L.marker([0, 0], {
    icon: L.divIcon({ className: "", html: `<div class="drone-mark2" title="The drone">${DRONE_SVG}</div>`,
      iconSize: [40, 40] }),
    interactive: false, keyboard: false, zIndexOffset: 1000 });
  const previewLayer = L.layerGroup(); // the selected mission, before it's loaded on the drone
  [previewLayer, routeLayer, fence, trailCase, trailLine].forEach((l) => l.addTo(flyLayer));
  let preview = null; // {key, name, flight, home, summary}
  let drawnRoute = null;

  function drawRoute(m) {
    if (drawnRoute === (m && m.uid)) return;
    drawnRoute = m && m.uid;
    routeLayer.clearLayers();
    if (!m) return;
    for (let i = 1; i < m.route.length; i++) {
      const a = m.route[i - 1], b = m.route[i];
      if (a[0] === b[0] && a[1] === b[1]) continue; // a vertical leg (climb or descent over home)
      const ab = [[a[0], a[1]], [b[0], b[1]]];
      if (b[3]) {
        L.polyline(ab, { color: "#FFFFFF", weight: 5, opacity: 0.55, interactive: false }).addTo(routeLayer);
        L.polyline(ab, { color: "#D1119B", weight: 3, opacity: 0.95, interactive: false }).addTo(routeLayer);
      } else {
        L.polyline(ab, { color: "#FFFFFF", weight: 4, opacity: 0.5, interactive: false }).addTo(routeLayer);
        L.polyline(ab, { color: "#D1119B", weight: 2, opacity: 0.95, dashArray: "6 6", interactive: false }).addTo(routeLayer);
      }
    }
  }

  function drawMap() {
    const s = st && st.status;
    const m = st && st.mission;
    const route = m ? m.route.map(([lat, lon]) => [lat, lon]) : [];
    drawRoute(m);
    trailLine.setLatLngs(trail);
    trailCase.setLatLngs(trail);
    if (map.hasLayer(trailLine)) { trailCase.bringToFront(); trailLine.bringToFront(); } // flown track over the plan
    // H is where to take off: the mission's takeoff point (the drone's own home moves to
    // wherever it last took off), or the previewed mission's.
    const home = preview ? preview.home : m && m.route.length ? m.route[0] : s && s.home;
    routeLayer.eachLayer((l) => {
      if (l.options.baseOpacity === undefined) l.options.baseOpacity = l.options.opacity;
      l.setStyle({ opacity: preview ? l.options.baseOpacity * 0.3 : l.options.baseOpacity });
    });
    if (home) { homeMark.setLatLng([home[0], home[1]]); if (!flyLayer.hasLayer(homeMark)) homeMark.addTo(flyLayer); }
    else homeMark.remove();
    // The geofence is centred on the drone's own home.
    const droneHome = s && s.home;
    if (droneHome && s.geofence && s.max_distance_m) {
      fence.setLatLng([droneHome[0], droneHome[1]]); fence.setRadius(s.max_distance_m); fence.setStyle({ opacity: 0.6 });
    } else fence.setStyle({ opacity: 0 });
    const pos = s && s.position;
    if (pos && st.link.state === "connected") {
      droneMark.setLatLng([pos[0], pos[1]]);
      if (!flyLayer.hasLayer(droneMark)) droneMark.addTo(flyLayer);
      const arrow = droneMark.getElement() && droneMark.getElement().querySelector(".drone-arrow");
      if (arrow) arrow.style.transform = `rotate(${s.heading_deg || 0}deg)`;
      // Keep the drone in view without fighting the pilot's own panning.
      const inner = map.getBounds().pad(-0.15);
      if (airborne() && !inner.contains(droneMark.getLatLng())) map.panTo(droneMark.getLatLng());
    } else droneMark.remove();
    if (m && fittedFor !== m.uid && route.length) {
      fittedFor = m.uid;
      map.fitBounds(L.latLngBounds(route), { padding: [50, 50] });
    }
  }

  // ---------- preview of the selected mission ----------
  const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;

  async function showPreview() {
    const name = byId("fly-mission").value;
    const flight = Number(byId("fly-flight").value || 1);
    const loaded = st && st.mission && st.mission.name === name && st.mission.flight === flight;
    const key = `${name}#${flight}`;
    if (!name || loaded) { preview = null; previewLayer.clearLayers(); render(); return; }
    if (preview && preview.key === key) return;
    try {
      const req = await (await fetch(`/api/missions/${encodeURIComponent(name)}`)).json();
      const res = await fetch("/api/plan", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(req) });
      const plan = await res.json();
      if (!res.ok) throw new Error(plan.error || `HTTP ${res.status}`);
      if (byId("fly-mission").value !== name) return; // the pilot moved on meanwhile
      drawPreview(plan, flight, key, name);
    } catch (err) {
      preview = null; previewLayer.clearLayers();
      localNote("warning", `Couldn't show ${name}: ${err.message}`);
    }
    render();
  }

  function drawPreview(plan, flight, key, name) {
    previewLayer.clearLayers();
    const feats = plan.geojson.features;
    const area = feats.find((f) => f.properties.kind === "area");
    if (area) {
      L.polygon(area.geometry.coordinates[0].map(([lon, lat]) => [lat, lon]),
        { color: "#FFD200", weight: 3, opacity: 0.9, fillColor: "#FFD200", fillOpacity: 0.06, interactive: false })
        .addTo(previewLayer);
    }
    const path = feats.find((f) => f.properties.kind === "flight" && f.properties.flight === flight);
    const pts = path ? path.geometry.coordinates.map(([lon, lat]) => [lat, lon]) : [];
    for (let i = 0; i + 1 < pts.length; i++) {
      const ab = [pts[i], pts[i + 1]];
      const photo = i % 2 === 1; // home, s1, e1, s2, e2, ..., home: odd legs fly a photo line
      L.polyline(ab, { color: "#FFFFFF", weight: photo ? 5 : 4, opacity: 0.55, interactive: false }).addTo(previewLayer);
      L.polyline(ab, { color: "#D1119B", weight: photo ? 3 : 2, opacity: 0.95, dashArray: photo ? null : "6 6",
        interactive: false }).addTo(previewLayer);
    }
    const f = plan.flights[flight - 1];
    const lines = f.first_line === f.last_line ? `line ${f.first_line}` : `lines ${f.first_line}–${f.last_line}`;
    preview = {
      key, name, flight, home: plan.home,
      summary: `Preview: ${name}, flight ${flight}: ${lines} of ${plan.total_lines}, about ${mmss(f.total_s)}, `
        + `battery about ${Math.round(f.battery_frac * 100)}%, up to ${Math.round(f.max_distance_m)} m from H. `
        + "Not on the drone yet.",
    };
    if (pts.length && !flying()) map.fitBounds(L.latLngBounds(pts.concat(area ? [] : [])), { padding: [50, 50] });
  }

  // ---------- helpers ----------
  const fmt = (v, d = 0) => (v === null || v === undefined ? "–" : Number(v).toFixed(d));
  const linked = () => st && st.link.state === "connected";
  const airborne = () => linked() && st.status && st.status.flying_state && st.status.flying_state !== "landed";
  const flying = () => st && st.flight && st.flight.started && !st.flight.finished;

  async function api(path, body) {
    const res = await fetch(`/api/fly${path}`, body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const out = await res.json();
    if (!res.ok) throw new Error(out.error || `HTTP ${res.status}`);
    return out;
  }

  function localNote(kind, text) {
    const li = document.createElement("li");
    li.className = `is-${kind}`;
    const t = document.createElement("time");
    t.textContent = new Date().toTimeString().slice(0, 8);
    li.append(t, document.createTextNode(text));
    byId("fly-notes").append(li);
    byId("fly-notes").scrollTop = byId("fly-notes").scrollHeight; // scroll the list, not the panel
  }

  async function send(path, body, label) {
    if (busy) return;
    busy = true;
    render();
    try {
      apply(await api(path, body));
    } catch (err) {
      localNote("error", `${label}: ${err.message}`);
    } finally {
      busy = false;
      render();
    }
  }

  // ---------- reading the state ----------
  function lineInfo() {
    const m = st && st.mission;
    const s = st && st.status;
    if (!m || !m.photo_lines.length) return null;
    const item = s && s.mission_item !== null && s.mission_item !== undefined ? s.mission_item : -1;
    // Survey numbering: a "from line N" plan or a later battery starts at line N, and the lines
    // before it were flown earlier.
    const before = Array(m.first_line - 1).fill("done");
    const own = m.photo_lines.map(([a, b]) => (item >= b ? "done" : item >= a ? "now" : "todo"));
    const after = Array(Math.max(0, m.survey_lines - before.length - own.length)).fill("later");
    const lines = [...before, ...own, ...after];
    const now = lines.indexOf("now");
    const done = before.length + own.filter((x) => x === "done").length;
    const end = before.length + own.length; // this flight ends after that many lines
    return { lines, now, done, end, total: lines.length, item, first: m.photo_lines[0][0] };
  }

  function stateText() {
    if (!st) return "Starting…";
    const l = st.link;
    if (l.state === "connecting") {
      const secs = connectingSince ? Math.round((Date.now() - connectingSince) / 1000) : 0;
      return `Connecting to ${l.host}… ${secs} s.` + (secs >= 10
        ? " A drone that was just switched on takes about a minute to accept a controller; this gives up after 2 minutes."
        : "");
    }
    if (l.state === "lost") return `Link lost (${l.error}). The drone carries on or returns home by itself; take over with the Skycontroller.`;
    if (l.state !== "connected") return st.can_fly ? "Not connected." : "Flying from this computer isn't available here.";
    const s = st.status || {};
    const f = st.flight;
    const li = lineInfo();
    if (f && f.finished) return "Landed.";
    if (s.flying_state === "landing") return "Landing.";
    if (s.rth_state === "inProgress" || s.rth_state === "pending") return "Returning home.";
    if (flying() && s.plan_state === "paused") {
      const where = li && li.now >= 0 ? ` on line ${li.now + 1} of ${li.total}` : "";
      if (s.home_m !== null && s.home_m < 5 && s.flying_state === "hovering") {
        return `Hovering over home, plan paused${where}. Land, or resume to finish the survey.`;
      }
      return `Paused${where}. Resume, return home or land.`;
    }
    if (flying() && s.plan_state === "playing" && li) {
      if (li.now >= 0) return `Flying line ${li.now + 1} of ${li.total}. Photos on.`;
      if (li.item < li.first) return `Climbing over home to ${fmt(st.mission.max_altitude_m)} m.`;
      if (li.done >= li.end) return li.end === li.total ? "Survey done. Flying home." : "This flight's lines are done. Flying home.";
      return `Turning onto line ${li.done + 1} of ${li.total}.`;
    }
    if (airborne()) return "In the air, no plan running. Return home or land.";
    if (!st.mission) return "Connected. Load a mission.";
    return st.checks.length ? "Not ready to take off yet." : "Ready to take off.";
  }

  function batteryLevel(pct) {
    const min = st ? st.min_battery_pct : 30;
    if (pct === null || pct === undefined) return { cls: "", word: "Battery" };
    if (pct < min) return { cls: "is-low", word: airborne() ? "Low: return home" : `Too low to take off (minimum ${min}%)` };
    if (pct < min + 15) return { cls: "is-tight", word: "Battery getting low" };
    return { cls: "is-ok", word: "Battery good" };
  }

  // ---------- rendering ----------
  function apply(state) {
    if (state.flight && state.flight.started && (!st || !st.flight || !st.flight.started)) trail.length = 0;
    st = state;
    const s = st.status;
    if (s && s.position && airborne()) {
      const p = [s.position[0], s.position[1]];
      const last = trail[trail.length - 1];
      if (!last || Math.abs(last[0] - p[0]) + Math.abs(last[1] - p[1]) > 2e-6) trail.push(p);
    }
    for (const n of st.notes) {
      if (n.id <= lastNote) continue;
      lastNote = n.id;
      const li = document.createElement("li");
      li.className = `is-${n.kind}`;
      const t = document.createElement("time");
      t.textContent = n.time;
      li.append(t, document.createTextNode(n.text));
      byId("fly-notes").append(li);
      byId("fly-notes").scrollTop = byId("fly-notes").scrollHeight;
    }
    render();
  }

  function render() {
    const s = (st && st.status) || {};
    const link = st ? st.link.state : "disconnected";
    if (link === "connecting") connectingSince = connectingSince || Date.now(); else connectingSince = null;

    // instruments
    const pct = linked() ? s.battery_pct : null;
    const lvl = batteryLevel(pct);
    byId("fly-battery-pct").textContent = pct === null || pct === undefined ? "–" : `${pct}%`;
    byId("fly-battery-word").textContent = lvl.word;
    byId("fly-battery-word").className = `meter-word ${lvl.cls}`;
    byId("fly-battery-fill").style.width = `${pct || 0}%`;
    byId("fly-battery-fill").className = `meter-fill ${lvl.cls}`;
    byId("fly-battery-min").style.left = `${st ? st.min_battery_pct : 30}%`;
    byId("fly-battery").setAttribute("aria-valuenow", pct ?? "");
    byId("fly-battery").setAttribute("aria-valuetext", pct === null || pct === undefined ? "unknown" : `${pct}% ${lvl.word}`);
    byId("fly-alt").textContent = linked() ? fmt(s.altitude_m) : "–";
    byId("fly-speed").textContent = linked() ? fmt(s.speed_ms, 1) : "–";
    byId("fly-home").textContent = linked() ? fmt(s.home_m) : "–";
    byId("fly-state").textContent = stateText();

    // one segment per survey line
    const box = byId("fly-lines");
    const li = lineInfo();
    box.innerHTML = "";
    if (li) {
      li.lines.forEach((x, i) => {
        const seg = document.createElement("span");
        seg.className = `line-seg is-${x === "later" ? "todo" : x}`;
        seg.title = `Line ${i + 1}: ${{ done: "flown", now: "flying now", todo: "to fly", later: "a later flight" }[x]}`;
        box.append(seg);
      });
      box.setAttribute("aria-label", `Survey lines: ${li.done} of ${li.total} flown`);
    }

    // link
    const ls = byId("fly-link-state");
    ls.textContent = { connected: `Connected to ${st && st.link.host}`, connecting: "Connecting…",
      lost: "Link lost", disconnected: "Not connected" }[link];
    ls.className = `link-state ${link === "connected" ? "is-on" : link === "lost" ? "is-lost" : ""}`;
    // Connected (perhaps from an earlier page): show the link actually in use.
    if (st && (link === "connected" || link === "connecting") && st.link.host) {
      const kind = { "192.168.53.1": "skycontroller", "192.168.42.1": "wifi" }[st.link.host] || "other";
      const radio = document.querySelector(`input[name="fly-link"][value="${kind}"]`);
      if (radio && !radio.checked) radio.checked = true;
      if (kind === "other" && document.activeElement !== byId("fly-host")) byId("fly-host").value = st.link.host;
    }
    const other = document.querySelector('input[name="fly-link"]:checked').value === "other";
    document.querySelector(".fly-host").hidden = !other;
    byId("fly-connect").hidden = link === "connected" || link === "connecting";
    byId("fly-connect").disabled = busy || !(st && st.can_fly);
    byId("fly-disconnect").hidden = !(link === "connected" || link === "connecting");
    byId("fly-disconnect").disabled = busy || airborne();
    byId("fly-disconnect").title = airborne() ? "Land first: disconnecting leaves the drone without this controller" : "";
    byId("no-fly").hidden = !(st && !st.can_fly);
    if (st && !st.can_fly) byId("no-fly").textContent = st.no_olympe;
    for (const r of document.querySelectorAll('input[name="fly-link"]')) r.disabled = link === "connected" || link === "connecting";

    // mission
    byId("fly-load").disabled = busy || !linked() || flying() || !byId("fly-mission").value;
    byId("fly-mission").disabled = flying();
    byId("fly-flight").disabled = flying();
    const m = st && st.mission;
    byId("fly-mission-meta").textContent = preview ? preview.summary : m
      ? `${m.name}, flight ${m.flight}: lines ${m.first_line}–${m.first_line + m.photo_lines.length - 1} of ${m.survey_lines}, up to ${fmt(m.max_altitude_m)} m. `
        + (m.on_drone ? "On the drone." : "Not on the drone yet.")
      : "Missions you saved in Plan. Connect first; loading uploads the flight to the drone.";

    // checks (before takeoff), then the flight itself
    byId("checks-h").textContent = flying() ? "Flight" : "Before takeoff";
    byId("hold-hint").hidden = flying();
    const ul = byId("fly-checks");
    ul.innerHTML = "";
    const checks = st ? st.checks : ["connect to the drone"];
    const pending = ["connect to the drone", "choose a mission", "waiting for the drone's state"];
    if (flying()) {
      ul.insertAdjacentHTML("beforeend", `<li class="is-ok">In flight${st.flight.elapsed_s ? ` for ${Math.floor(st.flight.elapsed_s / 60)} min ${String(Math.floor(st.flight.elapsed_s % 60)).padStart(2, "0")} s` : ""}. Log: ${st.flight.log}</li>`);
    } else if (!checks.length) {
      ul.insertAdjacentHTML("beforeend", '<li class="is-ok">All checks passed: GPS, home point, battery, limits, plan on the drone.</li>');
    } else {
      for (const c of checks) {
        const item = document.createElement("li");
        item.className = pending.includes(c) ? "is-wait" : "is-bad";
        item.textContent = c.charAt(0).toUpperCase() + c.slice(1) + ".";
        ul.append(item);
      }
    }
    // an unfinished survey: offer to fly the rest
    const r = st && st.resume;
    byId("fly-resume").hidden = !r || flying();
    if (r) {
      byId("fly-resume-text").textContent = `The survey stopped on line ${r.line} of ${r.total}. Carry the drone `
        + `back to the takeoff point (H), then continue: a new plan climbs over H and flies from line ${r.line} on.`;
      byId("fly-continue").textContent = `Continue from line ${r.line}`;
      byId("fly-continue").disabled = busy || !linked() || airborne();
    }
    const canStart = linked() && st.mission && !st.checks.length && !flying() && !airborne() && !busy;
    byId("fly-start").disabled = !canStart;
    if (!holding) byId("fly-start").querySelector(".hold-label").textContent = flying() ? "Flying" : "Hold to take off";

    // controls
    const up = airborne();
    byId("fly-rth").disabled = busy || !up;
    byId("fly-land").disabled = busy || !up;
    const paused = flying() && s.plan_state === "paused";
    // Flown away from the pause by hand: resuming would cut straight to the line's end.
    const pz = st && st.paused;
    const choose = paused && pz && pz.offer_refly;
    byId("fly-choice").hidden = !choose;
    if (choose) {
      byId("fly-choice-text").textContent = `Paused on line ${pz.line}, and the drone has moved `
        + `${Math.round(pz.moved_m)} m since: fly the line again from its start, or return home.`;
      byId("fly-refly").textContent = `Refly line ${pz.line} from its start`;
      byId("fly-refly").disabled = busy;
    }
    byId("fly-pause").hidden = choose;
    byId("fly-pause").textContent = paused ? "Resume" : "Pause";
    byId("fly-pause").disabled = busy || !(flying() && (s.plan_state === "playing" || paused));
    byId("fly-stop").disabled = busy || !(flying() && (s.plan_state === "playing" || paused));

    drawMap();
    syncCamera();
  }

  // ---------- live camera ----------
  const CAM_KEY = "anafi-planner.camera";
  let camOn = false;
  let camLarge = false;
  let camRetry = null;
  try { camOn = localStorage.getItem(CAM_KEY) === "on"; } catch { /* storage blocked */ }

  function setCamera(on) {
    camOn = on;
    try { localStorage.setItem(CAM_KEY, on ? "on" : "off"); } catch { /* storage blocked */ }
    syncCamera();
  }

  function syncCamera() {
    const want = camOn && linked() && document.body.dataset.view === "fly";
    const fig = byId("fly-camera");
    const img = byId("fly-camera-img");
    fig.hidden = !want;
    fig.classList.toggle("is-large", camLarge);
    byId("fly-camera-size").textContent = camLarge ? "Smaller" : "Larger";
    const t = byId("fly-camera-toggle");
    t.disabled = !linked();
    t.textContent = camOn ? "Hide camera" : "Show camera";
    t.setAttribute("aria-pressed", String(camOn));
    if (!want) {
      if (img.getAttribute("src")) img.removeAttribute("src"); // closes the stream; the server stops ffmpeg
      clearTimeout(camRetry); camRetry = null;
      return;
    }
    if (!img.getAttribute("src") && !camRetry) img.src = `/api/fly/video?t=${Date.now()}`;
    // Say what's wrong instead of a black box.
    const v = st && st.video;
    const msg = byId("fly-camera-msg");
    let text = "";
    if (v && !v.available) text = "The camera view needs ffmpeg on this computer.";
    else if (v && v.running && v.age_s !== null && v.age_s > 3) text = `No new picture for ${Math.round(v.age_s)} s.`;
    else if (v && !v.running && v.restarts) text = "Reconnecting: the drone restarted its video (it does when it switches camera modes)…";
    else if (v && !v.running && v.error) text = `No picture: ${v.error}. Retrying…`;
    else if (v && v.running && !v.frames) text = "Starting the camera…";
    msg.textContent = text;
    msg.hidden = !text;
    byId("fly-camera-cap").textContent = v && v.running && v.frames ? "Camera, live" : "Camera";
  }

  byId("fly-camera-img").addEventListener("error", () => {
    // Not connected yet, or the stream ended: try again in a few seconds while it's wanted.
    byId("fly-camera-img").removeAttribute("src");
    clearTimeout(camRetry);
    camRetry = setTimeout(() => { camRetry = null; syncCamera(); }, 4000);
  });
  byId("fly-camera-toggle").addEventListener("click", () => setCamera(!camOn));
  byId("fly-camera-close").addEventListener("click", () => setCamera(false));
  byId("fly-camera-size").addEventListener("click", () => { camLarge = !camLarge; syncCamera(); });

  // ---------- polling ----------
  async function poll() {
    try {
      apply(await api(`?since=${lastNote}`));
    } catch {
      byId("fly-state").textContent = "Can't reach anafi-gui. Is it still running? The drone carries on or returns home by itself.";
    }
  }
  function setPolling(on) {
    if (on && !polling) { poll(); polling = setInterval(poll, POLL_MS); }
    if (!on && polling) { clearInterval(polling); polling = null; }
  }

  // ---------- missions ----------
  async function loadMissions(prefer = null) {
    try {
      missions = (await (await fetch("/api/missions")).json()).missions || [];
    } catch { missions = []; }
    const sel = byId("fly-mission");
    const keep = prefer || sel.value || (st && st.mission && st.mission.name);
    sel.innerHTML = missions.length ? "" : '<option value="">No saved missions</option>';
    for (const m of missions) {
      const o = document.createElement("option");
      o.value = m.name;
      o.textContent = m.name;
      sel.append(o);
    }
    if (keep && missions.some((m) => m.name === keep)) sel.value = keep;
    fillFlights();
  }
  function fillFlights() {
    const m = missions.find((x) => x.name === byId("fly-mission").value);
    const sel = byId("fly-flight");
    const keep = sel.value;
    sel.innerHTML = "";
    for (let n = 1; n <= (m ? m.flights : 0); n++) {
      const o = document.createElement("option");
      o.value = n;
      o.textContent = m.flights === 1 ? "Flight 1" : `Flight ${n} of ${m.flights}`;
      sel.append(o);
    }
    if (keep && sel.querySelector(`option[value="${keep}"]`)) sel.value = keep;
    render();
    showPreview();
  }

  // ---------- hold to take off ----------
  let holding = null;
  function holdStart(e) {
    const btn = byId("fly-start");
    if (btn.disabled || holding) return;
    e.preventDefault();
    const t0 = performance.now();
    const fill = btn.querySelector(".hold-fill");
    const label = btn.querySelector(".hold-label");
    label.textContent = "Keep holding…";
    const step = (now) => {
      const f = Math.min(1, (now - t0) / HOLD_MS);
      fill.style.width = `${f * 100}%`;
      if (f < 1) { holding = requestAnimationFrame(step); return; }
      holding = null;
      label.textContent = "Taking off…";
      send("/start", {}, "Not started").finally(() => { fill.style.width = "0"; render(); });
    };
    holding = requestAnimationFrame(step);
  }
  function holdCancel() {
    if (!holding) return;
    cancelAnimationFrame(holding);
    holding = null;
    const btn = byId("fly-start");
    btn.querySelector(".hold-fill").style.width = "0";
    btn.querySelector(".hold-label").textContent = "Hold to take off";
  }
  const hold = byId("fly-start");
  hold.addEventListener("pointerdown", holdStart);
  for (const ev of ["pointerup", "pointerleave", "pointercancel", "blur"]) hold.addEventListener(ev, holdCancel);
  hold.addEventListener("keydown", (e) => { if ((e.key === " " || e.key === "Enter") && !e.repeat) holdStart(e); });
  hold.addEventListener("keyup", (e) => { if (e.key === " " || e.key === "Enter") holdCancel(); });
  hold.addEventListener("click", (e) => e.preventDefault()); // only a full hold starts

  // ---------- buttons ----------
  for (const r of document.querySelectorAll('input[name="fly-link"]')) r.addEventListener("change", render);
  byId("fly-connect").addEventListener("click", () => {
    const link = document.querySelector('input[name="fly-link"]:checked').value;
    send("/connect", { link, host: link === "other" ? byId("fly-host").value.trim() : null }, "Not connected");
  });
  byId("fly-disconnect").addEventListener("click", () => send("/disconnect", {}, "Still connected"));
  byId("fly-mission").addEventListener("change", fillFlights);
  byId("fly-flight").addEventListener("change", showPreview);
  byId("fly-load").addEventListener("click", async () => {
    await send("/mission", { name: byId("fly-mission").value, flight: Number(byId("fly-flight").value || 1) },
      "Not loaded");
    showPreview(); // loaded: the preview gives way to the mission on the drone
  });
  byId("fly-continue").addEventListener("click", async () => {
    await send("/continue", {}, "Not continued");
    loadMissions(st && st.mission && st.mission.name); // the new mission, selected
  });
  byId("fly-refly").addEventListener("click", () => send("/command", { action: "refly" }, "Not reflown"));
  byId("fly-rth").addEventListener("click", () => send("/command", { action: "rth" }, "Return home failed"));
  byId("fly-land").addEventListener("click", () => send("/command", { action: "land" }, "Landing failed"));
  byId("fly-stop").addEventListener("click", () => send("/command", { action: "stop" }, "Stop failed"));
  byId("fly-pause").addEventListener("click", () => {
    const paused = st && st.status && st.status.plan_state === "paused";
    send("/command", { action: paused ? "resume" : "pause" }, paused ? "Not resumed" : "Not paused");
  });

  // ---------- view ----------
  window.addEventListener("viewchange", (e) => {
    const on = e.detail === "fly";
    if (on) { flyLayer.addTo(map); loadMissions(); }
    else flyLayer.remove();
    // Keep reading while connected, wherever the pilot looks; stop when idle and away.
    setPolling(on || linked() || (st && st.link.state === "connecting"));
  });
  setInterval(() => { if (!polling && (linked() || document.body.dataset.view === "fly")) setPolling(true); }, 5000);
  render();
  if (document.body.dataset.view === "fly") window.dispatchEvent(new CustomEvent("viewchange", { detail: "fly" }));
})();
