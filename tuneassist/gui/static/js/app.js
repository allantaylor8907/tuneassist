/* tuneassist GUI — vanilla JS over the local JSON API (docs/V2.md). */
"use strict";

/* ---------- plumbing ---------- */
const BASE = location.pathname.replace(/[^/]*$/, "");   // ".../{token}/"
const $ = (sel, el) => (el || document).querySelector(sel);
const $$ = (sel, el) => Array.from((el || document).querySelectorAll(sel));

/* ---------- state (declared first: theme init below touches it) ---------- */
const S = {
  presets: null,
  vehicles: [],
  current: null,       // selected vehicle record (or ephemeral setup)
  report: null,        // last analyze payload
  charts: {},
  onboarding: false,   // guided first-car -> first-log walkthrough in progress
};

async function api(path, opts) {
  const r = await fetch(BASE + path.replace(/^\//, ""), Object.assign({
    method: opts && opts.body !== undefined ? "POST" : "GET",
    headers: { "Content-Type": "application/json" },
  }, opts, opts && opts.body !== undefined && typeof opts.body !== "string"
      ? { body: JSON.stringify(opts.body) } : {}));
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

function toast(msg, kind) {
  const t = document.createElement("div");
  t.className = "toast " + (kind || "");
  t.textContent = msg;
  $("#toast-wrap").appendChild(t);
  setTimeout(() => t.remove(), kind === "err" ? 9000 : 5500);
}

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

/* heartbeat keeps the server alive while the window is open */
setInterval(() => fetch(BASE + "api/ping", { method: "POST" }).catch(() => {}), 3000);

/* ---------- theme & skill level ---------- */
function setTheme(t) {
  document.body.dataset.theme = t;
  localStorage.setItem("ta-theme", t);
  $$("#theme-toggle").forEach(b => b.textContent = t === "dark" ? "☼" : "☾");
  renderCharts();              // ECharts needs re-style on theme change
}
function setSkill(s) {
  document.body.dataset.skill = s;
  localStorage.setItem("ta-skill", s);
  $("#skill-select").value = s;
}
setTheme(localStorage.getItem("ta-theme") || "dark");
setSkill(localStorage.getItem("ta-skill") || "beginner");
$("#theme-toggle").onclick = () =>
  setTheme(document.body.dataset.theme === "dark" ? "light" : "dark");
$("#theme-toggle-2").onclick = $("#theme-toggle").onclick;
$("#skill-select").onchange = e => setSkill(e.target.value);

/* ---------- view routing ---------- */
function show(view) {
  $$(".view").forEach(v => v.classList.remove("active"));
  $("#view-" + view).classList.add("active");
  $$(".nav-item").forEach(b => b.classList.toggle("active", b.dataset.view === view));
}
$$(".nav-item").forEach(b => b.onclick = () => b.dataset.view && show(b.dataset.view));

/* ---------- garage ---------- */
async function loadGarage() {
  const d = await api("api/garage");
  S.vehicles = d.vehicles || [];
  renderHero(S.vehicles.length === 0);
  const grid = $("#vehicle-grid");
  grid.innerHTML = "";
  const STAGES = 8;
  for (const v of S.vehicles) {
    const idx = (S.presets ? S.presets._stageIndex[v.stage] : 0) || 0;
    const card = document.createElement("div");
    card.className = "card vcard";
    card.innerHTML = `
      <div class="rowx">
        <button title="Rename" data-act="rename">&#x270E;</button>
        <button title="Delete" data-act="del">&#x1F5D1;</button>
      </div>
      <p class="nick">${esc(v.nickname || v.name)}</p>
      <p class="vname">${esc(v.nickname ? v.name : "")}&nbsp;</p>
      <div class="meta">
        <span class="chip accent">${esc(v.platform_label)}</span>
        ${v.make ? `<span class="chip">${esc(v.make.toUpperCase())}</span>` : ""}
        ${v.architecture ? `<span class="chip">${esc(archLabel(v.architecture))}</span>` : ""}
      </div>
      <div class="stagebar"><div style="width:${Math.round(100 * (idx + 1) / STAGES)}%"></div></div>
      <div class="stage-label">${v.stage ? "Journey: " + esc(stageTitle(v.stage)) : "Not analyzed yet"}</div>`;
    card.onclick = (e) => {
      const act = e.target.dataset && e.target.dataset.act;
      if (act === "del") return delVehicle(v);
      if (act === "rename") return renameVehicle(v);
      openVehicle(v);
    };
    grid.appendChild(card);
  }
  const add = document.createElement("div");
  add.className = "card vcard newcard";
  add.textContent = "+  New vehicle";
  add.onclick = () => openSetup(null);
  grid.appendChild(add);

  const quick = document.createElement("div");
  quick.className = "card vcard newcard";
  quick.textContent = "⚡ Quick scan (no save)";
  quick.onclick = () => { S.current = { name: null, ephemeral: true, stoich: 14.7, airflow_mode: "ve_sd" }; enterAnalyze(); };
  grid.appendChild(quick);
}

function stageTitle(key) {
  const j = S.presets && S.presets._journey;
  if (!j) return key;
  const hit = j.find(s => s.key === key);
  return hit ? hit.title : key;
}
function archLabel(key) {
  const a = S.presets && S.presets.architectures.find(x => x.key === key);
  return a ? a.label.replace(/ \(.*\)/, "") : key;
}

