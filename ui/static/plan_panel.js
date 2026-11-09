"use strict";
// Plan tab, side panel: planning requests and results, save, saved missions, iPhone.
// Plain scripts sharing one scope, loaded in order by index.html (see app.js).

// ---------- planning ----------
let timer = null;
let requestId = 0;

function request() {
  const params = Object.fromEntries(PARAMS.map((k) => [k, getParam(k)]));
  if (params["angle-mode"] !== "set" || params.angle === "") params.angle = null;
  delete params["angle-mode"];
  return { area: state.area, home: state.home, ...params, from_line: fromLine() };
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
  $("from_line").max = p.total_lines;
  const from = p.from_line > 1 ? ` Starting at line ${p.from_line} of ${p.total_lines}: lines 1–${p.from_line - 1} left out.` : "";
  $("summary").textContent = `${fmt(p.gsd_cm, 1)} cm per pixel. ${p.lines} lines, ${fmt(p.line_spacing_m, 1)} m apart `
    + `(side overlap ${fmt(p.side_overlap * 100)}%). A photo every ${fmt(p.photo_spacing_m, 1)} m `
    + `(${fmt(p.photo_interval_s, 1)} s at ${fmt(p.speed_ms, 1)} m/s${p.speed_lowered ? ", slowed down" : ""}); `
    + `each covers ${fmt(p.footprint_m[0])} × ${fmt(p.footprint_m[1])} m.${from}`;
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
function showStatus(kind, text) {
  // kind: "ok" (green, with a check), "error" (red), "busy" or "" (plain hint)
  const el = $("saved");
  el.className = kind ? `status is-${kind}` : "hint";
  el.textContent = text;
}

$("save-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const res = await fetch("/api/save", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...request(), name: $("name").value }),
    });
    const body = await res.json();
    if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
    const n = body.files.length;
    showStatus("ok", `Saved ${n} mission file${n === 1 ? "" : "s"} in ${body.folder}/, `
      + `plus ${n === 1 ? "a FreeFlight 6 copy" : "FreeFlight 6 copies"} in ${body.folder}/freeflight/.`);
    loadMissionList();
  } catch (err) {
    showStatus("error", `Not saved: ${err.message}`);
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
    for (const k of PARAMS.filter((k) => k !== "angle-mode" && k !== "angle")) { // the angle follows below
      if (m[k] !== undefined && m[k] !== null && m[k] !== "") setParam(k, m[k]);
    }
    setParam("angle-mode", m.angle === null || m.angle === undefined || m.angle === "" ? "auto" : "set");
    if (m.angle !== null && m.angle !== undefined) setParam("angle", m.angle);
    syncAngleMode();
    resetFromLine(Number(m.from_line) || 1);
    $("name").value = m.name;
    state.plan = null;
    drawHome();
    setMode("idle");
    map.fitBounds(L.latLngBounds(state.area.concat(state.home ? [state.home] : [])), { padding: [40, 40] });
    replanSoon();
    showStatus("", `Opened ${m.name}. Saving again under the same name replaces it.`);
    loadMissionList();
  } catch (err) {
    showStatus("error", `Couldn't open ${name}: ${err.message}`);
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
    showStatus("ok", `Moved ${name} to the trash.`
      + (missionName($("name").value) === name ? " It's still on the map: Save mission keeps it again." : ""));
    loadMissionList(out.missions);
  } catch (err) {
    showStatus("error", `Not deleted: ${err.message}`);
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
  $("send").disabled = true;
  showStatus("busy", "Sending to FreeFlight…");
  try {
    const res = await fetch("/api/iphone/send", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...request(), name: $("name").value }),
    });
    const body = await res.json();
    if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
    const n = body.titles.length;
    showStatus("ok", `On ${body.phone}: ${body.titles.join(", ")}. Close and reopen FreeFlight to see `
      + `${n === 1 ? "it" : "them"}; ${n === 1 ? "it points the camera down and takes photos" : "they point the camera down and take photos"} by itself.`);
    $("saved").insertAdjacentHTML("afterbegin", `<strong>Sent to FreeFlight.</strong> `);
    loadMissionList();
  } catch (err) {
    showStatus("error", `Not sent: ${err.message}`);
  } finally {
    updateSend();
  }
});

setInterval(checkPhone, 4000);
document.addEventListener("visibilitychange", checkPhone);
