/* KanoFarm AI web app (vanilla JS, no build step). Every value shown comes from the API; missing data shows "—". */
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const f = (v, u = "") => v == null ? "—" : v + u;
const store = {
  get(k, d = null) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} },
  del(k) { try { localStorage.removeItem(k); } catch {} }
};
let CFG = { accounts_enabled: false }, CFG_FAILED = false, SESSION = store.get("kf_session"), PROFILE = null, ROLES = [], LANG = store.get("kf_lang", "en");
const GLOSS = { maize:"Masara", rice:"Shinkafa", sorghum:"Dawa", millet:"Gero", wheat:"Alkama", cowpea:"Wake", groundnut:"Gyada",
  soybean:"Waken suya", tomato:"Tumatir", pepper:"Barkono", onion:"Albasa", cassava:"Rogo", potato:"Dankalin turawa", vegetables:"Kayan lambu" };

/* ---------- api + auth ---------- */
async function raw(path, opt = {}) {
  const h = { "Content-Type": "application/json" };
  if (SESSION) h.Authorization = "Bearer " + SESSION.access_token;
  return fetch(path, { method: opt.method || (opt.json ? "POST" : "GET"), headers: h, body: opt.json ? JSON.stringify(opt.json) : undefined });
}
async function api(path, opt = {}) {
  let r = await raw(path, opt);
  if (r.status === 401 && SESSION && await refresh()) r = await raw(path, opt);
  const d = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(d.detail || "Something went wrong. Please try again."); e.status = r.status; throw e; }
  return d;
}
async function gotrue(path, body) {
  const r = await fetch(CFG.supabase_url + "/auth/v1/" + path, { method: "POST", headers: { "Content-Type": "application/json", apikey: CFG.supabase_anon_key }, body: JSON.stringify(body) });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.msg || d.error_description || d.message || "Could not sign in. Check your details and try again.");
  return d;
}
function keep(d) {
  if (!d.access_token) return false;
  SESSION = { access_token: d.access_token, refresh_token: d.refresh_token, expires_at: Date.now() + (d.expires_in || 3600) * 1000 };
  store.set("kf_session", SESSION); authLink(); return true;
}
async function refresh() {
  if (!SESSION?.refresh_token) return false;
  try { return keep(await gotrue("token?grant_type=refresh_token", { refresh_token: SESSION.refresh_token })); }
  catch { SESSION = null; store.del("kf_session"); return false; }
}
function signOut() { SESSION = null; PROFILE = null; ROLES = []; store.del("kf_session"); authLink(); location.hash = "#/"; }

/* offline: network first, else a clearly labelled saved copy */
async function cached(key, fn) {
  try { const data = await fn(); store.set("kf_c_" + key, { t: Date.now(), data }); setBanner(""); return data; }
  catch (e) {
    if (e.status) throw e;
    const c = store.get("kf_c_" + key);
    if (c) { setBanner("No connection. Showing a saved copy from " + new Date(c.t).toLocaleString() + "."); return c.data; }
    throw new Error("No connection, and nothing is saved on this phone yet.");
  }
}
function setBanner(t) { $("#banner").textContent = t; }
function authLink() { const a = $("#hdr-auth"); if (a) a.textContent = SESSION ? "Account" : "Sign in"; }
const view = h => { $("#view").innerHTML = h; window.scrollTo(0, 0); };
const card = (t, h, cls = "") => `<section class="card ${cls}">${t ? `<h2>${t}</h2>` : ""}${h}</section>`;
const errBox = e => `<div class="card err">${esc(e.message || e)}</div>`;
const needLogin = () => card("Please sign in", `<p>Sign in to use this part of KanoFarm AI.</p><a class="btn" href="#/account">Sign in or create account</a>`);
const attribution = m => `<p class="note">${esc(m.resolution_note)}<br>Source: ${esc(m.attribution)}. Fetched ${esc(m.fetched_at)}${m.from_cache ? " (cached)" : ""}.</p>`;
const DISCLAIMER = card("", `<p class="note">KanoFarm AI provides AI-assisted agricultural information and does not replace qualified agricultural extension professionals. Weather and risk estimates are not guarantees.</p>`);

/* ---------- shared renderers ---------- */
function advisories(list, review) {
  if (!list.length) return '<p class="note">No weather-based advisories right now.</p>';
  return list.map(a => `<div class="adv ${esc(a.level)}"><b>${esc(a.message.text)}</b>
    ${a.message.fallback ? "<small>Hausa text not yet reviewed, showing English.</small>" : ""}
    <small>Based on: ${esc(Object.entries(a.evidence).map(([k, v]) => k + ": " + JSON.stringify(v)).join("; "))}</small></div>`).join("")
    + `<p class="note">Thresholds: ${esc(review)}. Risk estimates are not guarantees.</p>`;
}
function nowCard(c, type) {
  return `<div class="grid"><div class="m"><b>${f(c.temperature_c, "°C")}</b><span>Temperature</span></div>
   <div class="m"><b>${f(c.humidity_pct, "%")}</b><span>Humidity</span></div>
   <div class="m"><b>${f(c.wind_kmh, " km/h")}</b><span>Wind</span></div>
   <div class="m"><b>${c.soil_moisture_m3m3 == null ? "—" : c.soil_moisture_m3m3.toFixed(2)}</b><span>Modelled surface soil moisture (m³/m³)</span></div></div>`;
}
function dailyTable(days) {
  return `<div class="scroll"><table><tr><th>Date</th><th>Max/Min °C</th><th>Rain mm</th><th>ET₀ mm</th></tr>${days.map(x =>
    `<tr><td>${esc(x.date.slice(5))}</td><td>${f(x.tmax_c)}/${f(x.tmin_c)}</td><td>${f(x.precip_mm)}</td><td>${f(x.et0_mm)}</td></tr>`).join("")}</table></div>`;
}
const cropLabel = c => esc(c.name_en) + (GLOSS[c.slug] ? ` (${GLOSS[c.slug]})` : "");
async function farmOptions() {
  if (!SESSION) return "";
  try { return (await api("/api/farms")).map(x => `<option value="${esc(x.id)}">${esc(x.name)}</option>`).join(""); } catch { return ""; }
}
function langSelect() {
  return `<label for="lang">Language</label><select id="lang"><option value="en">English</option>
    <option value="ha">Hausa (translations pending review)</option><option value="yo">Yoruba (translations pending review)</option>
    <option value="ig">Igbo (translations pending review)</option><option value="pcm">Nigerian Pidgin (translations pending review)</option></select>`;
}
let NG_STATES = null;
async function nigeriaStates() {
  if (NG_STATES) return NG_STATES;
  try { NG_STATES = await cached("states", () => api("/api/states")); } catch { NG_STATES = { states: [] }; }
  return NG_STATES;
}
async function stateSelect(id, selected = "") {
  const d = await nigeriaStates();
  const opts = d.states.map(s => `<option value="${esc(s.name)}" data-lat="${s.capital_latitude}" data-lon="${s.capital_longitude}" ${s.name === selected ? "selected" : ""}>${esc(s.name)}</option>`).join("");
  return `<label for="${id}">State</label><select id="${id}"><option value="">Select your state</option>${opts}</select>`;
}

/* ---------- views ---------- */
async function vHome() {
  view((!SESSION && CFG.accounts_enabled ? card("Save your farms", '<p>Create a free account to save farms, record crops and get alerts.</p><a class="btn" href="#/account">Sign in or create account</a>') : "")
    + card("Farm weather", `${await stateSelect("hst")}
    <p class="note">Picking a state fills in its capital as a starting point. Use GPS or edit the numbers for your actual farm.</p>
    <div class="row"><div><label for="lat">Latitude</label><input id="lat" inputmode="decimal" value=""></div>
    <div><label for="lon">Longitude</label><input id="lon" inputmode="decimal" value=""></div></div>${langSelect()}
    <button id="go">Get weather &amp; advice</button><button id="gps" class="alt">Use my location</button>`)
    + '<div id="out"></div>'
    + `<div class="tiles"><a class="tile" href="#/market"><i>🛒</i>Farm Market<small>Buy and sell produce</small></a>
       <a class="tile" href="#/farms"><i>🌱</i>My Farms<small>Crops, weather, alerts</small></a>
       <a class="tile" href="#/scan"><i>📷</i>Scan Plant<small>Check a sick plant</small></a>
       <a class="tile" href="#/calendar"><i>📅</i>Crop Calendar<small>Draft guide</small></a>
       <a class="tile" href="#/soil"><i>🧪</i>Soil &amp; Fertilizer<small>General guidance</small></a></div>` + DISCLAIMER);
  const loc = store.get("kf_loc"); if (loc) { $("#lat").value = loc.lat; $("#lon").value = loc.lon; if (loc.state) $("#hst").value = loc.state; }
  $("#lang").value = LANG;
  $("#lang").onchange = e => { LANG = e.target.value; store.set("kf_lang", LANG); };
  $("#hst").onchange = e => { const o = e.target.selectedOptions[0]; if (o && o.dataset.lat) { $("#lat").value = o.dataset.lat; $("#lon").value = o.dataset.lon; } };
  $("#go").onclick = loadWeather;
  $("#gps").onclick = () => navigator.geolocation ? navigator.geolocation.getCurrentPosition(p => {
      $("#lat").value = p.coords.latitude.toFixed(4); $("#lon").value = p.coords.longitude.toFixed(4); loadWeather(); },
    () => $("#out").innerHTML = errBox("Could not get your location. Enter coordinates manually.")) : $("#out").innerHTML = errBox("Location is not supported on this device.");
}
async function loadWeather() {
  const lat = parseFloat($("#lat").value), lon = parseFloat($("#lon").value), out = $("#out");
  if (!isFinite(lat) || !isFinite(lon)) { out.innerHTML = errBox("Pick your state, use \"Use my location\", or enter coordinates."); return; }
  out.innerHTML = card("", "Loading…");
  try {
    const d = await api(`/api/weather?lat=${lat}&lon=${lon}&lang=${LANG}`);
    store.set("kf_loc", { lat, lon, state: $("#hst")?.value || undefined });
    out.innerHTML = card(`Now <span class="tag">${esc(d.meta.data_type)}</span>`, nowCard(d.forecast.current))
      + card("Advice", advisories(d.advisories, d.meta.thresholds_review_status))
      + card("Daily (past 7 days + forecast)", dailyTable(d.forecast.days)) + card("", attribution(d.meta));
  } catch (e) { out.innerHTML = errBox(e.status ? e : "No connection. Weather needs the internet; nothing is shown from memory."); }
}