async function delVehicle(v) {
  if (!confirm(`Delete '${v.nickname || v.name}' permanently?`)) return;
  await api("api/garage/delete", { body: { name: v.name } });
  loadGarage();
}
async function renameVehicle(v) {
  const nick = prompt(`Nickname for '${v.name}':`, v.nickname || "");
  if (nick === null) return;
  await api("api/garage/rename", { body: { name: v.name, nickname: nick } });
  loadGarage();
}
function openVehicle(v) { S.current = v; enterAnalyze(); }

/* ---------- onboarding: guided first car -> first log ---------- */
function startGuided() { S.onboarding = true; openSetup(null); }
$("#guided-btn").onclick = startGuided;

function renderHero(empty) {
  const hero = $("#garage-hero");
  hero.classList.toggle("hidden", !empty);
  if (!empty) { hero.innerHTML = ""; return; }
  hero.innerHTML = `
    <div class="hero-inner">
      <div class="hero-mark">&#x1F9ED;</div>
      <div class="hero-copy">
        <h2>New here? Let's get your first car set up.</h2>
        <p>Two quick steps: tell me about your car, then I'll show you exactly how to
        grab your first log and turn it into a CSV — what to record, and whether you
        need to change anything (you don't, yet). Then drop the log and I'll take it
        from there.</p>
        <div class="hero-actions">
          <button class="primary" id="hero-start">Start guided setup</button>
          <button class="ghost" id="hero-skip">I know what I'm doing</button>
        </div>
        <p class="hero-foot">Recommendation-only — it reads logs and tells you what to
        change. It never writes to your ECU or tune file.</p>
      </div>
    </div>`;
  $("#hero-start").onclick = startGuided;
  $("#hero-skip").onclick = () => openSetup(null);
}

/* platform-aware "capture your first log" copy. platform 'gm' == HP Tuners. */
function firstLogContent(v) {
  const holley = (v.platform === "holley");
  if (holley) return {
    title: "Capture your first log — Holley EFI",
    intro: "Your Holley replaces the factory ECU and logs for you — we just need to " +
           "pull its datalog off the unit and export it to a CSV.",
    need: ["Your Holley handheld, or a laptop with the Holley EFI software",
           "A USB drive (or the handheld) to copy the log to your computer",
           "A wideband — your Holley already has one built in",
           "A safe place to let it idle and drive"],
    steps: [
      "Make sure datalogging is turned on. The handheld and the software can record to internal memory; some setups log to a USB/SD card.",
      "Start a new datalog, then run the car: let it idle and self-learn for a bit, do some steady cruising, and — only if it's safe and you mean to — a pull or two.",
      "Stop and save the log, then get it onto your computer (USB stick, or pull it through the handheld).",
      "Open that datalog in the Holley EFI software and Export to CSV.",
      "Come back here and drop the CSV anywhere on this window."],
    chSub: "Holley records a full channel set by default, so there's usually nothing " +
           "to add. Just confirm these are in the log:",
    chips: ["RPM", "MAP", "TPS", "Wideband AFR (Air/Fuel)", "Target AFR",
            "Closed-loop comp / Learn", "Coolant temp (CTS)", "Air temp (IAT/MAT)",
            "Knock (if equipped)", "Vehicle speed (if wired)"],
    note: "Holley self-learns fuel, so let it run a while before you pull the log — the " +
          "more varied driving it sees, the better the data. You don't need to change " +
          "anything to capture this baseline.",
  };
  return {
    title: "Capture your first log — HP Tuners",
    intro: "Your car keeps its factory ECU — we'll record what it's doing with VCM " +
           "Scanner, then export that to a CSV.",
    need: ["Your HP Tuners interface (MPVI2 / MPVI3) and the VCM Scanner software",
           "A laptop in the car",
           "A wideband O2 if you have one — it makes the fuel advice far better, but isn't required",
           "A safe place to drive"],
    steps: [
      "Plug the interface into the OBD-II port, open VCM Scanner, and connect to the vehicle.",
      "Add the channels below to your scan (Scanner's channel list / 'Add Channels' panel). Save it as a layout so you only do this once.",
      "Hit record, then drive the baseline as the car sits now: let it idle and warm up, then steady cruising at a few speeds. Only do a wide-open-throttle pull if the car's safe and you mean to — for a first look, idle + cruise is plenty.",
      "Stop recording, then Scan → Export Data and save it as a CSV.",
      "Come back here and drop the CSV anywhere on this window."],
    chSub: "In VCM Scanner, add these. Don't worry if one or two aren't available on " +
           "your ECU — log what you can.",
    chips: ["Engine RPM", "Manifold Pressure (MAP)", "MAF frequency",
            "Wideband AFR (if equipped)", "Commanded AFR / EQ",
            "Short-term fuel trim (B1/B2)", "Long-term fuel trim (B1/B2)",
            "Coolant temp (ECT)", "Intake air temp (IAT)", "Throttle position (TPS)",
            "Knock retard", "Vehicle speed", "Spark advance",
            "Injector duty / pulse width", "Ethanol % (if flex-fuel)"],
    note: "Leave your tune alone for this first log — don't switch off the MAF or closed " +
          "loop yet. Grab the car exactly as it sits; once I read the log I'll tell you " +
          "the precise next move (for VE tuning that's usually: MAF off, dial VE first, " +
          "then the MAF curve).",
  };
}

