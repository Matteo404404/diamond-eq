"use strict";
const $ = (id) => document.getElementById(id);
let state = null;      // last /api/state
let model = null;      // search result being configured
let current = null;    // profile shown in the detail panel

async function api(path, body) {
  const opts = body === undefined ? {} :
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

// ---- state / outputs ------------------------------------------------------
async function refresh() {
  try {
    state = await api("/api/state");
  } catch (e) {
    $("status").textContent = "service not reachable";
    return;
  }
  $("enabled").checked = state.enabled;
  $("enabledLabel").textContent = state.enabled ? "EQ on" : "EQ off";
  const active = state.outputs.filter((o) => o.active).length;
  $("status").textContent = active ? `${active} output${active > 1 ? "s" : ""} equalized` : "no output equalized";
  renderOutputs();
  renderProfiles();
  const idx = state.index;
  $("indexInfo").textContent = idx.models ? `${idx.models.toLocaleString()} models` : "list not downloaded yet";
  const p = state.phone;
  $("phoneInfo").textContent = "The Android app syncs profiles from this PC over USB (USB debugging on), " +
    (p.lan && p.addresses.length ? `or over Wi-Fi at ${p.addresses[0]}:${p.port} once it has synced over USB.` : "Wi-Fi sync is off.");
  fillOutputSelect();
}

function profileOptions(selected) {
  const opts = [el("option", { value: "", textContent: "No EQ" })];
  for (const p of state.profiles) {
    opts.push(el("option", { value: p.id, textContent: p.name + sourceNote(p), selected: p.id === selected }));
  }
  return opts;
}

function sourceNote(p) {
  const sites = [...new Set((p.sources || []).map((s) => s.site))];
  return sites.length ? ` (${sites.join(", ")})` : p.kind === "imported" ? " (imported)" : "";
}

function renderOutputs() {
  const box = $("outputs");
  // the periodic refresh must not close a dropdown the user has open
  if (box.contains(document.activeElement) && document.activeElement.tagName === "SELECT") return;
  box.replaceChildren();
  for (const o of state.outputs) {
    if (!o.available && !o.profile && o.online) continue; // e.g. HDMI ports with nothing plugged in
    const tags = el("div", {},
      el("span", { className: "tag", textContent: o.kind }),
      o.online ? "" : el("span", { className: "tag", textContent: "not connected" }),
      o.default ? el("span", { className: "tag", textContent: "default" }) : "",
      o.active ? el("span", { className: "tag on", textContent: "equalized" }) : "",
      o.error ? el("span", { className: "tag err", textContent: "error", title: o.error }) : "");
    const select = el("select", {}, ...profileOptions(o.profile));
    select.onchange = async () => { await api("/api/assign", { output: o.key, profile: select.value || null }); setTimeout(refresh, 600); };
    box.append(el("div", { className: "card" + (o.online ? "" : " offline") },
      el("div", { className: "name", textContent: o.label }), tags, select));
  }
  if (!state.outputs.length) box.append(el("p", { className: "muted", textContent: "No audio outputs found." }));
}

function fillOutputSelect() {
  const sel = $("optOutput");
  const keep = sel.value;
  sel.replaceChildren(el("option", { value: "", textContent: "Don't assign yet" }),
    ...state.outputs.map((o) => el("option", { value: o.key, textContent: o.label })));
  sel.value = keep;
}

// ---- search / create -------------------------------------------------------
let searchTimer = null;
$("query").oninput = () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(search, 250);
};

async function search() {
  const q = $("query").value.trim();
  const box = $("results");
  if (q.length < 2) { box.replaceChildren(); return; }
  if (!state.index.models) box.replaceChildren(el("p", { className: "muted", textContent: "Downloading the model lists (about half a minute, once)…" }));
  let results;
  try { results = await api("/api/search?q=" + encodeURIComponent(q)); } catch (e) { box.replaceChildren(el("p", { className: "muted", textContent: e.message })); return; }
  if (q !== $("query").value.trim()) return;
  box.replaceChildren(...results.map((m) => {
    const sites = [...new Set(m.measurements.map((x) => x.site))];
    const row = el("div", {}, el("b", { textContent: m.name }), el("small", { textContent: `${m.kind} · ${sites.join(", ")}` }));
    row.onclick = () => openBuilder(m, row);
    return row;
  }));
  if (!results.length) box.append(el("p", { className: "muted", textContent: "No measured model matches. Try fewer words." }));
}