async function vFarms() {
  if (!SESSION) return view(needLogin());
  try {
    await ensureProfile(); if (!PROFILE) return vProfileForm();
    const farms = await cached("farms", () => api("/api/farms"));
    view(card("My Farms", farms.length ? farms.map(x => `<a class="tile" style="margin:8px 0" href="#/farm/${esc(x.id)}"><b>${esc(x.name)}</b>
      <small>${esc([x.community, x.ward, x.lga, x.state].filter(Boolean).join(", ") || "Nigeria")}${x.size_ha ? " · " + esc(x.size_ha) + " ha" : ""}</small></a>`).join("")
      : "<p>You have no farms yet. Add your first farm below.</p>")
      + card("Add a farm", `<label for="fn">Farm name</label><input id="fn" maxlength="120">
      ${await stateSelect("fst", PROFILE?.state || "")}
      <div class="row"><div><label for="fl">LGA</label><input id="fl" maxlength="80"></div><div><label for="fw">Ward</label><input id="fw" maxlength="80"></div></div>
      <label for="fc">Community</label><input id="fc" maxlength="80">
      <div class="row"><div><label for="fla">Latitude</label><input id="fla" inputmode="decimal" value="${esc(store.get("kf_loc")?.lat ?? "")}"></div>
      <div><label for="flo">Longitude</label><input id="flo" inputmode="decimal" value="${esc(store.get("kf_loc")?.lon ?? "")}"></div></div>
      <button id="fgps" class="alt">Use my location</button>
      <div class="row"><div><label for="fs">Size (hectares)</label><input id="fs" inputmode="decimal"></div><div><label for="fi">Irrigation</label><input id="fi" maxlength="60" placeholder="e.g. rainfed"></div></div>
      <button id="fadd">Save farm</button><div id="fmsg"></div>
      <p class="note">Your farm location is private. Weather is modelled for a grid of about 11 km, so it does not vary between wards.</p>`));
    $("#fst").onchange = e => { const o = e.target.selectedOptions[0]; if (o && o.dataset.lat && !$("#fla").value) { $("#fla").value = o.dataset.lat; $("#flo").value = o.dataset.lon; } };
    $("#fgps").onclick = () => navigator.geolocation?.getCurrentPosition(p => { $("#fla").value = p.coords.latitude.toFixed(5); $("#flo").value = p.coords.longitude.toFixed(5); });
    $("#fadd").onclick = async () => {
      try {
        await api("/api/farms", { json: { name: $("#fn").value, state: $("#fst").value, lga: $("#fl").value, ward: $("#fw").value, community: $("#fc").value,
          latitude: $("#fla").value, longitude: $("#flo").value, size_ha: $("#fs").value, irrigation_type: $("#fi").value } });
        vFarms();
      } catch (e) { $("#fmsg").innerHTML = errBox(e); }
    };
  } catch (e) { view(errBox(e)); }
}

async function vProfileForm() {
  view(card("Welcome! Tell us about you", `<label for="pn">Full name</label><input id="pn" maxlength="120">
    ${await stateSelect("pst")}
    ${langSelect().replace('id="lang"', 'id="pl"').replace('for="lang"', 'for="pl"')}
    <div class="row"><div><label for="plga">LGA</label><input id="plga"></div><div><label for="pw">Ward</label><input id="pw"></div></div>
    <label for="pc">Community</label><input id="pc"><label for="pf">Farm type</label><input id="pf" placeholder="e.g. smallholder, irrigated">
    <label for="pe">Years of farming experience</label><input id="pe" inputmode="numeric">
    <button id="psave">Save</button><div id="pmsg"></div>`));
  $("#psave").onclick = async () => {
    try {
      PROFILE = await api("/api/profile", { method: "PUT", json: { full_name: $("#pn").value, state: $("#pst").value, language: $("#pl").value, lga: $("#plga").value,
        ward: $("#pw").value, community: $("#pc").value, farm_type: $("#pf").value, experience_years: $("#pe").value } });
      LANG = PROFILE.language; store.set("kf_lang", LANG); vFarms();
    } catch (e) { $("#pmsg").innerHTML = errBox(e); }
  };
}
async function ensureProfile() {
  if (PROFILE) return;
  const d = await api("/api/profile"); PROFILE = d.profile; ROLES = d.roles || [];
  if (PROFILE?.language) { LANG = PROFILE.language; store.set("kf_lang", LANG); }
}