function enterFirstLog() {
  const c = firstLogContent(S.current || {});
  $("#fl-title").textContent = c.title;
  $("#fl-intro").textContent = c.intro;
  $("#fl-need-list").innerHTML = c.need.map(x => `<li>${esc(x)}</li>`).join("");
  $("#fl-steps-list").innerHTML = c.steps.map(x => `<li>${esc(x)}</li>`).join("");
  $("#fl-channels-sub").textContent = c.chSub;
  $("#fl-channels-chips").innerHTML =
    c.chips.map(x => `<span class="modchip static">${esc(x)}</span>`).join("");
  $("#fl-note").innerHTML = `<strong>One thing:</strong> ${esc(c.note)}`;
  show("firstlog");
}
$("#fl-later").onclick = () => { S.onboarding = false; loadGarage(); show("garage"); };
$("#fl-ready").onclick = () => { S.onboarding = false; enterAnalyze(); };

/* ---------- presets / setup ---------- */
async function loadPresets() {
  const p = await api("api/presets");
  p._stageIndex = {};
  p._journey = p.journey || [];
  p._journey.forEach((s, i) => p._stageIndex[s.key] = i);
  S.presets = p;
  fill($("#s-platform"), Object.entries(p.fitment).map(([k, v]) => [k, v.label]));
  fill($("#s-fuel"), p.fuels.map((x, i) => [String(i), x.label]));
  fill($("#s-airflow"), p.airflows.map((x, i) => [String(i), x.label]));
  fill($("#s-cam"), p.cam_tiers.map(x => [x.tier, x.label]));
  const mods = $("#s-mods");
  mods.innerHTML = "";
  for (const m of p.mods) {
    const c = document.createElement("span");
    c.className = "modchip"; c.textContent = m;
    c.onclick = () => c.classList.toggle("on");
    mods.appendChild(c);
  }
  cascade();          // populate product/make/generation/engine for the default platform
}
function fill(sel, pairs) {
  sel.innerHTML = pairs.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("");
}

/* ---------- fitment cascade: only real combinations are offered ----------
   HP Tuners -> Make -> Generation -> Engine  (factory-ECU world)
   Holley    -> System (Sniper/Terminator/...) -> Make -> Engine (flat)      */
function cascade(changed) {
  const f = S.presets.fitment;
  const plat = $("#s-platform").value || Object.keys(f)[0];
  const tree = f[plat];
  const holley = !!tree.products;

  $("#f-product").classList.toggle("hidden", !holley);
  // Holley self-learns -- the VE/MAF airflow journey is an HP Tuners concept
  $("#f-airflow").classList.toggle("hidden", holley);
  if (holley && (changed === "platform" || !$("#s-product").options.length)) {
    fill($("#s-product"), tree.products.map(x => [x.key, x.label]));
  }

  if (changed === "platform" || !$("#s-make").dataset.plat || $("#s-make").dataset.plat !== plat) {
    fill($("#s-make"), tree.makes.map(m => [m.key, m.label]));
    $("#s-make").dataset.plat = plat;
  }
  const make = $("#s-make").value;
  const makeDef = tree.makes.find(m => m.key === make) || tree.makes[0];

  // generation tier exists only where the factory ECU defines one (HP Tuners)
  const gens = (makeDef && makeDef.generations) || [];
  $("#f-arch").classList.toggle("hidden", !gens.length);
  if (gens.length && (changed !== "arch")) {
    const keep = $("#s-arch").value;
    fill($("#s-arch"), [["auto", "Auto-detect from the log"]]
         .concat(gens.map(g => [g.key, g.label])));
    if ([...$("#s-arch").options].some(o => o.value === keep)) $("#s-arch").value = keep;
  }
  const gen = gens.length ? $("#s-arch").value : null;

  // engines valid for this exact selection (+ always Custom)
  let engines = [];
  if (makeDef && makeDef.engines) engines = makeDef.engines;                  // Holley: flat
  else if (gens.length) {
    if (gen && gen !== "auto") {
      const g = gens.find(x => x.key === gen);
      engines = g ? g.engines : [];
    } else {
      gens.forEach(g => g.engines.forEach(e => { if (!engines.includes(e)) engines.push(e); }));
    }
  }
  const keepE = $("#s-engine").value;
  fill($("#s-engine"), [["custom", "Custom / other"]].concat(engines.map(e => [e, e])));
  if ([...$("#s-engine").options].some(o => o.value === keepE)) $("#s-engine").value = keepE;
}
$("#s-platform").addEventListener("change", () => cascade("platform"));
$("#s-product").addEventListener("change", () => cascade("product"));
$("#s-make").addEventListener("change", () => cascade("make"));
$("#s-arch").addEventListener("change", () => cascade("arch"));

