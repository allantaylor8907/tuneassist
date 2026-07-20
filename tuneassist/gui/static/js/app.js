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
  // expert-only cards (timeline, MAF) get shown/hidden by CSS -> re-init the
  // charts so the now-visible ones size correctly (ECharts can't size hidden divs)
  if (S.report) renderCharts();
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
    const axN = v.ve_axes && v.ve_axes.rpm && v.ve_axes.map
      ? `${v.ve_axes.rpm.length}×${v.ve_axes.map.length}` : null;
    card.innerHTML = `
      <div class="rowx">
        <button title="Edit settings" data-act="edit">&#x270E;</button>
        <button title="Delete" data-act="del">&#x1F5D1;</button>
      </div>
      <p class="nick">${esc(v.nickname || v.name)}</p>
      <p class="vname">${esc(v.nickname ? v.name : "")}&nbsp;</p>
      <div class="meta">
        <span class="chip accent">${esc(v.platform_label)}</span>
        ${v.make ? `<span class="chip">${esc(v.make.toUpperCase())}</span>` : ""}
        ${v.architecture ? `<span class="chip">${esc(archLabel(v.architecture))}</span>` : ""}
        ${axN ? `<span class="chip" title="Your VE table axes are set">VE ${axN}</span>` : ""}
      </div>
      <div class="stagebar"><div style="width:${Math.round(100 * (idx + 1) / STAGES)}%"></div></div>
      <div class="stage-label">${v.stage ? "Journey: " + esc(stageTitle(v.stage)) : "Not analyzed yet"}</div>`;
    card.onclick = (e) => {
      const act = e.target.dataset && e.target.dataset.act;
      if (act === "del") return delVehicle(v);
      if (act === "edit") return openSetup(v);
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

  // tune-table group labels follow the platform/generation the user actually
  // sees in their software (Gen 3 Main VE vs Gen 4+ VVE; Holley Base Fuel /
  // Timing; MAF is an HP Tuners concept only)
  const arch = gens.length ? $("#s-arch").value : "";
  $("#tbl-ve-label").textContent = holley ? "Base Fuel Table"
    : arch.includes("gen3") ? "Main VE table"
    : (arch && arch !== "auto") ? "VVE (Virtual VE) table"
    : "VE table (Main VE / VVE)";
  $("#tbl-spark-label").textContent = holley ? "Timing Table" : "High Octane Spark table";
  $("#f-tbl-maf").classList.toggle("hidden", holley);
}
$("#s-platform").addEventListener("change", () => cascade("platform"));
$("#s-product").addEventListener("change", () => cascade("product"));
$("#s-make").addEventListener("change", () => cascade("make"));
$("#s-arch").addEventListener("change", () => cascade("arch"));

function setSel(sel, val) {        // set a <select> only if that option exists
  if (val == null) return false;
  const el = $(sel);
  if ([...el.options].some(o => o.value === String(val))) { el.value = String(val); return true; }
  return false;
}