async function vFarm(id) {
  if (!SESSION) return view(needLogin());
  view(card("", "Loading…"));
  try {
    const d = await cached("dash_" + id, () => api(`/api/farms/${id}/dashboard?lang=${LANG}`));
    const [crops, obs, alerts, rules] = await Promise.all([api("/api/crops").catch(() => []), api(`/api/farms/${id}/observations`).catch(() => []),
      api(`/api/farms/${id}/alerts`).catch(() => []), api(`/api/farms/${id}/alert-rules`).catch(() => [])]);
    const c = d.forecast.current;
    view(card(esc(d.farm.name), `<p class="note">${esc([d.farm.community, d.farm.ward, d.farm.lga, d.farm.state].filter(Boolean).join(", ") || "Nigeria")}${d.farm.zone ? ` <span class="tag draft">${esc(d.farm.zone.replace(/_/g, " "))} zone</span>` : ""}</p>`)
      + card(`Now <span class="tag">${esc(d.meta.data_type)}</span>`, nowCard(c))
      + card("Advice for this farm", advisories(d.advisories, d.meta.thresholds_review_status))
      + card("Irrigation guidance", (d.irrigation.signals.map(s => `<div class="adv"><b>${esc(s.text)}</b></div>`).join("") || `<p>${esc(d.irrigation.none)}</p>`)
        + `<p class="note">${esc(d.irrigation.soil_moisture_note)} ${esc(d.irrigation.volume_note)}</p>`)
      + card("Crops", (d.crops.length ? d.crops.map(x => `<div class="adv"><b>${esc(x.name)}${x.variety ? " (" + esc(x.variety) + ")" : ""}</b>
          Planted ${esc(x.planting_date)} · <b style="display:inline">${x.days_after_planting}</b> days after planting
          ${x.expected_harvest_date ? "<br>Expected harvest: " + esc(x.expected_harvest_date) + " (your estimate)" : ""}
          <small>${esc(x.stage_note)}</small></div>`).join("") : "<p>No crops added yet.</p>")
        + `<h3>Add a crop</h3><label for="cc">Crop</label><select id="cc">${crops.map(x => `<option value="${esc(x.id)}">${cropLabel(x)}</option>`).join("")}</select>
        <label for="cv">Variety (optional)</label><input id="cv" maxlength="80"><label for="cp">Planting date</label><input id="cp" type="date">
        <label for="ch">Expected harvest date (optional)</label><input id="ch" type="date"><button id="cadd">Add crop</button><div id="cmsg"></div>
        <p class="note">Hausa crop names shown are unreviewed.</p>`)
      + '<div id="rain"></div>'
      + card("Past week and 7-day outlook", outlookCharts(d.forecast.days, d.forecast.current.time.slice(0, 10)) + "<h3>Table</h3>" + dailyTable(d.forecast.days))
      + card("Not calculated yet", `<p class="note">${esc(d.risks_not_computed.join(", "))} — these need data this app does not have yet, so they are not shown.</p>`)
      + card("Record something", `<label for="ok">What happened?</label><select id="ok">${["observation","pest","disease","treatment","fertilizer","irrigation","harvest","weather_event","germination","planting","note"].map(k => `<option>${k}</option>`).join("")}</select>
        <label for="od">Date</label><input id="od" type="date" value="${new Date().toISOString().slice(0, 10)}"><label for="ot">Notes</label><textarea id="ot" maxlength="2000"></textarea>
        <button id="oadd">Save to timeline</button><div id="omsg"></div>`)
      + card("Farm timeline", obs.length ? `<ul class="tl">${obs.map(o => `<li><b>${esc(o.observed_on)} · ${esc(o.kind)}</b>${esc(o.text || "")}</li>`).join("")}</ul>` : "<p>Nothing recorded yet.</p>")
      + card("Alert settings", ["heavy_rain|Heavy rain (mm per day)", "dry_spell|Dry spell (days)", "heat_stress|Heat stress (max °C)", "high_water_demand|High water demand (ET₀ mm/day)", "rain_delay_irrigation|Rain expected (mm in 2 days)"].map(s => {
          const [k, l] = s.split("|"), r = rules.find(x => x.alert_type === k) || {};
          return `<label>${esc(l)}</label><div class="row"><input data-th="${k}" inputmode="decimal" placeholder="default" value="${esc(r.threshold ?? "")}"><select data-en="${k}"><option value="1">On</option><option value="0" ${r.enabled === false ? "selected" : ""}>Off</option></select></div>`; }).join("")
        + `<button id="rsave" class="alt">Save alert settings</button><div id="rmsg"></div><p class="note">Defaults are unreviewed by an agronomist. Set your own if you know better.</p>`)
      + card("Recent alerts", alerts.length ? alerts.map(a => `<div class="adv ${esc(a.level)}"><b>${esc(a.alert_date)}</b> ${esc(a.message_key.replace(/_/g, " "))}</div>`).join("") : "<p>No alerts yet.</p>")
      + card("", `<a class="btn" href="#/scan/${esc(id)}">Scan a plant on this farm</a><button id="fdel" class="danger">Delete this farm</button>`) + card("", attribution(d.meta)));
    api(`/api/farms/${id}/rainfall`).then(r => { $("#rain").innerHTML = r.crops.map(rainfallCard).join("") + (r.crops.length ? card("", `<p class="note">${esc(r.meta.note)} ${esc(r.meta.attribution)}.</p>`) : ""); })
      .catch(() => { $("#rain").innerHTML = card("Rainfall since planting", '<p class="note">Rainfall history is unavailable right now. Please try again later.</p>'); });
    $("#cadd").onclick = async () => { try { await api(`/api/farms/${id}/crops`, { json: { crop_id: $("#cc").value, variety: $("#cv").value, planting_date: $("#cp").value, expected_harvest_date: $("#ch").value } }); vFarm(id); } catch (e) { $("#cmsg").innerHTML = errBox(e); } };
    $("#oadd").onclick = async () => { try { await api(`/api/farms/${id}/observations`, { json: { kind: $("#ok").value, observed_on: $("#od").value, text: $("#ot").value } }); vFarm(id); } catch (e) { $("#omsg").innerHTML = errBox(e); } };
    $("#rsave").onclick = async () => { try {
      for (const t of document.querySelectorAll("[data-th]")) { const k = t.dataset.th, en = $(`[data-en="${k}"]`).value === "1";
        if (t.value !== "" || !en || rules.some(r => r.alert_type === k)) await api(`/api/farms/${id}/alert-rules`, { method: "PUT", json: { alert_type: k, threshold: t.value, enabled: en } }); }
      vFarm(id); } catch (e) { $("#rmsg").innerHTML = errBox(e); } };
    $("#fdel").onclick = async () => { if (confirm("Delete this farm and its records? This cannot be undone.")) { await api(`/api/farms/${id}`, { method: "DELETE" }); location.hash = "#/farms"; } };
  } catch (e) { view(errBox(e)); }
}


/* ---------- tiny SVG charts (no library; readable in light and dark mode) ---------- */
function barSvg(items, label, unit = "mm") {
  const W = Math.max(320, items.length * 34), H = 150, top = 22, base = 112, bw = Math.min(24, (W - 20) / items.length - 6);
  const max = Math.max(1, ...items.map(i => i.value || 0)), step = (W - 20) / items.length;
  const bars = items.map((it, k) => {
    const h = it.missing ? 0 : Math.round((it.value / max) * (base - top)), x = 10 + k * step + (step - bw) / 2;
    return `<g>${it.missing ? `<text x="${x + bw / 2}" y="${base - 4}" text-anchor="middle" font-size="11" fill="currentColor">?</text>`
      : `<rect x="${x}" y="${base - h}" width="${bw}" height="${Math.max(h, it.value > 0 ? 2 : 0)}" rx="3" fill="${it.future ? "var(--g)" : "var(--info)"}"/>`}
      ${!it.missing && it.value > 0 ? `<text x="${x + bw / 2}" y="${base - h - 4}" text-anchor="middle" font-size="10" fill="currentColor">${it.value}</text>` : ""}
      <text x="${x + bw / 2}" y="${base + 14}" text-anchor="middle" font-size="10" fill="currentColor">${esc(it.label)}</text>
      ${it.today ? `<text x="${x + bw / 2}" y="${base + 27}" text-anchor="middle" font-size="9" fill="currentColor">today</text>` : ""}</g>`; }).join("");
  const desc = items.map(i => `${i.label}: ${i.missing ? "no data" : i.value + " " + unit}`).join(", ");
  return `<div class="scroll"><svg role="img" aria-label="${esc(label)}" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" style="color:var(--ink)"><title>${esc(label)}</title><desc>${esc(desc)}</desc>
    <line x1="8" x2="${W - 8}" y1="${base}" y2="${base}" stroke="currentColor" opacity=".35"/>${bars}</svg></div>`;
}
function tempSvg(days, todayStr) {
  const pts = days.filter(d => d.tmax_c != null && d.tmin_c != null); if (pts.length < 2) return "";
  const W = Math.max(320, days.length * 34), H = 150, lo = Math.min(...pts.map(d => d.tmin_c)) - 1, hi = Math.max(...pts.map(d => d.tmax_c)) + 1;
  const step = (W - 20) / days.length, X = i => 10 + i * step + step / 2, Y = v => 15 + (hi - v) / (hi - lo) * 95;
  const line = k => days.map((d, i) => d[k] == null ? null : `${X(i).toFixed(1)},${Y(d[k]).toFixed(1)}`).filter(Boolean).join(" ");
  const labels = days.map((d, i) => `<text x="${X(i)}" y="138" text-anchor="middle" font-size="10" fill="currentColor">${esc(d.date.slice(8))}</text>`).join("");
  const desc = days.map(d => `${d.date}: ${f(d.tmax_c)} max, ${f(d.tmin_c)} min`).join("; ");
  return `<div class="scroll"><svg role="img" aria-label="Daily maximum and minimum temperature in °C" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" style="color:var(--ink)"><title>Temperature °C</title><desc>${esc(desc)}</desc>
    <polyline points="${line("tmax_c")}" fill="none" stroke="var(--clay)" stroke-width="3"/><polyline points="${line("tmin_c")}" fill="none" stroke="var(--info)" stroke-width="3"/>
    <text x="10" y="12" font-size="10" fill="var(--clay)">max °C ${f(Math.max(...pts.map(d => d.tmax_c)))}</text><text x="120" y="12" font-size="10" fill="var(--info)">min °C ${f(Math.min(...pts.map(d => d.tmin_c)))}</text>${labels}</svg></div>`;
}
function outlookCharts(days, todayStr) {
  const items = days.map(d => ({ label: d.date.slice(8), value: d.precip_mm ?? 0, missing: d.precip_mm == null, future: d.date >= todayStr, today: d.date === todayStr }));
  return `<h3>Rain per day (mm)</h3>${barSvg(items, "Daily rainfall in millimetres")}<p class="note"><span style="color:var(--info)">■</span> past days · <span style="color:var(--g)">■</span> forecast · ? = no data</p>
    <h3>Temperature</h3>${tempSvg(days, todayStr)}`;
}
function rainfallCard(c) {
  const has = c.days > 0;
  const head = !has ? `<p>${esc(c.note || "No full days of rainfall yet.")}</p>` : `<div class="grid">
    <div class="m"><b>${c.total_mm == null ? "—" : (c.complete ? "" : "≥ ") + c.total_mm + " mm"}</b><span>Rain since planting (${c.days} days)</span></div>
    <div class="m"><b>${c.rainy_days}</b><span>Rainy days (≥ 1 mm)</span></div>
    <div class="m"><b>${f(c.max_day_mm, " mm")}</b><span>Wettest day</span></div>
    <div class="m"><b>${c.longest_dry_run_days}</b><span>Longest dry run (days)</span></div></div>`;
  const chart = has ? `<h3>Rain per week since planting (mm)</h3>` + barSvg(c.weeks.map(w => ({ label: "W" + w.week, value: w.total_mm, missing: w.missing_days === w.days })), "Weekly rainfall since planting in millimetres") : "";
  const warn = has && !c.complete ? `<p class="err">${c.missing_days} of ${c.days} days have no data, so the total is a minimum, not the full amount.</p>` : "";
  return card(`Rainfall since planting: ${esc(c.crop)}`, head + warn + chart
    + (has ? `<p class="note">Through ${esc(c.through)} · ${c.sources.reanalysis} days from historical reanalysis, ${c.sources.recent_model} recent days from model output.</p>` : ""));
}