function openSetup(vehicle) {
  $("#setup-title").textContent = vehicle ? `Edit ${vehicle.nickname || vehicle.name}` : "New vehicle";
  $("#s-name").value = vehicle ? vehicle.name : "";
  $("#s-nick").value = (vehicle && vehicle.nickname) || "";
  // prefill the custom VE axes (stored as arrays) when editing; the whole-table
  // paste box is an input convenience, so start it empty and show the saved
  // breakpoints in the manual fields.
  const ax = (vehicle && vehicle.ve_axes) || null;
  $("#s-axis-table").value = "";
  $("#s-axis-rpm").value = ax && ax.rpm ? ax.rpm.join(", ") : "";
  $("#s-axis-map").value = ax && ax.map ? ax.map.join(", ") : "";
  $("#axes-adv").open = !!ax;
  $("#axes-manual").open = !!ax;
  updateAxesStatus();
  $("#setup-onboard").classList.toggle("hidden", !S.onboarding);
  $("#setup-save").textContent = S.onboarding ? "Next: how to grab a log →" : "Save & continue";
  show("setup");
}
$("#setup-cancel").onclick = () => { S.onboarding = false; show("garage"); };
$("#setup-form").onsubmit = async (e) => {
  e.preventDefault();
  const name = $("#s-name").value.trim();
  if (!name) { toast("Give the vehicle a name.", "err"); return; }
  const body = collectSetup();
  body.name = name;
  body.nickname = $("#s-nick").value.trim();
  const d = await api("api/garage/upsert", { body });
  S.current = d.vehicle;
  await loadGarage();
  if (S.onboarding) enterFirstLog(); else enterAnalyze();
};
function collectSetup() {
  const p = S.presets;
  const plat = $("#s-platform").value;
  const holley = !!p.fitment[plat].products;
  // Holley: the product IS the architecture; HP Tuners: the generation is
  // ("auto" -> null so the engine fingerprints the log instead).
  const arch = holley ? $("#s-product").value
             : ($("#s-arch").value === "auto" ? null : $("#s-arch").value);
  return {
    platform: plat,
    make: $("#s-make").value === "other" ? null : $("#s-make").value,
    architecture: arch,
    stoich: p.fuels[Number($("#s-fuel").value)].stoich,
    airflow_mode: p.airflows[Number($("#s-airflow").value)].mode,
    engine_preset: $("#s-engine").value,
    mods: $$("#s-mods .modchip.on").map(c => c.textContent),
    cam_tier: $("#s-cam").value,
    tune_spark: $("#s-spark").checked,
    find_power: $("#s-power").checked,
    ve_axes: veAxesFromForm(),
  };
}

/* the axes the form is offering: prefer a whole-table paste, else manual boxes */
function veAxesFromForm() {
  const table = $("#s-axis-table").value.trim();
  if (table) return { table };
  return { rpm: $("#s-axis-rpm").value, map: $("#s-axis-map").value };
}

/* parse a pasted axis (commas/spaces/tabs/newlines), order PRESERVED + deduped */
function parseAxis(text) {
  const nums = (String(text || "").match(/-?\d+(?:\.\d+)?/g) || []).map(Number);
  return [...new Set(nums)];
}
/* parse a whole "Copy with Axis" table -> {rpm, map} (mirrors core.parse_ve_table) */
function parseVeTable(text) {
  const lines = String(text || "").split(/\r?\n/).map(l => l.trim()).filter(Boolean);
  let rpm = null; const map = [];
  for (const ln of lines) {
    const toks = ln.split(/[\t,;]+|\s+/).filter(Boolean);
    const nums = toks.map(Number).filter(n => !isNaN(n));
    const firstNum = !isNaN(Number(toks[0]));
    const isHeader = toks.some(t => t.toLowerCase() === "rpm") ||
                     (rpm === null && !firstNum && nums.length >= 2);
    if (isHeader && rpm === null) { rpm = nums; continue; }
    if (!nums.length) continue;
    map.push(nums[0]);
  }
  if (!rpm || rpm.length < 2 || map.length < 2) return null;
  return { rpm, map };
}
function updateAxesStatus() {
  const el = $("#axes-status");
  const table = $("#s-axis-table").value.trim();
  let r, m, fromTable = false;
  if (table) {
    const p = parseVeTable(table);
    if (!p) {
      el.textContent = "Couldn't read that as a table — use Copy with Axis (RPM across the top, MAP down the side), or enter the breakpoints manually below.";
      el.className = "axes-status warn"; return;
    }
    r = p.rpm; m = p.map; fromTable = true;
  } else {
    r = parseAxis($("#s-axis-rpm").value); m = parseAxis($("#s-axis-map").value);
  }
  if (!r.length && !m.length) { el.textContent = ""; el.className = "axes-status"; return; }
  if (r.length < 2 || m.length < 2) {
    el.textContent = "Need at least 2 values on each axis.";
    el.className = "axes-status warn"; return;
  }
  el.textContent = `✓ ${fromTable ? "Read from your table: " : ""}${r.length} RPM × ${m.length} MAP ` +
    `= ${r.length * m.length} cells — the grid and copied TSV will match your table.`;
  el.className = "axes-status ok";
}
["#s-axis-table", "#s-axis-rpm", "#s-axis-map"].forEach(s =>
  $(s).addEventListener("input", updateAxesStatus));

/* ---------- analyze ---------- */
function enterAnalyze() {
  $("#nav-analyze").disabled = false;
  show("analyze");
  const v = S.current || {};
  $("#vehicle-bar").innerHTML = `
    <span class="vtitle">${esc(v.nickname || v.name || "Quick scan")}</span>
    <span class="chip accent">${esc(v.platform_label || "auto-detect")}</span>
    ${v.make ? `<span class="chip">${esc(String(v.make).toUpperCase())}</span>` : ""}
    ${v.architecture ? `<span class="chip">${esc(archLabel(v.architecture))}</span>` : ""}
    <span class="chip">stoich ${esc(v.stoich || 14.7)}</span>
    <span class="chip">${esc(v.airflow_mode || "ve_sd")}</span>`;
  renderJourney(v.stage || "");
}

function analyzeOpts() {
  const v = S.current || {};
  return {
    vehicle: v.ephemeral ? null : v.name,
    platform: v.platform || null,
    make: v.make || null, architecture: v.architecture || null,
    stoich: v.stoich || 14.7, airflow_mode: v.airflow_mode || "ve_sd",
    tune_spark: v.tune_spark !== false,        // spark insight on by default in GUI
    find_power: !!v.find_power,
    mods: (v.profile && v.profile.mods) || [],
    ve_axes: v.ve_axes || null,                // bin the grid to this car's table
  };
}