function openSetup(vehicle) {
  const p = S.presets;
  $("#setup-title").textContent = vehicle ? `Edit ${vehicle.nickname || vehicle.name}` : "New vehicle";
  $("#s-name").value = vehicle ? vehicle.name : "";
  $("#s-nick").value = (vehicle && vehicle.nickname) || "";

  // platform -> cascade fills product / make / generation / engine + show/hide
  setSel("#s-platform", (vehicle && vehicle.platform) || "gm");
  cascade("platform");
  const holley = !!p.fitment[$("#s-platform").value].products;

  if (vehicle) {                  // editing: restore the whole setup
    if (holley) setSel("#s-product", vehicle.architecture);
    setSel("#s-make", vehicle.make || "");
    cascade("make");
    if (!holley) { setSel("#s-arch", vehicle.architecture || "auto"); cascade("arch"); }
    if (vehicle.profile && vehicle.profile.engine) setSel("#s-engine", vehicle.profile.engine);
    const fi = p.fuels.findIndex(f => Math.abs(f.stoich - (vehicle.stoich || 14.7)) < 0.05);
    if (fi >= 0) $("#s-fuel").value = String(fi);
    const ai = p.airflows.findIndex(a => a.mode === (vehicle.airflow_mode || "ve_sd"));
    if (ai >= 0) $("#s-airflow").value = String(ai);
    setSel("#s-cam", vehicle.cam_tier || "stock");
    $("#s-spark").checked = !!vehicle.tune_spark;
    $("#s-power").checked = !!vehicle.find_power;
    const mods = (vehicle.profile && vehicle.profile.mods) || [];
    $$("#s-mods .modchip").forEach(c => c.classList.toggle("on", mods.includes(c.textContent)));
  } else {                        // new: clear the optional bits to defaults
    $("#s-fuel").selectedIndex = 0; $("#s-airflow").selectedIndex = 0;
    $("#s-cam").selectedIndex = 0; $("#s-spark").checked = false; $("#s-power").checked = false;
    $$("#s-mods .modchip").forEach(c => c.classList.remove("on"));
  }

  // custom VE axes (stored as arrays) -> manual fields; the table paste boxes
  // are input conveniences, so they always start empty ("paste to replace").
  const ax = (vehicle && vehicle.ve_axes) || null;
  $("#s-axis-table").value = "";
  $("#s-axis-rpm").value = ax && ax.rpm ? ax.rpm.join(", ") : "";
  $("#s-axis-map").value = ax && ax.map ? ax.map.join(", ") : "";
  $("#axes-manual").open = !!ax;
  const sax = (vehicle && vehicle.spark_axes) || null;
  $("#s-spark-axis-table").value = "";
  $("#s-spark-axis-rpm").value = sax && sax.rpm ? sax.rpm.join(", ") : "";
  $("#s-spark-axis-map").value = sax && sax.map ? sax.map.join(", ") : "";
  $("#spark-axes-manual").open = !!sax;
  $("#s-maf-table").value = "";

  // what's already on file (values captured, when, prior versions)
  const tables = (vehicle && vehicle.tables) || {};
  const histN = (vehicle && vehicle.table_history_count) || 0;
  function onfile(el, t, kind) {
    if (!t) { el.textContent = ""; el.classList.remove("has"); return; }
    const dims = kind === "row" ? `${(t.hz || []).length} points`
      : `${(t.rpm || []).length}×${(t.map || []).length}`;
    const vals = (kind === "row" ? t.values : t.values) ? "with values" : "axes only";
    const when = t.pasted ? ` · pasted ${String(t.pasted).slice(0, 10)}` : "";
    el.textContent = `On file: ${dims} ${vals}${when}` +
      (histN ? ` · ${histN} older version${histN > 1 ? "s" : ""} kept` : "");
    el.classList.add("has");
  }
  onfile($("#tbl-ve-onfile"), tables.ve, "grid");
  onfile($("#tbl-spark-onfile"), tables.spark, "grid");
  onfile($("#tbl-maf-onfile"), tables.maf, "row");

  // "what changed since my last paste" -- server-computed diff vs the newest
  // archived version of each table (garage table_history).
  const diffs = (vehicle && vehicle.table_diffs) || {};
  const sign = v => (v > 0 ? "+" : "") + v;
  function diffline(el, dif, kind) {
    if (!el) return;
    if (!dif || !dif.changed) { el.innerHTML = ""; return; }
    const at = dif.at ? (kind === "row" ? `${dif.at.hz} Hz`
      : `${dif.at.rpm} rpm / ${dif.at.map} kPa`) : "";
    const when = dif.prev_pasted ? ` (previous paste ${String(dif.prev_pasted).slice(0, 10)})` : "";
    const rows = (dif.cells || []).slice(0, 12).map(c =>
      `<tr><td>${kind === "row" ? c.hz + " Hz" : c.rpm + " rpm · " + c.map + " kPa"}</td>` +
      `<td>${c.before} → ${c.after}</td>` +
      `<td class="${c.delta > 0 ? "d-up" : "d-down"}">${sign(c.delta)}</td></tr>`).join("");
    const more = dif.changed > 12
      ? `<tr><td colspan="3" class="tbl-diff-more">… and ${dif.changed - 12} more cell${dif.changed - 12 > 1 ? "s" : ""}</td></tr>` : "";
    el.innerHTML = `<details class="tbl-diff-x">
      <summary>Since your last paste: <strong>${dif.changed}</strong> of ${dif.compared} cells changed
        · biggest <strong>${sign(dif.max_delta)}</strong>${at ? " at " + at : ""}${when}</summary>
      <table class="tbl-diff-t"><thead><tr><th>${kind === "row" ? "Frequency" : "Cell"}</th>
        <th>was → now</th><th>Δ</th></tr></thead><tbody>${rows}${more}</tbody></table>
    </details>`;
  }
  diffline($("#tbl-ve-diff"), diffs.ve, "grid");
  diffline($("#tbl-spark-diff"), diffs.spark, "grid");
  diffline($("#tbl-maf-diff"), diffs.maf, "row");

  $("#axes-adv").open = !!(ax || sax || tables.ve || tables.spark || tables.maf);
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
    ve_axes: axesFromForm("#s-axis-table", "#s-axis-rpm", "#s-axis-map"),
    spark_axes: axesFromForm("#s-spark-axis-table", "#s-spark-axis-rpm", "#s-spark-axis-map"),
    // full-table pastes (values captured); omitted keys keep what's on file
    tables: (() => {
      const t = {};
      const ve = $("#s-axis-table").value.trim();
      const sp = $("#s-spark-axis-table").value.trim();
      const mf = $("#s-maf-table").value.trim();
      if (ve) t.ve = { table: ve };
      if (sp) t.spark = { table: sp };
      if (mf) t.maf = { table: mf };
      return Object.keys(t).length ? t : null;
    })(),
  };
}

/* the axes a group is offering: prefer a whole-table paste, else manual boxes */
function axesFromForm(tableSel, rpmSel, mapSel) {
  const table = $(tableSel).value.trim();
  if (table) return { table };
  return { rpm: $(rpmSel).value, map: $(mapSel).value };
}

/* parse a pasted axis (commas/spaces/tabs/newlines), order PRESERVED + deduped */
function parseAxis(text) {
  const nums = (String(text || "").match(/-?\d+(?:\.\d+)?/g) || []).map(Number);
  return [...new Set(nums)];
}
/* parse a whole "Copy with Axis" table -> {rpm, map, hasValues}
   (mirrors core.parse_ve_table, which also captures the cell values) */