/* ---------- Plant Doctor ---------- */
function qualityMetrics(img) {
  const w = 256, h = Math.max(1, Math.round(256 * img.height / img.width)), c = document.createElement("canvas");
  c.width = w; c.height = h; const x = c.getContext("2d", { willReadFrequently: true }); x.drawImage(img, 0, 0, w, h);
  const d = x.getImageData(0, 0, w, h).data, g = new Float32Array(w * h); let sum = 0;
  for (let i = 0; i < w * h; i++) { g[i] = 0.299 * d[4 * i] + 0.587 * d[4 * i + 1] + 0.114 * d[4 * i + 2]; sum += g[i]; }
  let m = 0, m2 = 0, n = 0;                                   // variance of the Laplacian = sharpness
  for (let y = 1; y < h - 1; y++) for (let xx = 1; xx < w - 1; xx++) {
    const i = y * w + xx, l = g[i - w] + g[i + w] + g[i - 1] + g[i + 1] - 4 * g[i]; m += l; m2 += l * l; n++; }
  return { brightness: +(sum / (w * h)).toFixed(1), sharpness: +(m2 / n - (m / n) ** 2).toFixed(1) };
}
function loadImage(file) {
  return new Promise((res, rej) => { const u = URL.createObjectURL(file), i = new Image();
    i.onload = () => { URL.revokeObjectURL(u); res(i); }; i.onerror = () => rej(new Error("We couldn't read this image. Please try another photo.")); i.src = u; });
}
async function prepareImage(file) {
  const img = await loadImage(file), s = Math.min(1, 1024 / Math.max(img.width, img.height));
  const c = document.createElement("canvas"); c.width = Math.round(img.width * s); c.height = Math.round(img.height * s);
  c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
  const url = c.toDataURL("image/jpeg", 0.8);
  return { url, b64: url.split(",")[1], quality: qualityMetrics(img) };
}
function ipmHtml(p) {
  return `<h3>What to do (integrated pest management)</h3>` + p.steps.map(s => `<div class="adv"><b>${esc(s.stage)}</b>${s.items.map(i =>
    `<div>${esc(i.action)}<small>Why: ${esc(i.why)}</small></div>`).join("")}</div>`).join("")
    + `<p class="note">General guidance (${esc(p.review_status)}), not a crop-specific prescription.</p>`;
}
function scanResult(r) {
  if (r.status === "quality_failed") return card("Photo not clear enough", `<p class="err">${esc(r.message)}</p><p class="note">Problem: ${esc(r.quality.problems.join(", ").replace(/_/g, " "))}. Tip: good daylight, hold steady, fill the frame with one leaf.</p>`);
  if (r.status === "error") return errBox(r.message);
  let h = "";
  if (r.status === "ok") {
    h += `<p><b style="font-size:1.3rem">${esc(r.condition)}</b><br>Crop: ${esc(r.crop)} · Type: ${esc(String(r.category).replace(/_/g, " "))}</p>
      <p>Confidence: <b>${Math.round(r.confidence * 100)}%</b> <span class="pill">${esc(r.confidence_level)}</span> · Severity: not assessed by this model</p>
      ${r.alternatives?.length ? `<p class="note">Other possibilities: ${r.alternatives.map(a => esc(a.label) + " " + Math.round(a.probability * 100) + "%").join(", ")}</p>` : ""}`;
  } else h += `<p class="err">${esc(r.message)}</p>`;
  h += ipmHtml(r.ipm);
  if (r.treatment_products) h += `<h3>Verified treatment information</h3>` + (r.treatment_products.products.length
    ? r.treatment_products.products.map(p => `<div class="adv"><b>${esc(p.product_name)}</b> (${esc(p.active_ingredient)})<small>Reg. no. ${esc(p.registration_number)} · Source: ${esc(p.source_name)} · verified ${esc(p.last_verified)}, valid until ${esc(p.expires_at)}${p.pre_harvest_interval_days != null ? " · pre-harvest interval " + esc(p.pre_harvest_interval_days) + " days" : ""}</small></div>`).join("")
    : `<p>${esc(r.treatment_products.message)}</p>`) + `<p class="note">${esc(r.treatment_products.disclaimer)}</p>`;
  return card("Result", h + `<p class="note">${esc(r.disclaimer)}</p>` + (r.validated_for_nigerian_field_conditions === false ? '<p class="note">This model has not been validated on Nigerian field conditions.</p>' : ""));
}
async function vScan(farmId) {
  if (!SESSION) return view(needLogin());
  const [farms, crops] = await Promise.all([farmOptions(), api("/api/crops").catch(() => [])]);
  view(card("Scan my plant", `${CFG.model_enabled ? "" : '<p class="note err">The Plant Doctor AI model is not connected yet. Photo checks work, but no diagnosis can be given.</p>'}
    <label for="sf">Farm (optional)</label><select id="sf"><option value="">—</option>${farms}</select>
    <label for="sc">Which crop is this?</label><select id="sc"><option value="">Not sure</option>${crops.map(x => `<option value="${esc(x.slug)}">${cropLabel(x)}</option>`).join("")}</select>
    <label for="si">Take a photo or choose one</label><input id="si" type="file" accept="image/*" capture="environment">
    <label><input id="sk" type="checkbox" style="width:auto"> Let KanoFarm AI keep this photo to help improve the model (experts may review it)</label>
    <div id="sp"></div><button id="sgo" disabled>Check plant</button><div id="sr"></div>`) + DISCLAIMER);
  if (farmId) $("#sf").value = farmId;
  let prep = null;
  $("#si").onchange = async ev => {
    const file = ev.target.files[0]; prep = null; $("#sgo").disabled = true; $("#sr").innerHTML = "";
    if (!file) return;
    try {
      prep = await prepareImage(file); $("#sp").innerHTML = `<img class="preview" alt="Selected plant photo" src="${prep.url}">`;
      const q = CFG.quality || {}, probs = [];
      if (prep.quality.brightness < q.min_brightness) probs.push("too dark"); if (prep.quality.brightness > q.max_brightness) probs.push("too bright");
      if (prep.quality.sharpness < q.min_sharpness) probs.push("blurry");
      if (probs.length) $("#sr").innerHTML = card("Photo not clear enough", `<p class="err">The image quality is not sufficient for reliable diagnosis. Please take another clear photo of the affected leaf or plant.</p><p class="note">Problem: ${esc(probs.join(", "))}.</p>`);
      else $("#sgo").disabled = false;
    } catch (e) { $("#sr").innerHTML = errBox(e); }
  };
  $("#sgo").onclick = async () => {
    $("#sgo").disabled = true; $("#sr").innerHTML = card("", "Checking your photo…");
    try {
      const r = await api("/api/plant/scan", { json: { image_b64: prep.b64, quality: prep.quality, crop_hint: $("#sc").value || null,
        farm_id: $("#sf").value || null, contribute_image: $("#sk").checked } });
      $("#sr").innerHTML = scanResult(r);
    } catch (e) { $("#sr").innerHTML = errBox(e.status ? e : "No connection. Plant scans need the internet."); }
    $("#sgo").disabled = false;
  };
}

/* ---------- guides & tools ---------- */
const ZONE_NAMES = { north_west: "North West", north_east: "North East", north_central: "North Central",
  south_west: "South West", south_east: "South East", south_south: "South South" };