$("#browse-btn").onclick = async () => {
  const d = await api("api/pick-file", { body: {} });
  if (d.path) { $("#path-input").value = d.path; runAnalyze(); }
};
$("#analyze-btn").onclick = () => runAnalyze();
$("#path-input").addEventListener("keydown", e => { if (e.key === "Enter") runAnalyze(); });

/* whole-window drag & drop: any file dropped anywhere analyzes it */
let dragDepth = 0;
function dragHasFiles(e) {
  return e.dataTransfer && Array.from(e.dataTransfer.types || []).includes("Files");
}
document.addEventListener("dragenter", e => {
  if (!dragHasFiles(e)) return;
  e.preventDefault();
  dragDepth++;
  $("#drop-overlay").classList.remove("hidden");
});
document.addEventListener("dragover", e => { if (dragHasFiles(e)) e.preventDefault(); });
document.addEventListener("dragleave", e => {
  if (!dragHasFiles(e)) return;
  dragDepth = Math.max(0, dragDepth - 1);
  if (dragDepth === 0) $("#drop-overlay").classList.add("hidden");
});
document.addEventListener("drop", async e => {
  e.preventDefault();
  dragDepth = 0;
  $("#drop-overlay").classList.add("hidden");
  const f = e.dataTransfer.files && e.dataTransfer.files[0];
  if (!f) return;
  if (!/\.(csv|txt)$/i.test(f.name)) {
    toast("That's not a CSV. Export the log to CSV first (native .hpl/.dl can't be read safely).", "err");
    return;
  }
  if (!S.current) S.current = { name: null, ephemeral: true, stoich: 14.7, airflow_mode: "ve_sd" };
  enterAnalyze();
  busy(true);
  try {
    const r = await fetch(BASE + "api/analyze-upload", {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream",
                 "X-Filename": f.name, "X-Opts": JSON.stringify(analyzeOpts()) },
      body: f });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || r.statusText);
    showReport(d);
  } catch (err) { toast("Analysis failed: " + err.message, "err"); }
  busy(false);
});

async function runAnalyze() {
  const path = $("#path-input").value.trim();
  if (!path) { toast("Pick or paste a log CSV first."); return; }
  busy(true);
  try {
    const body = analyzeOpts(); body.path = path;
    const d = await api("api/analyze", { body });
    showReport(d);
  } catch (err) { toast("Analysis failed: " + err.message, "err"); }
  busy(false);
}
function busy(on) {
  $("#dropzone").classList.toggle("busy", on);
  $("#analyze-btn").innerHTML = on ? '<span class="spinner"></span>Analyzing…' : "Analyze";
}

/* ---------- journey ---------- */
function renderJourney(stageKey, journey) {
  const j = journey || (S.presets && S.presets._journey) || [];
  const wrap = $("#journey");
  wrap.innerHTML = "";
  const cur = j.findIndex(s => s.key === stageKey);
  j.forEach((s, i) => {
    const el = document.createElement("div");
    el.className = "jstep " + (i < cur ? "done" : i === cur ? "now" : i === cur + 1 ? "next" : "");
    el.innerHTML = `<span class="n">${i < cur ? "✓" : (i + 1)}</span>${esc(s.title)}`;
    wrap.appendChild(el);
  });
}

/* ---------- report ---------- */
const SEV = { critical: ["crit", "CRITICAL"], warning: ["warn", "WARNING"],
              opportunity: ["opp", "OPPORTUNITY"], info: ["info", "INFO"] };

function showReport(d) {
  S.report = d;
  if (d.journey) { S.presets._journey = d.journey; d.journey.forEach((s, i) => S.presets._stageIndex[s.key] = i); }
  renderJourney(d.stage, d.journey);
  const rep = $("#report");
  rep.classList.remove("hidden");
  rep.innerHTML = buildVerdict(d) + buildFindings(d) + buildChartShells(d);
  wireReport(d);
  renderCharts();
  rep.scrollIntoView({ behavior: "smooth", block: "start" });
}

function buildVerdict(d) {
  const tri = d.triage || {};
  const sum = d.summary || {};
  const rx = d.prescription || {};
  const ok = tri.can_correct;
  const stateBadge = ok ? `<span class="badge opp">✓ ${esc(tri.state)}</span>`
                        : `<span class="badge crit">⚠ ${esc(tri.state)}</span>`;
  const stats = ok && d.correction ? `
    <div class="statrow">
      <div class="stat"><div class="k">Coverage</div><div class="v">${sum.coverage_pct || 0}<small>%</small></div></div>
      <div class="stat"><div class="k">Median change</div><div class="v">${fmtPct(sum.median_pct)}</div></div>
      <div class="stat"><div class="k">Worst cell</div><div class="v">${fmtPct(sum.max_abs_pct)}</div></div>
      <div class="stat"><div class="k">Wideband</div><div class="v">${sum.has_wideband ? "yes" : "no"}</div></div>
    </div>` : "";
  const firstAction = (rx.actions && rx.actions[0]) || tri.detail || "";
  return `
  <div class="verdict">
    <div class="vh-row">
      ${stateBadge}
      <span class="chip accent">${esc(d.platform_label || "")}</span>
      ${d.make ? `<span class="chip">${esc(String(d.make).toUpperCase())}</span>` : ""}
      ${d.architecture ? `<span class="chip">${esc(archLabel(d.architecture))}</span>` : ""}
      ${d.log_name ? `<span class="chip">${esc(d.log_name)}</span>` : ""}
    </div>
    <h2>${esc(rx.title || "Analysis")}</h2>
    <p class="lead">${esc(firstAction)}</p>
    <details class="why"><summary>Why this is the next move</summary>
      <div class="why-body">${esc(rx.rationale || tri.detail || "")}
      ${rx.drive ? `<br><br><strong>The drive to do next:</strong> ${esc(rx.drive)}` : ""}</div>
    </details>
    ${stats}
    ${(d.notes || []).map(n => `<p class="sub" style="margin:10px 0 0">ⓘ ${esc(n)}</p>`).join("")}
  </div>`;
}