function parseVeTable(text) {
  const lines = String(text || "").split(/\r?\n/).map(l => l.trim()).filter(Boolean);
  let rpm = null; const map = []; const rowLens = [];
  for (const ln of lines) {
    const toks = ln.split(/[\t,;]+|\s+/).filter(Boolean);
    const nums = toks.map(Number).filter(n => !isNaN(n));
    const firstNum = !isNaN(Number(toks[0]));
    const isHeader = toks.some(t => t.toLowerCase() === "rpm") ||
                     (rpm === null && !firstNum && nums.length >= 2);
    if (isHeader && rpm === null) { rpm = nums; continue; }
    if (!nums.length) continue;
    map.push(nums[0]);
    rowLens.push(nums.length - 1);
  }
  if (!rpm || rpm.length < 2 || map.length < 2) return null;
  const hasValues = rowLens.length > 0 && rowLens.every(n => n === rpm.length);
  return { rpm, map, hasValues };
}
function updateAxesGroup(tableSel, rpmSel, mapSel, statusSel) {
  const el = $(statusSel);
  const table = $(tableSel).value.trim();
  let r, m, fromTable = false;
  if (table) {
    const p = parseVeTable(table);
    if (!p) {
      el.textContent = "Couldn't read that as a table — use Copy with Axis (RPM across the top, " +
        "values down the side), or enter the breakpoints manually below.";
      el.className = "axes-status warn"; return;
    }
    r = p.rpm; m = p.map; fromTable = true;
  } else {
    r = parseAxis($(rpmSel).value); m = parseAxis($(mapSel).value);
  }
  if (!r.length && !m.length) { el.textContent = ""; el.className = "axes-status"; return; }
  if (r.length < 2 || m.length < 2) {
    el.textContent = "Need at least 2 values on each axis.";
    el.className = "axes-status warn"; return;
  }
  const p2 = fromTable ? parseVeTable(table) : null;
  el.textContent = `✓ ${fromTable ? "Read from your table: " : ""}${r.length} RPM × ${m.length} MAP ` +
    (p2 && p2.hasValues
      ? `with all ${r.length * m.length} cell values — grids match your table, and recommendations can go absolute.`
      : `= ${r.length * m.length} cells — the grid and copied TSV will match your table.`);
  el.className = "axes-status ok";
}
function updateMafStatus() {
  const el = $("#maf-axes-status");
  const text = $("#s-maf-table").value.trim();
  if (!text) { el.textContent = ""; el.className = "axes-status"; return; }
  // mirror core.parse_maf_table: column pairs, or a Hz row over a value row
  const rows = text.split(/\r?\n/).map(l =>
    (l.match(/-?\d+(?:\.\d+)?/g) || []).map(Number)).filter(r => r.length);
  let n = 0;
  if (rows.length >= 4 && rows.every(r => r.length === 2)) n = rows.length;
  else if (rows.length === 2 && rows[0].length === rows[1].length && rows[0].length >= 4)
    n = rows[0].length;
  if (n) {
    el.textContent = `✓ Read ${n} MAF points with values — the MAF row will match your calibration.`;
    el.className = "axes-status ok";
  } else {
    el.textContent = "Couldn't read that as a MAF calibration — expect Hz + value pairs per line, or a Hz row over a value row.";
    el.className = "axes-status warn";
  }
}
function updateAxesStatus() {
  updateAxesGroup("#s-axis-table", "#s-axis-rpm", "#s-axis-map", "#axes-status");
  updateAxesGroup("#s-spark-axis-table", "#s-spark-axis-rpm", "#s-spark-axis-map", "#spark-axes-status");
  updateMafStatus();
}
["#s-axis-table", "#s-axis-rpm", "#s-axis-map",
 "#s-spark-axis-table", "#s-spark-axis-rpm", "#s-spark-axis-map",
 "#s-maf-table"].forEach(s =>
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
  // prefill the "what's it doing?" box with the car's last complaint
  const ci = $("#complaint-input");
  if (ci && !ci.value.trim()) ci.value = v.complaint || "";
}

function analyzeOpts() {
  const v = S.current || {};
  return {
    complaint: ($("#complaint-input") && $("#complaint-input").value.trim()) || null,
    vehicle: v.ephemeral ? null : v.name,
    platform: v.platform || null,
    make: v.make || null, architecture: v.architecture || null,
    stoich: v.stoich || 14.7, airflow_mode: v.airflow_mode || "ve_sd",
    tune_spark: v.tune_spark !== false,        // spark insight on by default in GUI
    find_power: !!v.find_power,
    mods: (v.profile && v.profile.mods) || [],
    ve_axes: v.ve_axes || null,                // bin the grids to this car's tables
    spark_axes: v.spark_axes || null,
    tables: v.tables || null,                  // full tables -> table-aware spark
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
  analyzeUpload(f);
});

async function analyzeUpload(f) {
  enterAnalyze();
  S.reanalyze = () => analyzeUpload(f);        // so the report can re-run this log
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
}

async function runAnalyze() {
  const path = $("#path-input").value.trim();
  if (!path) { toast("Pick or paste a log CSV first."); return; }
  S.reanalyze = () => runAnalyze();            // path-input still holds the log
  busy(true);
  try {
    const body = analyzeOpts(); body.path = path;
    const d = await api("api/analyze", { body });
    showReport(d);
  } catch (err) { toast("Analysis failed: " + err.message, "err"); }
  busy(false);
}

/* "Add power" is a DISPLAY toggle over always-computed adds: instant, no
   re-analysis. Remembers it as this car's default (persists on the next analyze
   of a saved vehicle via find_power). */