async function vCalendar(zone = "") {
  try {
    const [d, sd] = await Promise.all([cached("calendar_" + zone, () => api("/api/crop-calendar" + (zone ? `?zone=${zone}` : ""))), nigeriaStates()]);
    const by = {}; d.entries.forEach(e => (by[e.crop_slug] ||= []).push(e));
    const zoneOpts = Object.keys(sd.zones || ZONE_NAMES).map(z => `<option value="${z}" ${z === zone ? "selected" : ""}>${esc((sd.zones?.[z]?.name) || ZONE_NAMES[z])}</option>`).join("");
    view(card("Crop calendar", `<p class="err">${esc(d.banner)}</p>
      <label for="czone">Show only entries for a zone</label><select id="czone"><option value="">All zones</option>${zoneOpts}</select>`)
      + (Object.keys(by).length ? Object.entries(by).map(([slug, es]) => card(esc(slug[0].toUpperCase() + slug.slice(1)).replace(/_/g, " ") + (GLOSS[slug] ? ` (${GLOSS[slug]})` : ""),
        es.map(e => `<div class="adv"><b>${esc(e.activity.replace(/_/g, " "))}: ${esc(e.months_label || "see note")}</b><span class="tag draft">${esc(e.review_status.replace(/_/g, " "))}</span>
          ${e.zones && !e.zones.includes("all") ? `<span class="tag">${e.zones.map(z => esc(ZONE_NAMES[z] || z)).join(", ")}</span>` : ""}
          ${e.note ? `<small>${esc(e.note)}</small>` : ""}<small>Source: <a href="${esc(e.source_url)}" rel="noopener" target="_blank">${esc(e.source_name)}</a></small></div>`).join("")))
        : card("", "<p>No draft entries yet for this zone.</p>")) + DISCLAIMER);
    $("#czone").onchange = e => vCalendar(e.target.value);
  } catch (e) { view(errBox(e)); }
}
async function vSoil() {
  const farms = await farmOptions();
  view(card("Soil & fertilizer advisor", `<p class="note">Leave blank anything you do not know. Without a soil test the advice is general.</p>
    <label for="st">Soil type</label><input id="st" maxlength="60" placeholder="e.g. sandy, clay">
    <div class="row"><div><label for="sph">pH</label><input id="sph" inputmode="decimal"></div><div><label for="som">Organic matter %</label><input id="som" inputmode="decimal"></div></div>
    <div class="row"><div><label for="sn">Nitrogen</label><input id="sn" inputmode="decimal"></div><div><label for="sp2">Phosphorus</label><input id="sp2" inputmode="decimal"></div><div><label for="sk2">Potassium</label><input id="sk2" inputmode="decimal"></div></div>
    <label for="su">Units / lab method for N, P, K</label><input id="su" maxlength="120">
    <label for="spc">Previous crop</label><input id="spc" maxlength="60"><label for="sfa">Fertilizer already applied</label><input id="sfa" maxlength="200">
    ${farms ? `<label for="sfm">Save to farm records (optional)</label><select id="sfm"><option value="">Do not save</option>${farms}</select>` : ""}
    <button id="sgo">Get guidance</button><div id="sout"></div>`));
  $("#sgo").onclick = async () => {
    const body = { soil_type: $("#st").value, ph: $("#sph").value, organic_matter_pct: $("#som").value, nitrogen: $("#sn").value, phosphorus: $("#sp2").value,
      potassium: $("#sk2").value, npk_units_method: $("#su").value, previous_crop: $("#spc").value, fertilizer_applied: $("#sfa").value };
    try {
      const fm = $("#sfm")?.value, r = await api(fm ? `/api/farms/${fm}/soil` : "/api/soil/advice", { json: body });
      $("#sout").innerHTML = card("Guidance", `<p class="note">${esc(r.notice)}</p><ul>${r.tips.map(t => `<li>${esc(t)}</li>`).join("")}</ul><p>${esc(r.timing)}</p>`);
    } catch (e) { $("#sout").innerHTML = errBox(e); }
  };
}
async function vHistory() {
  if (!SESSION) return view(needLogin());
  view(card("Farm history", `<input id="hq" placeholder="Search notes, crops, diagnoses" maxlength="60"><button id="hgo">Search</button>`) + '<div id="hout"></div>');
  const run = async () => {
    try {
      const d = await cached("history_" + $("#hq").value, () => api("/api/history?q=" + encodeURIComponent($("#hq").value)));
      $("#hout").innerHTML = card("Records", d.observations.length ? `<ul class="tl">${d.observations.map(o => `<li><b>${esc(o.observed_on)} · ${esc(o.kind)}</b>${esc(o.text || "")}</li>`).join("")}</ul>` : "<p>No records found.</p>")
        + card("Plant scans", d.scans.length ? d.scans.map(s => { const g = (s.diagnoses || [])[0] || {}; return `<div class="adv"><b>${esc(s.created_at.slice(0, 10))}</b> ${esc(s.crop_hint || "crop not stated")}
          <small>${g.status === "ok" ? esc(g.top_label) + " · " + Math.round(g.confidence * 100) + "% (" + esc(g.level) + ")" : esc((g.status || "").replace(/_/g, " "))}</small></div>`; }).join("") : "<p>No scans yet.</p>");
    } catch (e) { $("#hout").innerHTML = errBox(e); }
  };
  $("#hgo").onclick = run; run();
}
/* ---------- voice (progressive enhancement; not every phone supports these) ---------- */
const BCP47 = { en: "en-NG", ha: "ha-NG", yo: "yo-NG", ig: "ig-NG", pcm: "en-NG" };
const SR_CTOR = window.SpeechRecognition || window.webkitSpeechRecognition;
const canListen = !!SR_CTOR;
const canSpeak = "speechSynthesis" in window;
let currentRecognition = null;

function micButtonHtml() {
  if (!canListen) return "";
  return `<button id="mic" type="button" class="alt mic-btn" title="Speak your question" aria-label="Speak your question">🎤</button>`;
}
function speakerButtonHtml(idx) {
  if (!canSpeak) return "";
  return `<button class="speak-btn" data-speak="${idx}" title="Read this aloud" aria-label="Read this aloud">🔊</button>`;
}
function stopSpeaking() { if (canSpeak) speechSynthesis.cancel(); }
function speakText(text, lang) {
  if (!canSpeak || !text) return;
  stopSpeaking();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = BCP47[lang] || "en-NG";
  speechSynthesis.speak(u);
}
const MIC_ERROR = {
  "not-allowed": "Microphone access was blocked. Check your browser's site settings and allow the microphone.",
  "service-not-allowed": "Microphone access was blocked. Check your browser's site settings and allow the microphone.",
  "no-speech": "No speech was heard. Please try again.",
  "audio-capture": "No microphone was found on this device.",
  "network": "Voice input needs an internet connection. Please check your connection and try again.",
  "language-not-supported": "Voice input does not support this language on this device yet. Try English, or type your question.",
  "aborted": "",
};
function startListening(lang, onText, onDone) {
  if (!canListen) return;
  if (currentRecognition) { currentRecognition.stop(); return; }
  const r = new SR_CTOR();
  currentRecognition = r;
  r.lang = BCP47[lang] || "en-NG";
  r.continuous = false; r.interimResults = true; r.maxAlternatives = 1;
  let finalText = "";
  r.onresult = e => {
    let interim = "";
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const t = e.results[i][0].transcript;
      if (e.results[i].isFinal) finalText += t; else interim += t;
    }
    onText(finalText + interim);
  };
  r.onerror = e => { const m = MIC_ERROR[e.error]; if (m) onDone(m); else onDone(""); };
  r.onend = () => { currentRecognition = null; onDone(""); };
  try { r.start(); } catch { currentRecognition = null; onDone("Could not start voice input. Please try again."); }
}