function buildFindings(d) {
  const fs = d.findings || [];
  if (!fs.length) return "";
  const cards = fs.map((f, i) => {
    const [cls, label] = SEV[f.severity] || SEV.info;
    const open = (f.severity === "critical" || f.severity === "warning" || i === 0) ? "open" : "";
    return `
    <details class="finding ${cls}" ${open}>
      <summary><span class="badge ${cls}">${label}</span> ${esc(f.title)}
        <span class="f-detail">${esc(truncate(f.detail, 90))}</span></summary>
      <div class="f-body">
        <div class="fb-block"><div class="fb-k">What I see</div><div>${esc(f.detail)}</div></div>
        ${f.causes && f.causes.length ? `<div class="fb-block"><div class="fb-k">Likely</div>
          <ul>${f.causes.map(c => `<li>${esc(c)}</li>`).join("")}</ul></div>` : ""}
        ${f.corrections && f.corrections.length ? `<div class="fb-block"><div class="fb-k">Do this</div>
          <ul>${f.corrections.map(c => `<li>${esc(c)}</li>`).join("")}</ul></div>` : ""}
      </div>
    </details>`;
  }).join("");
  return `<div class="findings">${cards}</div>`;
}

function buildChartShells(d) {
  let html = "";
  if (d.correction && d.correction.cells && d.correction.cells.length) {
    const axN = d.ve_axes ? `${d.ve_axes.rpm.length}×${d.ve_axes.map.length}` : null;
    const sub = axN
      ? `% change per cell — matched to your VE table (${axN})`
      : `% change per RPM × MAP cell — hover any cell`;
    html += `
    <div class="chart-card">
      <div class="ch-head">
        <h3>VE / fuel correction</h3><span class="ch-sub">${sub}</span>
        <div class="ch-actions">
          ${d.tsv && d.tsv.correction ? `<button id="copy-grid-tsv">Copy for VCM/Holley (TSV)</button>` : ""}
        </div>
      </div>
      <div id="ve-heatmap" class="chart-box"></div>
      <details class="why expander"><summary>What am I looking at?</summary>
        <div class="why-body">Each cell is how far the fuel model is off at that RPM (rows)
        and engine load (columns, manifold pressure in kPa). <strong>Warm cells = the engine ran lean
        there; raise VE / add fuel.</strong> <strong>Cool cells = rich; pull fuel.</strong> Faint cells
        didn't get enough samples to trust — drive more in that zone. Paste the TSV into the matching
        table with Paste Special → Multiply by Percentage; zeros leave a cell unchanged.</div>
      </details>
    </div>`;
  }
  if (d.maf && d.maf.cells && d.maf.cells.length) {
    html += `
    <div class="chart-card">
      <div class="ch-head"><h3>MAF curve correction</h3>
        <span class="ch-sub">one row, frequency (Hz) across — matches 'Airflow vs Frequency'</span>
        <div class="ch-actions">
          ${d.tsv && d.tsv.maf ? `<button id="copy-maf-tsv">Copy MAF row (TSV)</button>` : ""}
        </div></div>
      <div id="maf-chart" class="chart-box"></div>
      <details class="why expander"><summary>What am I looking at?</summary>
        <div class="why-body">The MAF table is a different table from VE: a single row indexed by
        sensor frequency. Bars above zero = the MAF under-reports air there (add); below = over-reports
        (remove). Paste into the MAF calibration — <strong>not</strong> the VE table.</div>
      </details>
    </div>`;
  }
  if (d.timeseries && d.timeseries.t && d.timeseries.t.length) {
    const bands = (d.timeseries.bands || []);
    const hasLean = bands.some(b => b.type === "lean");
    const hasRich = bands.some(b => b.type === "rich");
    const legend = (hasLean || hasRich) ? `
      <div class="band-legend">
        ${hasLean ? `<span class="bl lean"><i></i>lean under load</span>` : ""}
        ${hasRich ? `<span class="bl rich"><i></i>too rich</span>` : ""}
      </div>` : "";
    html += `
    <div class="chart-card">
      <div class="ch-head"><h3>Log timeline</h3>
        <span class="ch-sub">when things happened — drag to zoom, hover for point-in-time readouts</span>
        ${legend}</div>
      <div id="timeline-chart" class="chart-box"></div>
      <details class="why expander"><summary>What am I looking at?</summary>
        <div class="why-body">The whole log over time: RPM, manifold pressure, and air-fuel ratio
        (with the commanded target as a dashed line). Red ticks flag <strong>knock events</strong> —
        zoom in on one to see exactly what RPM/load/AFR the engine was at when it knocked.
        <strong>Red shading = the engine ran dangerously lean while under load</strong> (the one to
        chase first — lean + load is how parts break); <strong>blue shading = it ran much richer than
        commanded.</strong> Shading compares actual AFR to the commanded target, so it scales with your
        fuel.</div>
      </details>
    </div>`;
  }
  return html;
}