function setShowAdds(on) {
  S.showAdds = on;
  if (S.current) S.current.find_power = on;
  if (S.report) showReport(S.report, true);      // re-render from the same payload
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

function showReport(d, rerender) {
  S.report = d;
  // "Add power" is a display filter over always-computed adds. Seed it from the
  // car's default (spark.find_power) on a fresh report; keep it on re-renders.
  if (!rerender) S.showAdds = !!(d.spark && d.spark.find_power);
  if (d.journey) { S.presets._journey = d.journey; d.journey.forEach((s, i) => S.presets._stageIndex[s.key] = i); }
  renderJourney(d.stage, d.journey);
  const rep = $("#report");
  rep.classList.remove("hidden");
  rep.innerHTML = buildVerdict(d) + buildComplaint(d) + buildStaleTables(d)
                + buildCoverage(d) + buildFindings(d) + buildChartShells(d);
  wireReport(d);
  renderCharts();
  if (!rerender) rep.scrollIntoView({ behavior: "smooth", block: "start" });
}

function buildStaleTables(d) {
  const tm = d.tables_meta;
  if (!tm || !tm.stale) return "";
  return `<div class="coverage warn">
    <div class="cov-head"><span class="cov-ico">⚠</span>
      <span>Your saved tune tables were pasted ${tm.analyses_since_paste} analyses ago —
      if you've applied changes since, the current→target numbers are working from an old
      copy.</span>
      <button class="linklike cov-ref" id="stale-edit-btn">re-paste tables</button></div>
  </div>`;
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

function buildComplaint(d) {
  const c = d.complaint;
  if (!c) return "";
  const chips = (c.matched || []).map(m =>
    `<span class="chip accent">${esc(m.label)}</span>`).join(" ");
  const heard = c.matched && c.matched.length
    ? `<div class="cmpl-heard">Heard you: ${chips}${
        (c.related_ids || []).length
          ? ` — <strong>${c.related_ids.length} finding${c.related_ids.length > 1 ? "s" : ""} below speak${c.related_ids.length > 1 ? "" : "s"} to it</strong> (pinned first, tagged ⤷ your complaint).`
          : " — but nothing in this log's findings matches it directly. The coverage notes below may explain why."}</div>`
    : `<div class="cmpl-heard">Couldn't match that to a known symptom — analysis ran normally.
       Try words like "rough idle", "bogs when I floor it", "pings under load", "smells rich".</div>`;
  const gaps = (c.gaps || []).map(g =>
    `<div class="cmpl-gap">⚠ ${esc(g)}</div>`).join("");
  return `<div class="card cmpl-card">
    <div class="cmpl-quote">“${esc(truncate(c.text, 220))}”</div>
    ${heard}${gaps}
  </div>`;
}

function buildFindings(d) {
  const fs = d.findings || [];
  if (!fs.length) return "";
  const rel = new Set(((d.complaint || {}).related_ids) || []);
  const cards = fs.map((f, i) => {
    const [cls, label] = SEV[f.severity] || SEV.info;
    const open = (f.severity === "critical" || f.severity === "warning" || i === 0
                  || rel.has(f.id)) ? "open" : "";
    const tag = rel.has(f.id) ? `<span class="badge cmpl">⤷ your complaint</span>` : "";
    return `
    <details class="finding ${cls}" ${open}>
      <summary><span class="badge ${cls}">${label}</span> ${tag} ${esc(f.title)}
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
    const hasVeTable = !!d.correction.has_table;
    const sub = hasVeTable
      ? `absolute: current → target, matched to your VE table (${axN})`
      : axN
      ? `% change per cell — matched to your VE table (${axN})`
      : `% change per RPM × MAP cell — hover any cell`;
    html += `
    <div class="chart-card">
      <div class="ch-head">
        <h3>VE / fuel correction</h3><span class="ch-sub">${sub}</span>
        <div class="ch-actions">
          ${d.tsv && d.tsv.ve_abs ? `<button id="copy-ve-abs" class="primary">Copy new VE table</button>` : ""}
          ${d.tsv && d.tsv.correction ? `<button id="copy-grid-tsv">Copy % changes (TSV)</button>` : ""}
        </div>
      </div>
      <div id="ve-heatmap" class="chart-box"></div>
      <details class="why expander"><summary>What am I looking at?</summary>
        <div class="why-body">Each cell is how far the fuel model is off at that RPM (rows)
        and engine load (columns, manifold pressure in kPa). <strong>Warm cells = the engine ran lean
        there; raise VE / add fuel.</strong> <strong>Cool cells = rich; pull fuel.</strong> Faint cells
        didn't get enough samples to trust — drive more in that zone.
        ${hasVeTable
          ? "Your VE table is on file, so hovering a cell shows the <strong>absolute value to type in</strong> (current → target). <strong>Copy new VE table</strong> gives your complete table with the changes applied — select the whole table and plain-paste (Ctrl+V); cells the log didn't cover keep your original values."
          : "Paste the TSV into the matching table with Paste Special → Multiply by Percentage; zeros leave a cell unchanged."}</div>
      </details>
    </div>`;
  }
  if (d.spark && d.spark.can_run && d.spark.cells && d.spark.cells.length) {
    const sp = d.spark;
    const showAdds = !!S.showAdds;
    const isAdd = c => c.action === "ADD" || c.action === "AT_CEILING";
    const isPull = c => c.action && c.action !== "OK" && !isAdd(c);
    const addCount = sp.cells.filter(isAdd).length;
    const pullCount = sp.cells.filter(isPull).length;
    const hasWork = pullCount > 0 || (showAdds && addCount > 0);
    const axN = d.spark_axes ? `${d.spark_axes.rpm.length}×${d.spark_axes.map.length}` : null;
    const sub = sp.has_table
      ? `absolute: current → target, matched to your spark table (${axN})`
      : axN ? `degrees to add (+) / pull (−) — matched to your spark table (${axN})`
            : `degrees to add (+) / pull (−) per RPM × MAP cell`;
    const tableNotes = (sp.table_findings || []).map(t =>
      `<p class="sub spark-tablenote">⚠ ${esc(t)}</p>`).join("");
    // "Add power" is a display toggle over always-computed adds -- flipping it is
    // instant (no re-analysis), the default is pulls-only (the safe view).
    const addToggle = `<label class="spark-fp" title="Reveal the knock-safe timing ADDs toward MBT (off = knock-driven pulls only, the safe default)">
        <input type="checkbox" id="spark-add"${showAdds ? " checked" : ""}> Add power${
          addCount ? ` <span class="spark-fp-n">${addCount}</span>` : ""}</label>`;
    if (!hasWork) {
      const powerHint = addCount
        ? `<strong>${addCount} cell${addCount > 1 ? "s" : ""}</strong> look safe for a little more timing toward MBT — flip <strong>Add power</strong> to see them.`
        : "No cells had the load + clean AFR/IAT needed to suggest an add — get some wide-open-throttle pulls into the log to hunt for power.";
      html += `
    <div class="chart-card expert-only">
      <div class="ch-head"><h3>Spark / timing</h3><span class="ch-sub">${sub}</span>
        <div class="ch-actions">${addToggle}</div></div>
      <div class="spark-clean">
        <span class="spark-clean-ico">✓</span>
        <div><strong>No knock anywhere in this log — timing is holding at its current advance.</strong>
        <p class="sub">${powerHint}</p></div>
      </div>
      ${tableNotes}
      ${sp.advisory ? `<p class="sub spark-advisory">${esc(sp.advisory)}</p>` : ""}
    </div>`;
    } else {
      const copyLabel = showAdds ? "Copy changes (Add)"
                                 : (pullCount ? "Copy pulls (Add)" : "Copy changes (Add)");
      html += `
    <div class="chart-card expert-only">
      <div class="ch-head">
        <h3>Spark / timing change</h3><span class="ch-sub">${sub}</span>
        <div class="ch-actions">${addToggle}
          ${d.tsv && d.tsv.spark_abs ? `<button id="copy-spark-abs" class="primary">Copy new spark table</button>` : ""}
          ${d.tsv && d.tsv.spark ? `<button id="copy-spark-tsv">${copyLabel}</button>` : ""}
        </div>
      </div>
      <div id="spark-heatmap" class="chart-box"></div>
      ${tableNotes}
      ${sp.advisory ? `<p class="sub spark-advisory">${esc(sp.advisory)}</p>` : ""}
      <details class="why expander"><summary>What am I looking at?</summary>
        <div class="why-body">Knock-governed timing moves per cell. <strong>Red = pull timing</strong>
        (the engine knocked there — the pull includes a safety margin). <strong>Add power</strong> reveals
        <strong>green = room to add</strong> toward MBT (${sp.has_table ? "capped at the advisory ceiling for your build"
        : "never more than +1° per pass"}); it's off by default because pulling is always the safe move.
        A cell tagged LEAN or HOT means fix fueling or charge temp <em>before</em> touching timing.
        ${sp.has_table
          ? "<strong>Copy new spark table</strong> gives your complete table with the changes applied — paste it over the whole table (plain paste). Cells the log didn't cover keep your original values."
          : "Paste the changes into your spark table with Paste Special → <strong>Add</strong> (degrees, not a percentage)."}</div>
      </details>
    </div>`;
    }
  }
  if (d.maf && d.maf.cells && d.maf.cells.length) {
    html += `
    <div class="chart-card expert-only">
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
    <div class="chart-card expert-only">
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
  const hasExpert = (d.timeseries && d.timeseries.t && d.timeseries.t.length) ||
                    (d.maf && d.maf.cells && d.maf.cells.length);
  if (hasExpert) {
    html += `<div class="beginner-only expert-hint">Showing the essentials —
      <a id="to-expert">switch to Expert mode</a> for the log timeline${
        d.maf && d.maf.cells && d.maf.cells.length ? " and MAF curve" : ""} (knock + lean/rich shading).</div>`;
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
  const cva = $("#copy-ve-abs");
  if (cva) cva.onclick = () => copyText(d.tsv.ve_abs,
    "Copied your COMPLETE new VE table. Select the whole table (top-left cell) and plain-paste (Ctrl+V) — cells the log didn't cover keep your original values.");
  const cm = $("#copy-maf-tsv");
  if (cm) cm.onclick = () => copyText(d.tsv.maf,
    "Copied the MAF row. Paste into the MAF calibration (Multiply by Percentage) — not the VE table.");
  const cs = $("#copy-spark-tsv");
  if (cs) cs.onclick = () => copyText(S.showAdds ? d.tsv.spark_power : d.tsv.spark, d.spark_axes
    ? "Copied — laid out to match your spark table. In VCM Editor: click the top-left cell → Paste Special → Add (degrees)."
    : "Copied the spark changes. Paste into your spark table with Paste Special → Add (degrees, not %).");
  const ca = $("#copy-spark-abs");
  if (ca) ca.onclick = () => copyText(S.showAdds ? d.tsv.spark_abs_power : d.tsv.spark_abs,
    "Copied your COMPLETE new spark table. Select the whole table (top-left cell) and plain-paste (Ctrl+V) — cells the log didn't cover keep your original values.");
  const fp = $("#spark-add");
  if (fp) fp.onchange = e => setShowAdds(e.target.checked);
  const te = $("#to-expert");
  if (te) te.onclick = () => setSkill("expert");
  const cr = $("#cov-ref-btn");
  if (cr) cr.onclick = openChannelsModal;
  const se = $("#stale-edit-btn");
  if (se) se.onclick = () => {
    const v = S.vehicles.find(x => x.name === (S.current && S.current.name));
    openSetup(v || S.current);
    $("#axes-adv").open = true;
    $("#axes-adv").scrollIntoView({ behavior: "smooth", block: "center" });
  };
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
  if ($("#spark-heatmap") && d.spark && d.spark.can_run) S.charts.spark = sparkHeatmap($("#spark-heatmap"), d);
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
  const data = cells.map(c => [mapL.indexOf(c.map), rpmL.indexOf(c.rpm), c.value, c.samples || 0,
                               c.current, c.target]);
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
      const v = p.value[2], n = p.value[3], cur = p.value[4], tgt = p.value[5];
      const verb = v > 1 ? "ran LEAN here — raise VE / add fuel"
                 : v < -1 ? "ran RICH here — pull fuel" : "on target — leave it";
      const abs = (cur != null && tgt != null)
        ? `<br><b>now ${cur} → set ${tgt}</b>` : "";
      return `<b>${rpmL[p.value[1]]} RPM × ${mapL[p.value[0]]} kPa</b><br>` +
             `${v > 0 ? "+" : ""}${v}% · ${n} samples${abs}<br><span style="opacity:.75">${verb}</span>`;
    } },
    series: [{ type: "heatmap", data, label: { show: showLabels, fontSize: 10,
      color: C.text, formatter: p => (p.value[2] > 0 ? "+" : "") + p.value[2].toFixed(1) },
      emphasis: { itemStyle: { shadowBlur: 8, shadowColor: "rgba(0,0,0,.4)" } } }],
  });
  return ch;
}

function sparkHeatmap(el, d) {
  const C = chartColors();
  // hide power ADDs unless "Add power" is on -- render them as no-change cells
  const isAdd = a => a === "ADD" || a === "AT_CEILING";
  const cells = S.showAdds ? d.spark.cells
    : d.spark.cells.map(c => isAdd(c.action) ? { ...c, deg: 0, action: "OK" } : c);
  const rpmL = axisSort([...new Set(cells.map(c => c.rpm))]);
  const mapL = axisSort([...new Set(cells.map(c => c.map))]);
  const data = cells.map(c => [mapL.indexOf(c.map), rpmL.indexOf(c.rpm), c.deg,
                               c.action || "", c.current, c.target]);
  const lim = Math.max(2, ...cells.map(c => Math.abs(c.deg)));
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
      right: 6, top: "middle", text: ["add timing", "pull timing"], dimension: 2,
      textStyle: { color: C.text3, fontSize: 11 },
      inRange: { color: [C.crit, C.bg2, C.opp] } },
    tooltip: { confine: true, formatter: p => {
      const v = p.value[2], act = p.value[3], cur = p.value[4], tgt = p.value[5];
      let line = `${v > 0 ? "+" : ""}${v}°${act ? " · " + act : ""}`;
      if (cur != null && tgt != null)
        line += `<br><span style="opacity:.8">now ${cur}° → set <b>${tgt}°</b></span>`;
      return `<b>${rpmL[p.value[1]]} RPM × ${mapL[p.value[0]]} kPa</b><br>` + line;
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
let updPolling = false;
$("#install-update").onclick = async () => {
  $("#install-update").disabled = true;
  $("#update-status").textContent = "Starting…";
  try {
    const d = await api("api/update/install", { body: {} });
    if (d.frozen === false) {            // source/pip install: just show guidance
      $("#update-status").textContent = d.message;
      $("#install-update").disabled = false;
      return;
    }
    pollUpdate();
  } catch (e) {
    $("#update-status").textContent = "Update failed: " + e.message;
    $("#install-update").disabled = false;
  }
};

const _mb = b => (b / 1048576).toFixed(1);
async function pollUpdate() {
  if (updPolling) return;
  updPolling = true;
  $("#upd-bar").classList.remove("hidden");
  let reachedWork = false;
  for (;;) {
    let p;
    try {
      p = await api("api/update/progress", { body: {} });
    } catch (e) {
      // the server going away mid-update = the app is exiting to swap + relaunch
      if (reachedWork) { finishUpdateUI(); }
      else { $("#update-status").textContent = "Update failed: lost connection."; $("#install-update").disabled = false; }
      break;
    }
    if (p.phase === "downloading" || p.phase === "applying") reachedWork = true;
    renderUpdateProgress(p);
    if (p.phase === "done") { finishUpdateUI(); break; }
    if (p.phase === "error") {
      $("#upd-bar").classList.add("hidden");
      $("#install-update").disabled = false;
      break;
    }
    await new Promise(r => setTimeout(r, 400));
  }
  updPolling = false;
}
function renderUpdateProgress(p) {
  const bar = $("#upd-bar"), fill = $("#upd-fill");
  if (p.phase === "downloading") {
    if (p.total > 0) {
      const pct = Math.min(100, Math.round(p.downloaded / p.total * 100));
      bar.classList.remove("indet"); fill.style.width = pct + "%";
      $("#update-status").textContent = `Downloading ${pct}% — ${_mb(p.downloaded)} / ${_mb(p.total)} MB`;
    } else {
      bar.classList.add("indet");
      $("#update-status").textContent = `Downloading… ${_mb(p.downloaded)} MB`;
    }
  } else if (p.phase === "applying") {
    bar.classList.add("indet");
    $("#update-status").textContent = p.message || "Installing…";
  } else if (p.phase === "error") {
    $("#update-status").textContent = p.message || "Update failed.";
  }
}
function finishUpdateUI() {
  $("#upd-bar").classList.add("indet");
  $("#update-status").textContent = "Installed — restarting on the new version. This window will close.";
  toast("Updating — the app will reopen on the new version.", "ok");
  setTimeout(() => { try { window.close(); } catch (_) {} }, 5000);
}

/* ---------- channels-to-log popout ---------- */
function channelsKeyFor(v) {
  v = v || {};
  if (v.platform === "holley") return "holley";
  const a = v.architecture || "";
  if (a.includes("gen3")) return "gm_gen3_ls";
  if (a.includes("gen5")) return "gm_gen5_lt";
  return "gm_gen4_ls";                       // HP Tuners default
}
function renderChannelsList(key) {
  const ch = S.presets && S.presets.channels && S.presets.channels[key];
  if (!ch) return;
  const holley = key === "holley";
  $("#channels-intro").textContent = holley
    ? "Holley records these by default — just confirm they're in your datalog, then export to CSV."
    : "In VCM Scanner, add these to your channel list (Add Channels), save it as a layout, then Scan → Export Data → CSV.";
  $("#channels-list").innerHTML = ch.channels.map(c =>
    `<div class="chan-row"><span class="chan-name">${esc(c.name)}</span>` +
    `<span class="chan-tier ${c.tier}">${c.tier === "reference" ? "nice to have" : c.tier}</span></div>`).join("");
  $("#channels-pick").value = key;
}
function openChannelsModal() {
  if (!S.presets || !S.presets.channels) return;
  const sel = $("#channels-pick");
  if (!sel.options.length) {
    fill(sel, Object.entries(S.presets.channels).map(([k, v]) => [k, v.label]));
  }
  renderChannelsList(channelsKeyFor(S.current));
  $("#channels-modal").classList.remove("hidden");
}
$("#channels-btn").onclick = openChannelsModal;
$("#channels-close").onclick = () => $("#channels-modal").classList.add("hidden");
$("#channels-modal").onclick = (e) => { if (e.target.id === "channels-modal") $("#channels-modal").classList.add("hidden"); };
$("#channels-pick").onchange = e => renderChannelsList(e.target.value);
$("#channels-copy").onclick = () => {
  const key = $("#channels-pick").value;
  const ch = S.presets.channels[key];
  copyText(ch.channels.map(c => c.name).join("\n"), "Copied the channel list — paste it somewhere handy while you set up your scan.");
};

function buildCoverage(d) {
  const cov = d.channel_coverage;
  if (!cov) return "";
  if (!cov.missing || !cov.missing.length) {
    return `<div class="coverage ok"><span class="cov-ico">✓</span>
      <span>All key channels were logged (${cov.n_present}). Good data to tune on.</span></div>`;
  }
  const items = cov.missing.map(m =>
    `<li class="${m.tier}"><strong>${esc(m.name)}</strong>${m.why ? " — " + esc(m.why) : ""}</li>`).join("");
  const ess = cov.missing.some(m => m.tier === "essential");
  return `<div class="coverage ${ess ? "warn" : "info"}">
    <div class="cov-head"><span class="cov-ico">${ess ? "⚠" : "ⓘ"}</span>
      <span>Add these channels before your next log${ess ? " — some are essential" : ""}:</span>
      <button class="linklike cov-ref" id="cov-ref-btn">see the full list</button></div>
    <ul class="cov-miss">${items}</ul></div>`;
}

/* ---------- compare two logs ---------- */
const cmp = { a: null, b: null };
function cmpRefresh() {
  $("#cmp-name-a").textContent = cmp.a ? cmp.a.replace(/^.*[\\/]/, "") : "";
  $("#cmp-name-b").textContent = cmp.b ? cmp.b.replace(/^.*[\\/]/, "") : "";
  $("#cmp-run").disabled = !(cmp.a && cmp.b);
}
$("#compare-btn").onclick = () => { $("#compare-modal").classList.remove("hidden"); cmpRefresh(); };
$("#compare-close").onclick = () => $("#compare-modal").classList.add("hidden");
$("#compare-modal").onclick = e => { if (e.target.id === "compare-modal") $("#compare-modal").classList.add("hidden"); };
async function cmpBrowse(slot) {
  const d = await api("api/pick-file", { body: {} });
  if (d.path) { cmp[slot] = d.path; cmpRefresh(); }
}
$("#cmp-browse-a").onclick = () => cmpBrowse("a");
$("#cmp-browse-b").onclick = () => cmpBrowse("b");
$("#cmp-run").onclick = async () => {
  $("#cmp-msg").textContent = "Analyzing both…";
  $("#cmp-run").disabled = true;
  try {
    const body = analyzeOpts(); body.path_a = cmp.a; body.path_b = cmp.b;
    const d = await api("api/compare", { body });
    $("#compare-modal").classList.add("hidden");
    $("#cmp-msg").textContent = "";
    showComparison(d);
  } catch (e) {
    $("#cmp-msg").textContent = "Compare failed: " + e.message;
    $("#cmp-run").disabled = false;
  }
};

function fmtCmpVal(v) { return v == null ? "—" : (typeof v === "number" ? (Math.round(v * 100) / 100) : v); }
function showComparison(d) {
  const c = d.comparison;
  S.report = null;                          // comparison view, not a single report
  disposeCharts();
  const rep = $("#report");
  rep.classList.remove("hidden");
  const rows = c.metrics.filter(m => m.a != null || m.b != null).map(m => {
    const cls = m.same ? "same" : (m.better ? "better" : "worse");
    const mark = m.same ? "—" : (m.better ? "✓" : "⚠");
    return `<tr class="${cls}"><td>${esc(m.label)}</td><td class="cmp-a">${fmtCmpVal(m.a)}</td>
      <td class="cmp-arrow">→</td><td class="cmp-b">${fmtCmpVal(m.b)}</td><td class="cmp-mark">${mark}</td></tr>`;
  }).join("");
  const fcol = (title, arr, cls) =>
    `<div class="cmp-fcol ${cls}"><h4>${title} <span>(${arr.length})</span></h4>` +
    (arr.length ? `<ul>${arr.map(f => `<li>${esc(f.title)}</li>`).join("")}</ul>`
                : `<p class="sub">none</p>`) + `</div>`;
  const hasDelta = c.correction_delta && c.correction_delta.length;
  rep.innerHTML = `
    <div class="verdict">
      <div class="vh-row">
        <span class="chip">${esc(d.a.log_name || "before")}</span>
        <span class="cmp-arrow">→</span>
        <span class="chip accent">${esc(d.b.log_name || "after")}</span>
        ${c.stage && c.stage.advanced ? `<span class="badge opp">journey advanced</span>` : ""}
      </div>
      <h2>Before → after</h2>
      <p class="lead">${esc(c.headline)}</p>
      <table class="cmp-table">${rows}</table>
    </div>
    <div class="cmp-findings">
      ${fcol("Cleared", c.findings.resolved, "resolved")}
      ${fcol("Still there", c.findings.persisting, "persisting")}
      ${fcol("New", c.findings.new, "new")}
    </div>
    ${hasDelta ? `<div class="chart-card"><div class="ch-head"><h3>Where the fuel error changed</h3>
      <span class="ch-sub">green = got closer to target · red = moved away</span></div>
      <div id="cmp-heatmap" class="chart-box"></div></div>` : ""}`;
  if (hasDelta) S.charts.cmp = compareHeatmap($("#cmp-heatmap"), c.correction_delta);
  rep.scrollIntoView({ behavior: "smooth", block: "start" });
}

function compareHeatmap(el, cells) {
  const C = chartColors();
  const rpmL = axisSort([...new Set(cells.map(c => c.rpm))]);
  const mapL = axisSort([...new Set(cells.map(c => c.map))]);
  const data = cells.map(c => [mapL.indexOf(c.map), rpmL.indexOf(c.rpm), c.delta, c.a, c.b]);
  const lim = Math.max(2, ...cells.map(c => Math.abs(c.delta)));
  const cellW = (el.clientWidth - 182) / Math.max(1, mapL.length);
  const ch = echarts.init(el, null, { renderer: "canvas" });
  ch.setOption({
    animationDuration: 350,
    grid: { left: 86, right: 96, top: 18, bottom: 44 },
    xAxis: { type: "category", data: mapL, name: "MAP (kPa)", nameLocation: "middle", nameGap: 30,
      axisLabel: { color: C.text3 }, nameTextStyle: { color: C.text3 },
      axisLine: { lineStyle: { color: C.grid } }, splitArea: { show: true } },
    yAxis: { type: "category", data: rpmL, name: "RPM", axisLabel: { color: C.text3 },
      nameTextStyle: { color: C.text3 }, axisLine: { lineStyle: { color: C.grid } }, splitArea: { show: true } },
    visualMap: { min: -lim, max: lim, calculable: true, orient: "vertical", right: 6, top: "middle",
      text: ["worse", "better"], dimension: 2, textStyle: { color: C.text3, fontSize: 11 },
      inRange: { color: [C.opp, C.bg2, C.crit] } },
    tooltip: { confine: true, formatter: p =>
      `<b>${rpmL[p.value[1]]} RPM × ${mapL[p.value[0]]} kPa</b><br>` +
      `${p.value[3] > 0 ? "+" : ""}${p.value[3]}% → ${p.value[4] > 0 ? "+" : ""}${p.value[4]}%` +
      `<br><span style="opacity:.75">${p.value[2] < 0 ? "closer to target" : p.value[2] > 0 ? "further off" : "no change"}</span>` },
    series: [{ type: "heatmap", data, label: { show: cellW >= 46, fontSize: 10, color: C.text,
      formatter: p => (p.value[2] > 0 ? "+" : "") + p.value[2].toFixed(1) },
      emphasis: { itemStyle: { shadowBlur: 8, shadowColor: "rgba(0,0,0,.4)" } } }],
  });
  return ch;
}

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