/* ---------- Ask KanoFarm (chat) ---------- */
let CHAT_FARM = "";
function chatKey() { return "kf_chat_" + (CHAT_FARM || "none"); }
function chatHistory() {
  // sanitize any "thinking" placeholder left over from a page reload mid-request
  const h = store.get(chatKey(), []).map(m => m.thinking ? { role: "assistant", content: "That question was interrupted. Please ask again.", error: true } : m);
  return h;
}
function saveChatHistory(h) { store.set(chatKey(), h.slice(-20)); }
function chatBubbles(history) {
  return history.map((m, i) => `<div class="msg ${m.role}">${m.role === "assistant" && m.error ? `<span class="err">${esc(m.content)}</span>` : esc(m.content).replace(/\n/g, "<br>")}
    ${m.role === "assistant" && !m.error && !m.thinking ? speakerButtonHtml(i) : ""}</div>`).join("")
    || '<p class="note">Ask about a sick plant, when to plant, what to do about dry weather, or anything else about your farm.</p>';
}
async function vAssistant() {
  if (!SESSION) return view(needLogin());
  if (!CFG.assistant_enabled) return view(card("Ask KanoFarm", "<p>The AI assistant is not switched on yet. Meanwhile, use Scan Plant, the Crop Calendar and your farm advice, or ask an extension officer.</p>"));
  const farms = await farmOptions();
  view(card("Ask KanoFarm", `<label for="af">Which farm is this about? (optional)</label><select id="af"><option value="">—</option>${farms}</select>`)
    + `<div id="chat" class="chat"></div>`
    + card("", `<div class="row"><textarea id="aq" maxlength="1000" placeholder="e.g. My maize leaves are turning yellow." rows="2"></textarea></div>
      <div class="row">${micButtonHtml()}<button id="ago">Send</button></div>
      <button id="anew" class="alt">New conversation</button>
      <div id="micstatus" class="note"></div>
      <p class="note">Answers are AI-generated and can be wrong. They do not replace an extension officer. Your LGA and ward, crop and weather advisories are sent to the AI; your name, phone and exact location are not.
      ${canListen ? " Tap the microphone to speak your question; it needs an internet connection and works best in English on most phones." : ""}
      ${canSpeak ? " Tap the speaker icon on a reply to have it read aloud." : ""}</p>`));
  CHAT_FARM = "";
  const renderChat = () => {
    $("#chat").innerHTML = chatBubbles(chatHistory()); $("#chat").scrollTop = $("#chat").scrollHeight;
    document.querySelectorAll("[data-speak]").forEach(b => b.onclick = () => {
      const m = chatHistory()[+b.dataset.speak]; if (m) speakText(m.content, LANG);
    });
  };
  renderChat();
  $("#af").onchange = () => { CHAT_FARM = $("#af").value; renderChat(); };
  $("#anew").onclick = () => { stopSpeaking(); saveChatHistory([]); renderChat(); };
  const send = async () => {
    const text = $("#aq").value.trim();
    if (!text) return;
    const history = chatHistory();
    history.push({ role: "user", content: text }); saveChatHistory(history); renderChat();
    $("#aq").value = ""; $("#ago").disabled = true;
    const thinking = { role: "assistant", content: "…", thinking: true };
    history.push(thinking); saveChatHistory(history); renderChat();
    try {
      const r = await api("/api/assistant", { json: { message: text, history: history.slice(0, -2).filter(m => !m.error),
        farm_id: $("#af").value || null, lang: LANG } });
      history[history.length - 1] = { role: "assistant", content: r.reply };
    } catch (e) {
      history[history.length - 1] = { role: "assistant", content: e.message || "Something went wrong. Please try again.", error: true };
    }
    saveChatHistory(history); renderChat(); $("#ago").disabled = false;
  };
  $("#ago").onclick = send;
  $("#aq").addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
  if (canListen) {
    $("#mic").onclick = () => {
      const mic = $("#mic"), status = $("#micstatus"), base = $("#aq").value;
      if (mic.classList.contains("listening")) { currentRecognition?.stop(); return; }
      mic.classList.add("listening"); mic.textContent = "⏹"; status.textContent = "Listening…";
      startListening(LANG,
        text => { $("#aq").value = (base ? base + " " : "") + text; },
        message => { mic.classList.remove("listening"); mic.textContent = "🎤"; status.textContent = message || ""; });
    };
  }
}
async function vSources() {
  try {
    const d = await cached("sources", () => api("/api/sources"));
    view(card("Data sources", '<p class="note">Where our information comes from, how much to trust it, and what it cannot tell you.</p>') + d.sources.map(s =>
      card(`<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.name)}</a>`, `<span class="tag">${esc(s.data_type)}</span>
      <p>Coverage: ${esc(s.coverage)}<br>Updates: ${esc(s.update_frequency)}<br>License: ${esc(s.license)}<br>Free tier: ${esc(s.free_tier)}<br>Last verified: ${esc(s.last_verified)}</p>
      <p class="note">Limitation: ${esc(s.limitations)}</p>`)));
  } catch (e) { view(errBox(e)); }
}

/* ---------- account / more / admin ---------- */
async function vMore() {
  view(`<div class="tiles"><a class="tile" href="#/market/mine"><i>🛒</i>My Listings</a><a class="tile" href="#/history"><i>🗂️</i>Farm History</a><a class="tile" href="#/calendar"><i>📅</i>Crop Calendar</a>
    <a class="tile" href="#/soil"><i>🧪</i>Soil &amp; Fertilizer</a><a class="tile" href="#/sources"><i>ℹ️</i>Data Sources</a>
    <a class="tile" href="#/account"><i>👤</i>Account</a>${ROLES.length ? '<a class="tile" href="#/admin"><i>🛠️</i>Expert / Admin</a>' : ""}</div>`
    + card("", '<button id="inst" class="alt" hidden>Install KanoFarm AI on this phone</button>') + DISCLAIMER);
  if (deferred) { $("#inst").hidden = false; $("#inst").onclick = async () => { deferred.prompt(); deferred = null; $("#inst").hidden = true; }; }
}
async function vAccount() {
  if (CFG_FAILED) return view(card("Sign in", '<p class="err">We couldn\'t load the app settings. Check your connection and reload the page.</p>'));
  if (!CFG.accounts_enabled) return view(card("Sign in", `<p>Sign-in is not switched on for this site yet. Weather and guides still work without an account.</p>
    <p class="note">Site owner: add SUPABASE_URL and SUPABASE_ANON_KEY in Vercel (Settings → Environment Variables), then redeploy.</p>`));
  if (SESSION) {
    try { await ensureProfile(); } catch {}
    view(card("Account", `<p>${PROFILE ? esc(PROFILE.full_name) : "Signed in"}</p>${langSelect()}<button id="lsave" class="alt">Save language</button>
      <button id="out" class="danger">Sign out</button>`) + card("Send feedback", `<textarea id="fbm" maxlength="2000" placeholder="What is working? What is wrong?"></textarea><button id="fbs">Send</button><div id="fbo"></div>`));
    $("#lang").value = LANG;
    return;
  }
  view(card("Sign in or create an account", `<label for="ae">Email</label><input id="ae" type="email" autocomplete="email"><label for="ap">Password (8+ characters)</label><input id="ap" type="password" autocomplete="current-password">
    <button id="ain">Sign in</button><button id="aup" class="alt">Create account</button><div id="amsg"></div>`));
  const go = kind => async () => {
    try {
      const d = await gotrue(kind === "up" ? "signup" : "token?grant_type=password", { email: $("#ae").value.trim(), password: $("#ap").value });
      if (keep(d)) { PROFILE = null; location.hash = "#/farms"; route(); }
      else $("#amsg").innerHTML = `<p class="ok">Account created. Check your email to confirm it, then sign in.</p>`;
    } catch (e) { $("#amsg").innerHTML = errBox(e); }
  };
  $("#ain").onclick = go("in"); $("#aup").onclick = go("up");
}
async function vAdmin() {
  if (!SESSION) return view(needLogin());
  try { await ensureProfile(); } catch {}
  if (!ROLES.length) return view(errBox("This area is for approved experts and administrators."));
  try {
    const isAdmin = ROLES.includes("admin");
    const [rv, ac, hidden] = await Promise.all([api("/api/admin/reviews"), isAdmin ? api("/api/admin/assistant-check").catch(() => null) : Promise.resolve(null),
      isAdmin ? api("/api/admin/market-hidden").catch(() => []) : Promise.resolve([])]);
    let html = "";
    if (isAdmin) html += card("Market listings under review", hidden.length ? hidden.map(l => marketCard(l, { admin: true })).join("") : "<p>No hidden listings.</p>");
    if (isAdmin) html += card("AI assistant setup", `<div id="acbox">${assistantCheckHtml(ac)}</div>`);
    html += card("Diagnoses to review", rv.length ? rv.map(d => `<div class="adv"><b>${esc(d.top_label || d.status)}</b> ${d.confidence != null ? Math.round(d.confidence * 100) + "% (" + esc(d.level) + ")" : ""}
      <small>Crop selected: ${esc(d.plant_scans?.crop_hint || "—")} · ${d.expert_reviews?.length ? "reviewed: " + esc(d.expert_reviews.map(r => r.verdict).join(", ")) : "pending"}</small>
      ${d.image_url ? `<img class="preview" alt="Contributed photo" src="${esc(d.image_url)}">` : '<small>No photo contributed.</small>'}
      ${["correct", "incorrect", "alternative", "needs_more_info"].map(v => `<button class="sm alt" data-rv="${esc(d.id)}|${v}">${v.replace(/_/g, " ")}</button>`).join("")}</div>`).join("") : "<p>Nothing to review.</p>");
    if (isAdmin) html += card("Add a verified pesticide record", `<p class="note">Enter only what you can source. Every field marked required is enforced. Expired records stop showing to farmers.</p>
      ${[["product_name","Product name"],["active_ingredient","Active ingredient"],["manufacturer","Manufacturer"],["registration_number","Registration number"],["formulation","Formulation"],["target_crop","Target crop (slug, e.g. tomato)"],["target_pest_or_disease","Target pest/disease"],["source_name","Source name"],["source_url","Source URL"],["pre_harvest_interval_days","Pre-harvest interval (days)"]].map(([k, l]) => `<label>${l}</label><input data-pf="${k}">`).join("")}
      <label>Registration status</label><select data-pf="registration_status"><option>registered</option><option>suspended</option><option>withdrawn</option><option>unknown</option></select>
      <label>Application information</label><textarea data-pf="application_info"></textarea><label>Safety information</label><textarea data-pf="safety_info"></textarea>
      <label>Last verified</label><input type="date" data-pf="last_verified"><label>Record expires</label><input type="date" data-pf="expires_at"><button id="padd">Save record</button><div id="pmsg"></div>`);
    view(html);
    wireMarketActions(document, vAdmin);
    document.querySelectorAll("[data-rv]").forEach(b => b.onclick = async () => { const [id, verdict] = b.dataset.rv.split("|");
      try { await api(`/api/admin/reviews/${id}`, { json: { verdict } }); vAdmin(); } catch (e) { alert(e.message); } });
    $("#padd")?.addEventListener("click", async () => { const body = {}; document.querySelectorAll("[data-pf]").forEach(i => body[i.dataset.pf] = i.value);
      try { await api("/api/admin/pesticides", { json: body }); $("#pmsg").innerHTML = '<p class="ok">Saved.</p>'; } catch (e) { $("#pmsg").innerHTML = errBox(e); } });
    $("#actest")?.addEventListener("click", async () => {
      $("#actest").disabled = true; $("#actest").textContent = "Testing…";
      try { const r = await api("/api/admin/assistant-check?live=true"); $("#acbox").innerHTML = assistantCheckHtml(r); }
      catch (e) { $("#acbox").innerHTML = assistantCheckHtml(ac) + errBox(e); }
    });
  } catch (e) { view(errBox(e)); }
}
function assistantCheckHtml(ac) {
  if (!ac) return '<p class="err">Could not load assistant status. The endpoint may not be deployed yet on this server — check GitHub Actions is green and Vercel shows the latest commit.</p>';
  const rows = [["Active provider", ac.provider], ["Provider recognised", ac.provider_recognised ? "yes" : "no — check ASSISTANT_PROVIDER spelling"],
    ["Model", ac.model], ["Anthropic key", ac.anthropic_key], ["Groq key", ac.groq_key], ["Key that will actually be used", ac.active_key_for_current_provider]];
  const table = `<table>${rows.map(([k, v]) => `<tr><td><b>${esc(k)}</b></td><td>${esc(v)}</td></tr>`).join("")}</table>`;
  let live = `<p class="note">${esc(ac.live_check)}</p>`;
  if (ac.live_check === "success") live = `<p class="ok">Live test succeeded. The assistant replied: "${esc(ac.live_reply)}"</p>`;
  else if (ac.live_check === "failed") live = `<p class="err">Live test failed. Reason: ${esc(ac.live_check_reason)}. Detail: ${esc(ac.live_check_detail)}</p>`;
  let models = "";
  if (Array.isArray(ac.available_models)) models = `<p><b>Models this key can use:</b> ${ac.available_models.length ? esc(ac.available_models.join(", ")) : "none listed"}</p>
    <p class="note">Set ASSISTANT_MODEL in Vercel to exactly one of these names, then redeploy.</p>`;
  else if (ac.available_models_error) models = `<p class="err">Could not list models: ${esc(ac.available_models_error)}</p>`;
  return table + live + models + (ac.live_check?.startsWith("not run") ? '<button id="actest" class="alt">Run a real live test (uses one request against your quota)</button>' : "");
}