function truncate(s, n) { s = String(s || ""); return s.length > n ? s.slice(0, n - 1) + "…" : s; }
function fmtPct(v) { if (v == null) return "—"; const s = v > 0 ? "+" : ""; return `${s}${v}<small>%</small>`; }

function wireReport(d) {
  const cg = $("#copy-grid-tsv");
  if (cg) cg.onclick = () => copyText(d.tsv.correction, d.ve_axes
    ? "Copied — laid out to match your table. In VCM Editor: click the top-left VE cell → Edit → Paste Special → Multiply by Percentage. (Holley: paste into Base Fuel.)"
    : "Copied. In VCM Editor: select the matching VE cells → Edit → Paste Special → Multiply by Percentage. (Holley: paste into Base Fuel.)");
  const cm = $("#copy-maf-tsv");
  if (cm) cm.onclick = () => copyText(d.tsv.maf,
    "Copied the MAF row. Paste into the MAF calibration (Multiply by Percentage) — not the VE table.");
}
async function copyText(text, msg) {
  try { await navigator.clipboard.writeText(text); toast(msg, "ok"); }
  catch (e) { toast("Clipboard blocked — select & copy from the grid instead.", "err"); }
}

/* ---------- charts (ECharts) ---------- */
function chartColors() {
  const cs = getComputedStyle(document.body);
  return {
    text: cs.getPropertyValue("--text").trim(),
    text3: cs.getPropertyValue("--text-3").trim(),
    grid: cs.getPropertyValue("--chart-grid").trim(),
    add: cs.getPropertyValue("--add").trim(),
    pull: cs.getPropertyValue("--pull").trim(),
    crit: cs.getPropertyValue("--crit").trim(),
    accent: cs.getPropertyValue("--accent").trim(),
    bg2: cs.getPropertyValue("--bg-2").trim(),
  };
}
function disposeCharts() {
  Object.values(S.charts).forEach(c => c && c.dispose());
  S.charts = {};
}
function renderCharts() {
  if (!S.report) return;
  disposeCharts();
  const d = S.report;
  if ($("#ve-heatmap") && d.correction) S.charts.ve = veHeatmap($("#ve-heatmap"), d);
  if ($("#maf-chart") && d.maf) S.charts.maf = mafChart($("#maf-chart"), d.maf);
  if ($("#timeline-chart") && d.timeseries) S.charts.tl = timeline($("#timeline-chart"), d.timeseries);
}
window.addEventListener("resize", () => Object.values(S.charts).forEach(c => c && c.resize()));

function axisSort(labels) {     // "400-800" -> numeric sort by left edge
  return labels.sort((a, b) => parseFloat(a) - parseFloat(b));
}

function veHeatmap(el, d) {
  const C = chartColors();
  const cells = d.correction.cells;
  const rpmL = axisSort([...new Set(cells.map(c => c.rpm))]);
  const mapL = axisSort([...new Set(cells.map(c => c.map))]);
  const data = cells.map(c => [mapL.indexOf(c.map), rpmL.indexOf(c.rpm), c.value, c.samples || 0]);
  const lim = Math.max(5, ...cells.map(c => Math.abs(c.value)));
  // hide in-cell labels when cells get cramped (narrow window / many columns)
  const cellW = (el.clientWidth - 182) / Math.max(1, mapL.length);
  const showLabels = cellW >= 46;
  const ch = echarts.init(el, null, { renderer: "canvas" });
  ch.setOption({
    animationDuration: 350,
    grid: { left: 86, right: 96, top: 18, bottom: 44 },
    xAxis: { type: "category", data: mapL, name: "MAP (kPa)", nameLocation: "middle",
             nameGap: 30, axisLabel: { color: C.text3 }, nameTextStyle: { color: C.text3 },
             axisLine: { lineStyle: { color: C.grid } }, splitArea: { show: true } },
    yAxis: { type: "category", data: rpmL, name: "RPM", axisLabel: { color: C.text3 },
             nameTextStyle: { color: C.text3 }, axisLine: { lineStyle: { color: C.grid } },
             splitArea: { show: true } },
    visualMap: { min: -lim, max: lim, calculable: true, orient: "vertical",
      right: 6, top: "middle", text: ["add fuel", "pull fuel"],
      // color by the correction VALUE (dim 2); without this ECharts defaults to
      // the last data dim (samples), so negative cells wrongly showed as orange.
      dimension: 2,
      textStyle: { color: C.text3, fontSize: 11 },
      inRange: { color: [C.pull, C.bg2, C.add] } },
    tooltip: { confine: true, formatter: p => {
      const v = p.value[2], n = p.value[3];
      const verb = v > 1 ? "ran LEAN here — raise VE / add fuel"
                 : v < -1 ? "ran RICH here — pull fuel" : "on target — leave it";
      return `<b>${rpmL[p.value[1]]} RPM × ${mapL[p.value[0]]} kPa</b><br>` +
             `${v > 0 ? "+" : ""}${v}% · ${n} samples<br><span style="opacity:.75">${verb}</span>`;
    } },
    series: [{ type: "heatmap", data, label: { show: showLabels, fontSize: 10,
      color: C.text, formatter: p => (p.value[2] > 0 ? "+" : "") + p.value[2].toFixed(1) },
      emphasis: { itemStyle: { shadowBlur: 8, shadowColor: "rgba(0,0,0,.4)" } } }],
  });
  return ch;
}