function openBuilder(m, row) {
  model = m;
  document.querySelectorAll("#results .sel").forEach((n) => n.classList.remove("sel"));
  row.classList.add("sel");
  $("builder").hidden = false;
  $("builderName").textContent = m.name;
  $("builderMsg").textContent = m.measurements.length > 1
    ? "Measurements are listed by reviewer reliability. Ticking several averages them." : "";
  $("builderMeasurements").replaceChildren(...m.measurements.map((x, i) =>
    el("label", {}, el("input", { type: "checkbox", checked: i === 0, value: i }), `${x.site}${x.variant ? " · " + x.variant : ""}`)));
}

$("create").onclick = async () => {
  const picks = [...document.querySelectorAll("#builderMeasurements input:checked")].map((c) => model.measurements[+c.value]);
  if (!picks.length) { $("builderMsg").textContent = "Tick at least one measurement."; return; }
  const btn = $("create");
  btn.disabled = true;
  $("builderMsg").textContent = "Downloading the measurement and fitting filters…";
  try {
    const prof = await api("/api/profile/create", {
      name: model.name, kind: model.kind, measurements: picks, output: $("optOutput").value || null,
      options: { bands: +$("optBands").value, max_loss_db: $("optLoss").value ? +$("optLoss").value : null },
    });
    $("builderMsg").textContent = "Done.";
    await refresh();
    showProfile(prof);
  } catch (e) {
    $("builderMsg").textContent = "Failed: " + e.message;
  }
  btn.disabled = false;
};

$("refreshIndex").onclick = async () => {
  $("indexInfo").textContent = "updating…";
  try { await api("/api/index/refresh", {}); } catch (e) { $("indexInfo").textContent = e.message; return; }
  refresh();
};

// ---- profiles ----------------------------------------------------------------
function renderProfiles() {
  const box = $("profiles");
  box.replaceChildren(...state.profiles.map((p) => {
    const used = state.outputs.filter((o) => o.profile === p.id).map((o) => o.label);
    const row = el("div", { className: current && current.id === p.id ? "sel" : "" },
      el("b", { textContent: p.name + sourceNote(p) }),
      el("small", { textContent: `${p.filters.length} filters · preamp ${fmt(p.preamp_db)} dB` + (used.length ? ` · on ${used.join(", ")}` : "") }));
    row.onclick = async () => showProfile(await api("/api/profile?id=" + encodeURIComponent(p.id)));
    return row;
  }));
  if (!state.profiles.length) box.append(el("p", { className: "muted", textContent: "No profiles yet. Search for your headphone above." }));
}

const fmt = (x) => (x > 0 ? "+" : "") + Number(x).toFixed(1);

function showProfile(p) {
  current = p;
  $("detail").hidden = false;
  $("detailName").textContent = p.name + sourceNote(p);
  $("detailMsg").textContent = "";
  const s = p.stats || {};
  const parts = [`Preamp ${fmt(p.preamp_db)} dB`];
  if (s.loudness_change_db !== undefined) parts.push(`music plays about ${Math.abs(s.loudness_change_db).toFixed(1)} dB ${s.loudness_change_db < 0 ? "quieter" : "louder"}`);
  if (s.error_rms_db) parts.push("deviation from target: " + Object.entries(s.error_rms_db).map(([k, v]) => `${k} Hz ${v} dB`).join(", "));
  if (s.limited) parts.push(`limited (an exact match would cost ${s.exact_loss_db} dB)`);
  $("detailStats").textContent = parts.join(" · ");
  $("refitBands").value = (p.options && p.options.bands) || 10;
  $("refitLoss").value = p.options && p.options.max_loss_db != null ? String(p.options.max_loss_db) : "";
  $("refit").hidden = !(p.sources && p.sources.length);
  const c = p.curves;
  const hasMeasurement = !!c.measurement;
  $("plotResponse").style.display = hasMeasurement ? "" : "none";
  $("plotResponse").previousElementSibling.style.display = hasMeasurement ? "" : "none";
  if (hasMeasurement) plot($("plotResponse"), c.freqs, [[c.measurement, "--raw"], [c.target, "--target"], [c.equalized, "--result"]], 320);
  plot($("plotEq"), c.freqs, [[c.eq, "--eq"]], 200);
  $("filters").replaceChildren(
    el("tr", {}, ...["Type", "Frequency", "Gain", "Q"].map((h) => el("th", { textContent: h }))),
    ...p.filters.map((f) => el("tr", {},
      el("td", { textContent: { PK: "Peak", LSC: "Low shelf", HSC: "High shelf" }[f.type] }),
      el("td", { textContent: f.fc.toFixed(0) + " Hz" }), el("td", { textContent: fmt(f.gain) + " dB" }),
      el("td", { textContent: f.q.toFixed(2) }))));
  renderProfiles();
}