/* ---------- Farm Market ---------- */
const MK_CATEGORIES = { produce: "Fresh produce & grains", livestock: "Livestock & poultry", seeds_inputs: "Seeds & farm inputs",
  equipment: "Equipment & tools", processed: "Processed foods", other: "Other" };
const MK_UNITS = ["kg", "bag", "tonne", "crate", "basket", "bunch", "piece", "litre", "tray", "other"];
const MK_SAFETY = "KanoFarm AI only shows listings. It does not handle payments or delivery. Meet in a public place, inspect the goods before you pay, and never send money in advance to someone you have not met. Pesticides and veterinary drugs must not be advertised here. Report anything suspicious.";
const naira = n => "₦" + Number(n).toLocaleString("en-NG");
const mkOpts = (obj, sel = "") => Object.entries(obj).map(([k, l]) => `<option value="${k}" ${k === sel ? "selected" : ""}>${esc(l)}</option>`).join("");
const unitOpts = sel => MK_UNITS.map(u => `<option ${u === sel ? "selected" : ""}>${u}</option>`).join("");

function marketCard(l, opts = {}) {
  const imgs = l.images || [];
  const gallery = imgs.length
    ? `<img class="mk-main" src="${esc(imgs[0])}" alt="${esc(l.title)}" loading="lazy">${imgs.length > 1 ? `<div class="mk-thumbs">${imgs.map((u, i) => `<img data-thumb src="${esc(u)}" alt="Photo ${i + 1}" loading="lazy">`).join("")}</div>` : ""}`
    : '<div class="mk-noimg">No photo</div>';
  const price = l.price_ngn != null ? `${naira(l.price_ngn)}${l.price_unit ? " per " + esc(l.price_unit) : ""}` : "Price not stated";
  const qty = l.quantity != null ? `${esc(l.quantity)} ${esc(l.quantity_unit || "")}` : "";
  const live = l.status === "active";
  const contact = l.contact && live ? `<a class="btn sm" href="${esc(l.contact.call)}">Call ${esc(l.contact.display)}</a><a class="btn sm alt" target="_blank" rel="noopener" href="${esc(l.contact.whatsapp)}">WhatsApp</a>` : "";
  const status = !live ? `<span class="tag draft">${esc(l.status)}</span>` : "";
  const actions = opts.mine ? (l.status === "hidden" ? '<p class="note">This listing is under review.</p>'
      : `${live ? `<button class="sm alt" data-mk="sold" data-id="${esc(l.id)}">Mark as sold</button>` : `<button class="sm alt" data-mk="active" data-id="${esc(l.id)}">Reopen</button>`}
         <button class="sm alt" data-mk="renew" data-id="${esc(l.id)}">Renew 30 days</button><button class="sm danger" data-mk="delete" data-id="${esc(l.id)}">Delete</button>`)
    : opts.admin ? `<p class="note">Reports (${l.report_count}): ${esc((l.report_reasons || []).join(" | ") || "none recorded")}</p>
         <button class="sm" data-mkadmin="restore" data-id="${esc(l.id)}">Restore</button><button class="sm danger" data-mkadmin="remove" data-id="${esc(l.id)}">Remove for good</button>`
    : `<button class="sm alt" data-mk="report" data-id="${esc(l.id)}">Report</button>`;
  return `<div class="mk-card">${gallery}<h3>${esc(l.title)} ${status}</h3>
    <div class="mk-tags"><span class="tag">${esc(l.kind === "wanted" ? "Wanted" : "For sale")}</span><span class="tag">${esc(l.category_label || l.category)}</span></div>
    <div class="mk-price">${price}${l.negotiable ? " · negotiable" : ""}</div>${qty ? `<div>Quantity: ${qty}</div>` : ""}
    ${l.product ? `<div class="mk-meta">Product: ${esc(l.product)}</div>` : ""}
    ${l.description ? `<p>${esc(l.description).replace(/\n/g, "<br>")}</p>` : ""}
    <div class="mk-meta">${esc([l.lga, l.state].filter(Boolean).join(", "))} · Seller: ${esc(l.seller_name)} · Posted ${esc((l.created_at || "").slice(0, 10))}</div>
    ${contact}<div>${actions}</div></div>`;
}
document.addEventListener("click", e => {
  const t = e.target.closest("[data-thumb]");
  if (t) { const main = t.closest(".mk-card")?.querySelector(".mk-main"); if (main) main.src = t.src; }
});
function wireMarketActions(root, refresh) {
  root.querySelectorAll("[data-mk]").forEach(b => b.onclick = async () => {
    const id = b.dataset.id, act = b.dataset.mk;
    try {
      if (act === "report") {
        if (!SESSION) { location.hash = "#/account"; return; }
        const reason = prompt("Why are you reporting this listing? (optional)"); if (reason === null) return;
        const r = await api(`/api/market/${id}/report`, { json: { reason } }); alert(r.message);
      } else if (act === "delete") {
        if (!confirm("Delete this listing and its photos? This cannot be undone.")) return;
        await api(`/api/market/${id}`, { method: "DELETE" }); refresh();
      } else { await api(`/api/market/${id}`, { method: "PATCH", json: { action: act } }); refresh(); }
    } catch (e) { alert(e.message); }
  });
  root.querySelectorAll("[data-mkadmin]").forEach(b => b.onclick = async () => {
    try { await api(`/api/admin/market/${b.dataset.id}/moderate`, { json: { action: b.dataset.mkadmin } }); refresh(); } catch (e) { alert(e.message); }
  });
}