function mafChart(el, maf) {
  const C = chartColors();
  const ch = echarts.init(el);
  ch.setOption({
    animationDuration: 350,
    grid: { left: 56, right: 18, top: 14, bottom: 40 },
    xAxis: { type: "category", data: maf.cells.map(c => c.hz.split("-")[0]),
      name: "MAF Hz", nameLocation: "middle", nameGap: 26,
      axisLabel: { color: C.text3 }, nameTextStyle: { color: C.text3 },
      axisLine: { lineStyle: { color: C.grid } } },
    yAxis: { type: "value", axisLabel: { color: C.text3, formatter: "{value}%" },
      splitLine: { lineStyle: { color: C.grid } } },
    tooltip: { formatter: p => `<b>${p.name} Hz</b><br>${p.value > 0 ? "+" : ""}${p.value}% · ${maf.cells[p.dataIndex].samples || 0} samples` },
    series: [{ type: "bar", data: maf.cells.map(c => c.pct),
      itemStyle: { color: p => p.value >= 0 ? C.add : C.pull, borderRadius: 3 } }],
  });
  return ch;
}

function timeline(el, ts) {
  const C = chartColors();
  const ch = echarts.init(el);
  const t = ts.t;
  const tr = ts.traces;
  const series = [];
  const legend = [];
  function add(name, key, yIdx, opts) {
    if (!tr[key]) return;
    legend.push(name);
    series.push(Object.assign({ name, type: "line", yAxisIndex: yIdx, showSymbol: false,
      sampling: "lttb", lineStyle: { width: 1.4 },
      data: tr[key].map((v, i) => [t[i], v]) }, opts || {}));
  }
  add("RPM", "rpm", 0, { lineStyle: { width: 1.6, color: C.accent }, itemStyle: { color: C.accent } });
  add("MAP", "map", 1);
  add("AFR", "afr_actual", 2);
  add("AFR target", "afr_cmd", 2, { lineStyle: { type: "dashed", width: 1 } });
  add("Knock", "knock", 1, { lineStyle: { color: C.crit }, itemStyle: { color: C.crit }, areaStyle: { opacity: .25, color: C.crit } });
  const marks = (ts.events || []).map(e => ({ xAxis: e.t }));
  if (marks.length && series.length) {
    series[0].markLine = { symbol: "none", silent: true, label: { show: false },
      lineStyle: { color: C.crit, width: 1, opacity: .6 }, data: marks };
  }
  // shade dangerously-lean (under load) and overly-rich stretches
  const bands = (ts.bands || []).map(b => ([
    { xAxis: b.from, itemStyle: {
        color: b.type === "lean" ? C.crit : C.pull,
        opacity: b.type === "lean" ? 0.16 : 0.13 } },
    { xAxis: b.to } ]));
  if (bands.length && series.length) {
    series[0].markArea = { silent: true, data: bands };
  }
  ch.setOption({
    animationDuration: 350,
    legend: { textStyle: { color: C.text3 }, top: 0 },
    grid: { left: 56, right: 56, top: 52, bottom: 58 },
    xAxis: { type: "value", name: "s", min: "dataMin", max: "dataMax",
      axisLabel: { color: C.text3 }, splitLine: { lineStyle: { color: C.grid } } },
    yAxis: [
      { type: "value", axisLabel: { color: C.text3 }, splitLine: { show: false } },
      { type: "value", show: false },
      { type: "value", position: "right", axisLabel: { color: C.text3, formatter: "{value}" },
        splitLine: { show: false }, min: v => Math.floor(v.min - 1), max: v => Math.ceil(v.max + 1) },
    ],
    dataZoom: [{ type: "inside" }, { type: "slider", height: 22, bottom: 8,
      borderColor: C.grid, textStyle: { color: C.text3 } }],
    tooltip: { trigger: "axis", confine: true,
      valueFormatter: v => v == null ? "—" : (+v).toFixed(1) },
    series,
  });
  return ch;
}

/* ---------- settings ---------- */
$("#check-update").onclick = async () => {
  $("#update-status").textContent = "Checking…";
  try {
    const d = await api("api/update/check", { body: {} });
    if (d.update) {
      $("#update-status").textContent =
        `Update available: v${d.update.current} → v${d.update.latest}`;
      $("#install-update").classList.remove("hidden");
    } else {
      $("#update-status").textContent = d.message;
    }
  } catch (e) { $("#update-status").textContent = "Check failed: " + e.message; }
};
$("#install-update").onclick = async () => {
  $("#update-status").textContent = "Downloading…";
  try {
    const d = await api("api/update/install", { body: {} });
    $("#update-status").textContent = d.message;
    if (d.restarting) toast("Installing — the app will close and reopen on the new version.", "ok");
  } catch (e) { $("#update-status").textContent = "Update failed: " + e.message; }
};

/* ---------- boot ---------- */
(async function boot() {
  try {
    const v = await api("api/version");
    $("#version").textContent = "v" + v.version;
    $("#about-version").textContent = "v" + v.version;
  } catch (e) { /* dev without server */ }
  try {
    await loadPresets();
    await loadGarage();
  } catch (e) { toast("Couldn't reach the local engine: " + e.message, "err"); }
  // passive once-a-day update notice (the default UI should surface new versions)
  try {
    const u = await api("api/update/passive", { body: {} });
    if (u.update) toast(`Update available: v${u.update.current} → v${u.update.latest} — install it in Settings.`, "ok");
  } catch (e) { /* offline is fine */ }
})();