function plot(svg, freqs, series, height) {
  const W = 900, H = height, L = 44, R = 10, T = 10, B = 24;
  const all = series.flatMap(([ys]) => ys.filter((_, i) => freqs[i] >= 20 && freqs[i] <= 20000));
  let lo = Math.floor((Math.min(...all) - 1) / 5) * 5, hi = Math.ceil((Math.max(...all) + 1) / 5) * 5;
  if (hi - lo < 10) { lo -= 5; hi += 5; }
  const x = (f) => L + (Math.log(f / 20) / Math.log(1000)) * (W - L - R);
  const y = (d) => T + ((hi - d) / (hi - lo)) * (H - T - B);
  const ns = "http://www.w3.org/2000/svg";
  const mk = (tag, attrs) => { const n = document.createElementNS(ns, tag); for (const k in attrs) n.setAttribute(k, attrs[k]); return n; };
  svg.replaceChildren();
  for (const f of [20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000]) {
    svg.append(mk("line", { class: "grid", x1: x(f), x2: x(f), y1: T, y2: H - B }));
    const t = mk("text", { x: x(f), y: H - 7, "text-anchor": "middle" });
    t.textContent = f >= 1000 ? f / 1000 + "k" : f;
    svg.append(t);
  }
  for (let d = lo; d <= hi; d += 5) {
    svg.append(mk("line", { class: d === 0 ? "zero" : "grid", x1: L, x2: W - R, y1: y(d), y2: y(d) }));
    const t = mk("text", { x: L - 6, y: y(d) + 4, "text-anchor": "end" });
    t.textContent = d + " dB";
    svg.append(t);
  }
  for (const [ys, color] of series) {
    const d = ys.map((v, i) => `${i ? "L" : "M"}${x(freqs[i]).toFixed(1)},${y(v).toFixed(1)}`).join("");
    svg.append(mk("path", { d, style: `stroke:var(${color})` }));
  }
}

document.querySelectorAll("[data-export]").forEach((b) => {
  b.onclick = () => { location.href = `/api/profile/export?id=${encodeURIComponent(current.id)}&format=${b.dataset.export}`; };
});

$("refit").onclick = async () => {
  $("detailMsg").textContent = "Fitting…";
  try {
    showProfile(await api("/api/profile/refit", { id: current.id, options: { bands: +$("refitBands").value, max_loss_db: $("refitLoss").value ? +$("refitLoss").value : null } }));
    refresh();
  } catch (e) { $("detailMsg").textContent = e.message; }
};

$("verify").onclick = async () => {
  $("detailMsg").textContent = "Measuring the applied EQ (about 30 s, silent)…";
  try {
    const r = await api("/api/selftest", { id: current.id });
    $("detailMsg").textContent = `Applied EQ matches the design within ${r.max_dev_db} dB (RMS ${r.rms_dev_db} dB).`;
  } catch (e) { $("detailMsg").textContent = "Verification failed: " + e.message; }
};

$("delete").onclick = async () => {
  if (!confirm(`Delete the profile “${current.name}”?`)) return;
  await api("/api/profile/delete", { id: current.id });
  current = null;
  $("detail").hidden = true;
  refresh();
};

$("importBtn").onclick = async () => {
  try {
    const prof = await api("/api/profile/import", { name: $("importName").value.trim() || "Imported preset", text: $("importText").value });
    await refresh();
    showProfile(prof);
  } catch (e) { alert(e.message); }
};

$("enabled").onchange = async () => { await api("/api/enabled", { enabled: $("enabled").checked }); setTimeout(refresh, 600); };
$("usbLink").onclick = async () => { $("phoneMsg").textContent = "…"; $("phoneMsg").textContent = (await api("/api/phone/usb", {})).message; };

// #profile=<id> opens that profile directly
refresh().then(async () => {
  const id = new URLSearchParams(location.hash.slice(1)).get("profile");
  if (id) showProfile(await api("/api/profile?id=" + encodeURIComponent(id)));
});
setInterval(refresh, 4000);