const MK = { state: "", category: "", kind: "", q: "", offset: 0 };
async function vMarket(sub) {
  if (sub === "new") return vMarketNew();
  if (sub === "mine") return vMarketMine();
  const sd = await nigeriaStates();
  view(card("Farm Market", `<p class="note">Browse farm produce and inputs from farmers across Nigeria. No account needed to look.</p>
      <div class="row"><a class="btn sm" href="#/market/new">+ Post a listing</a><a class="btn sm alt" href="#/market/mine">My listings</a></div>
      <label for="mks">State</label><select id="mks"><option value="">All Nigeria</option>${sd.states.map(s => `<option ${s.name === MK.state ? "selected" : ""}>${esc(s.name)}</option>`).join("")}</select>
      <div class="row"><div><label for="mkc">Category</label><select id="mkc"><option value="">All</option>${mkOpts(MK_CATEGORIES, MK.category)}</select></div>
      <div><label for="mkk">Type</label><select id="mkk"><option value="">All</option>${mkOpts({ for_sale: "For sale", wanted: "Wanted" }, MK.kind)}</select></div></div>
      <label for="mkq">Search</label><input id="mkq" maxlength="40" placeholder="e.g. maize, goat, tomato" value="${esc(MK.q)}"><button id="mkgo">Search</button>`)
    + '<div id="mklist"></div><div id="mkmore"></div><div class="mk-safety" id="mknote"></div>' + DISCLAIMER);
  const load = async reset => {
    if (reset) { MK.offset = 0; $("#mklist").innerHTML = ""; }
    const qs = new URLSearchParams({ limit: 12, offset: MK.offset });
    for (const k of ["state", "category", "kind", "q"]) if (MK[k]) qs.set(k, MK[k]);
    $("#mkmore").innerHTML = '<p class="note">Loading…</p>';
    try {
      const d = await api("/api/market?" + qs);
      $("#mknote").textContent = d.notice || MK_SAFETY;
      if (reset && !d.listings.length) $("#mklist").innerHTML = card("", "<p>No listings found. Try another state or search, or post the first one.</p>");
      $("#mklist").insertAdjacentHTML("beforeend", d.listings.map(l => marketCard(l)).join(""));
      wireMarketActions($("#mklist"), () => load(true));
      MK.offset += d.listings.length;
      $("#mkmore").innerHTML = d.has_more ? '<button id="mkmorebtn" class="alt">Show more</button>' : "";
      $("#mkmorebtn")?.addEventListener("click", () => load(false));
    } catch (e) { $("#mkmore").innerHTML = errBox(e.status ? e : "No connection. The market needs the internet."); }
  };
  $("#mkgo").onclick = () => { MK.state = $("#mks").value; MK.category = $("#mkc").value; MK.kind = $("#mkk").value; MK.q = $("#mkq").value.trim(); load(true); };
  load(true);
}

async function vMarketMine() {
  if (!SESSION) return view(needLogin());
  view(card("My listings", '<a class="btn sm" href="#/market/new">+ Post a listing</a><a class="btn sm alt" href="#/market">Back to market</a>') + '<div id="mine"></div>');
  const load = async () => {
    try {
      const d = await api("/api/market/mine");
      $("#mine").innerHTML = d.listings.length ? d.listings.map(l => marketCard(l, { mine: true })).join("") : card("", "<p>You have not posted anything yet.</p>");
      wireMarketActions($("#mine"), load);
    } catch (e) { $("#mine").innerHTML = errBox(e); }
  };
  load();
}

async function compressForMarket(file) {
  const img = await loadImage(file), s = Math.min(1, 960 / Math.max(img.width, img.height));
  const c = document.createElement("canvas"); c.width = Math.round(img.width * s); c.height = Math.round(img.height * s);
  c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
  for (const q of [0.75, 0.6, 0.5, 0.4]) {
    const b64 = c.toDataURL("image/jpeg", q).split(",")[1];
    if (b64.length <= 780000) return b64;      // ≈ 585 KB of image data, under the server's 600 KB limit
  }
  throw new Error("One photo is too large even after shrinking. Please choose a smaller photo.");
}

async function vMarketNew() {
  if (!SESSION) return view(needLogin());
  try { await ensureProfile(); } catch {}
  if (!PROFILE) return vProfileForm();
  const sd = await nigeriaStates();
  view(card("Post a listing", `<label for="nk">I want to</label><select id="nk"><option value="for_sale">Sell something</option><option value="wanted">Buy something (wanted)</option></select>
    <label for="nt">Title</label><input id="nt" maxlength="100" placeholder="e.g. Fresh white maize, 20 bags">
    <label for="nc">Category</label><select id="nc">${mkOpts(MK_CATEGORIES)}</select>
    <label for="np">Product (optional)</label><input id="np" maxlength="60" placeholder="e.g. maize, goat, tomato">
    <label for="nd">Description (optional)</label><textarea id="nd" maxlength="1000" placeholder="Variety, quality, when it is available, delivery…"></textarea>
    <div class="row"><div><label for="nq">Quantity</label><input id="nq" inputmode="decimal"></div><div><label for="nqu">Unit</label><select id="nqu">${unitOpts("bag")}</select></div></div>
    <div class="row"><div><label for="npr">Price (₦)</label><input id="npr" inputmode="decimal"></div><div><label for="npu">Per</label><select id="npu">${unitOpts("bag")}</select></div></div>
    <label class="check"><input id="nn" type="checkbox"> Price is negotiable</label>
    <label for="ns">State</label><select id="ns"><option value="">Select state</option>${sd.states.map(s => `<option ${s.name === PROFILE.state ? "selected" : ""}>${esc(s.name)}</option>`).join("")}</select>
    <label for="nl">LGA (optional)</label><input id="nl" maxlength="80" value="${esc(PROFILE.lga || "")}">
    <label for="nph">Phone number buyers can call</label><input id="nph" type="tel" inputmode="tel" placeholder="0803 123 4567">
    <label for="nsn">Name shown to buyers</label><input id="nsn" maxlength="80" value="${esc(PROFILE.full_name || "")}">
    <label for="nf">Photos (up to 4; at least 1 to sell)</label><input id="nf" type="file" accept="image/*" multiple><div id="nprev" class="mk-previews"></div>
    <div class="mk-safety">${esc(MK_SAFETY)}</div>
    <label class="check"><input id="nco" type="checkbox"> I understand that my phone number, name and photos will be visible to everyone who opens the market.</label>
    <button id="nsub">Post listing</button><div id="nmsg"></div>`));
  let files = [];
  $("#nf").onchange = e => {
    files = Array.from(e.target.files).slice(0, 4);
    $("#nprev").innerHTML = files.map(f => `<img alt="Selected photo" src="${URL.createObjectURL(f)}">`).join("");
  };
  $("#nsub").onclick = async () => {
    const msg = $("#nmsg"), btn = $("#nsub");
    btn.disabled = true; msg.innerHTML = card("", "Preparing photos and posting…");
    try {
      const images = []; for (const f of files) images.push(await compressForMarket(f));
      await api("/api/market", { json: { kind: $("#nk").value, title: $("#nt").value, category: $("#nc").value, product: $("#np").value,
        description: $("#nd").value, quantity: $("#nq").value, quantity_unit: $("#nqu").value, price_ngn: $("#npr").value, price_unit: $("#npu").value,
        negotiable: $("#nn").checked, state: $("#ns").value, lga: $("#nl").value, contact_phone: $("#nph").value, seller_name: $("#nsn").value,
        consent_public_contact: $("#nco").checked, images } });
      location.hash = "#/market/mine";
    } catch (e) { msg.innerHTML = errBox(e.status ? e : e.message || "No connection. Posting needs the internet."); btn.disabled = false; }
  };
}

/* ---------- router ---------- */
let deferred;
window.addEventListener("beforeinstallprompt", e => { e.preventDefault(); deferred = e; });
function route() {
  const [, r = "", arg] = (location.hash || "#/").split("/");
  document.querySelectorAll("#nav a").forEach(a => a.classList.toggle("on", a.dataset.r === (["", "home"].includes(r) ? "home" : ["farm"].includes(r) ? "farms" : ["calendar", "soil", "history", "sources", "account", "admin"].includes(r) ? "more" : r)));
  ({ "": vHome, farms: vFarms, farm: () => vFarm(arg), scan: () => vScan(arg), calendar: vCalendar, soil: vSoil, history: vHistory,
     assistant: vAssistant, sources: vSources, more: vMore, account: vAccount, admin: vAdmin, market: () => vMarket(arg) }[r] || vHome)();
}
document.addEventListener("click", e => { if (e.target.id === "out" && SESSION) signOut(); });
document.addEventListener("click", async e => {
  if (e.target.id === "lsave") { try { LANG = $("#lang").value; store.set("kf_lang", LANG); if (PROFILE) await api("/api/profile", { method: "PUT", json: { ...PROFILE, language: LANG } }); PROFILE = null; alert("Saved."); } catch (x) { alert(x.message); } }
  if (e.target.id === "fbs") { try { await api("/api/feedback", { json: { message: $("#fbm").value } }); $("#fbo").innerHTML = '<p class="ok">Thank you.</p>'; } catch (x) { $("#fbo").innerHTML = errBox(x); } }
});
window.addEventListener("hashchange", route);
(async () => {
  try { CFG = await (await fetch("/api/config")).json(); } catch { CFG_FAILED = true; }
  if (SESSION && Date.now() > SESSION.expires_at - 60000) await refresh();
  if (SESSION) { try { await ensureProfile(); } catch {} }
  authLink(); route();
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
})();
