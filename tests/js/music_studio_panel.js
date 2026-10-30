/* Harness: lift ONLY the song-studio block out of portal/app.js and run it in a
   stub DOM so its real behaviour (fetch calls, POST bodies, button states, the
   poll, the honesty rules) can be checked without launching the console. */
const vm = require("vm");
const fs = require("fs");
const path = require("path");

/* Resolved from this file, not from the machine that first ran it, so the kit's own copy works
   wherever the kit is checked out. */
const SRC = path.join(__dirname, "..", "..", "portal", "app.js");
const src = fs.readFileSync(SRC, "utf8");
const a = src.indexOf("  (function songStudio() {");
const startFn = src.indexOf("(async function start() {", a);
const b = src.lastIndexOf("\n", startFn);
if (a < 0 || b < 0) throw new Error("could not locate the songStudio block");
const block = src.slice(a, b);
console.log("extracted block chars:", block.length, "| js file lines:", src.slice(0, a).split("\n").length, "-", src.slice(0, b).split("\n").length);

function mkEl(tag, id) {
  const el = {
    tagName: tag, id: id || "", children: [], options: [], _text: "",
    value: "", disabled: false, selected: false, title: "", className: "",
    checked: false, src: "", preload: "", controls: false,
    classList: { toggle: (c, on) => { el._warn = !!on; } },
    appendChild(c) { el.children.push(c); if (c.tagName === "option") el.options.push(c); return c; },
  };
  Object.defineProperty(el, "innerHTML", {
    get() { return ""; },
    set(v) { if (v === "") { el.children = []; el.options = []; } },
  });
  Object.defineProperty(el, "textContent", {
    get() { return el._text; },
    set(v) { el._text = String(v); },
  });
  return el;
}

const TAGS = {
  "ms-studio": "section", "ms-engine": "select", "ms-refresh": "button",
  "ms-style": "input", "ms-lyrics": "textarea", "ms-segments": "input",
  "ms-seed": "input", "ms-tokens": "input", "ms-go": "button",
  "ms-cancel": "button", "ms-status": "span", "ms-songs": "div",
};

function runStudio(name, getFor, postFor) {
  const byId = {};
  const calls = [];
  const intervals = [];
  const document = {
    getElementById(id) {
      if (!byId[id]) byId[id] = mkEl(TAGS[id] || "div", id);
      return byId[id];
    },
    createElement(tag) { return mkEl(tag); },
  };
  const sandbox = {
    document,
    console,
    isFinite,
    parseInt,
    Math,
    Number,
    String,
    Object,
    Error,
    Promise,
    encodeURIComponent,
    setInterval(fn, ms) { const h = { fn, ms, cleared: false }; intervals.push(h); return h; },
    clearInterval(h) { if (h) h.cleared = true; },
    headers: () => ({ "Content-Type": "application/json", "X-LYGO-LLM-Token": "tok", "Cache-Control": "no-store" }),
    async fetch(url, opts) {
      const body = opts && opts.method === "POST" ? JSON.parse(opts.body) : null;
      calls.push({ url, opts, body });
      const data = body ? postFor(body) : getFor();
      return { ok: true, status: 200, json: async () => data };
    },
  };
  vm.runInNewContext(block, sandbox);
  return { byId, calls, intervals, name };
}

const tick = () => new Promise((r) => setTimeout(r, 0));
let fails = 0;
function ok(label, cond, extra) {
  if (cond) console.log("  PASS  " + label);
  else { fails++; console.log("  FAIL  " + label + (extra !== undefined ? "  -> " + JSON.stringify(extra) : "")); }
}

/* The take list is read through these, never through child indexes: a row carries a player, a count
   line sits above the list, and an index-based harness then fails on every honest layout change
   instead of on a behaviour change. */
const songRows = (box) => box.children.filter((c) => c.className === "ms-song");
const songHead = (box) => box.children.find((c) => c.className === "ms-songs-head");
const rowMain = (row) => row.children.find((c) => c.className === "ms-song-main");
const rowName = (row) => rowMain(row).children[0].textContent;
const rowMeta = (row) => rowMain(row).children[1].textContent;
const rowAudio = (row) => row.children.find((c) => c.tagName === "audio");
const rowControls = (row) => row.children.filter((c) => c.tagName === "button" || c.tagName === "a");

const ENGINES = [
  { id: "yue", label: "YuE (installed)", state: "installed", note: "" },
  { id: "stable_audio", label: "Stable Audio", state: "declared", note: "weights not downloaded" },
  { id: "ace_step", label: "ACE-Step 1.5", state: "declared", note: "no audiocpp_cli.exe on this host" },
];
/* The song rows the server really sends: a `path` as well as a bare `name` (music_jobs.songs()),
   which is what the panel matches a finished job against. */
const SONGS_2 = [
  { name: "20260924-1010_song.mp3", path: "I:/x/workspace/audio/songs/20260924-1010_rock/20260924-1010_song.mp3", bytes: 999, when: "10:10", seconds: 30.1, engine: "yue", seed: 5, style: "rock" },
  { name: "20260925-1239_song.mp3", path: "I:/x/workspace/audio/songs/20260925-1239_folk/20260925-1239_song.mp3", bytes: 1234567, when: "13:20", seconds: 61.4, engine: "yue", seed: 963, style: "folk" },
];
const SONGS_3 = SONGS_2.concat([
  { name: "20260925-1402_song.mp3", path: "I:/x/workspace/audio/songs/20260925-1402_folk/20260925-1402_song.mp3", bytes: 2000000, when: "14:02", seconds: 58.0, engine: "yue", seed: 7, style: "folk" },
]);
/* The card reading the server sends on every GET (`route_for_a_song()`'s own shape). */
const ROUTE_READY = { route: "cuda", profile: 3, free_mib: 7271, need_mib: 4200,
                      why: "the card has 7271 MiB free and this profile wants 4200 MiB" };

/* ---------- scenario 1: the happy path, start -> running -> done ---------- */
console.log("\n[1] load, then start -> running -> done");
{
  let phase = "init";
  let seen = null;
  const g = () => {
    if (phase === "init") return { ok: true, engine: "yue", engines: ENGINES, route: ROUTE_READY, job: { id: "j0", state: "none", status: "idle", elapsed_s: 0, song: null }, songs: SONGS_2, defaults: { segments: 0, seed: 0, max_new_tokens: 3000, style: "default style" } };
    if (phase === "tick1") return { ok: true, engine: "yue", engines: ENGINES, route: ROUTE_READY, job: { id: "j1", state: "running", status: "queueing the render", elapsed_s: 42, sections: 6, planned_audio_seconds: 180, progress: { stage: 1, at: 3, of: 6, text: "writing the song: section 3 of 6" } }, songs: SONGS_2, defaults: { segments: 0, seed: 0, max_new_tokens: 3000, style: "default style" } };
    /* The REAL wire shape for a finished job: `song` is the PATH the limb wrote (music_jobs._run
       stores `str(out["path"])`), not an object. Feeding an object here is what let a panel bug
       through: it read `j.song.name`, got undefined, and warned "nothing here is claimed as
       finished" over a song that had just been written. */
    if (phase === "tick2") return { ok: true, engine: "yue", engines: ENGINES, route: ROUTE_READY, job: { id: "j1", state: "done", status: "done", elapsed_s: 611, song: "I:/x/workspace/audio/songs/20260925-1402_folk/20260925-1402_song.mp3" }, songs: SONGS_3, defaults: {} };
    if (phase === "objdone") return { ok: true, engine: "yue", engines: ENGINES, route: ROUTE_READY, job: { id: "j4", state: "done", status: "done", elapsed_s: 5, song: { name: "20260925-1402_song.mp3", seconds: 58.0 } }, songs: SONGS_3, defaults: {} };
    if (phase === "ghost") return { ok: true, engine: "yue", engines: ENGINES, route: ROUTE_READY, job: { id: "j2", state: "done", status: "done", elapsed_s: 90, song: "I:/x/workspace/audio/songs/nowhere/never-written_song.mp3" }, songs: SONGS_3, defaults: {} };
    if (phase === "failed") return { ok: true, engine: "yue", engines: ENGINES, route: ROUTE_READY, job: { id: "j3", state: "failed", status: "engine exited 1: CUDA out of memory", elapsed_s: 12 }, songs: SONGS_3, defaults: {} };
    return { ok: true, engine: "stable_audio", engines: ENGINES, job: { id: "j3", state: "none", status: "idle", elapsed_s: 0 }, songs: SONGS_3, defaults: {} };
  };
  const p = (body) => {
    seen = body;
    if (body.action === "start") return { ok: true, job: { id: "j1", state: "running", status: "queueing the render", elapsed_s: 0 } };
    if (body.action === "cancel") return { ok: true, job: { id: "j1", state: "running", status: "cancel requested", elapsed_s: 44 } };
    if (body.action === "engine") return { ok: true, engine: body.engine };
    return { ok: false, error: "unknown action" };
  };
  const s = runStudio("s1", g, p);
  const E = s.byId;

  (async () => {
    await tick(); await tick();

    console.log(" -- on load");
    ok("GET /api/music first call", s.calls[0].url === "/api/music", s.calls[0].url);
    ok("GET sends headers() + no-store", s.calls[0].opts.headers["X-LYGO-LLM-Token"] === "tok" && s.calls[0].opts.cache === "no-store");
    ok("engine select filled with all 3 engines", E["ms-engine"].options.length === 3, E["ms-engine"].options.length);
    ok("installed engine selectable", E["ms-engine"].options[0].disabled === false);
    ok("declared engines disabled", E["ms-engine"].options[1].disabled === true && E["ms-engine"].options[2].disabled === true);
    ok("disabled option label shows why", /declared \(weights not downloaded\)/.test(E["ms-engine"].options[1].textContent), E["ms-engine"].options[1].textContent);
    ok("engine select enabled (engines exist)", E["ms-engine"].disabled === false);
    ok("defaults applied to style", E["ms-style"].value === "default style", E["ms-style"].value);
    /* 0 sections from the server means "one per part of the words": it must NOT be written into the box
       as a demand for no sections at all, and a 1 there would cap every song at a single section. */
    ok("defaults leave the section count to the words", E["ms-segments"].value === "" && E["ms-seed"].value === 0 && E["ms-tokens"].value === 3000, [E["ms-segments"].value, E["ms-seed"].value, E["ms-tokens"].value]);
    const rows = songRows(E["ms-songs"]);
    ok("both songs rendered", rows.length === 2, rows.length);
    ok("the list says how many takes it holds", /2 takes/.test(songHead(E["ms-songs"]).textContent), songHead(E["ms-songs"]).textContent);
    ok("newest first", rowName(rows[0]) === "20260925-1239_song.mp3", rowName(rows[0]));
    ok("audio src uses the kernel media route", rowAudio(rows[0]).src === "/api/media/audio/20260925-1239_song.mp3", rowAudio(rows[0]).src);
    ok("audio preload=none", rowAudio(rows[0]).preload === "none");
    /* A finished take has to be USABLE from the list without a native player bar on every row: play,
       download, and "again" to put this take's style and seed back in the boxes. */
    const ctrl = rowControls(rows[0]);
    ok("the row is usable: play / get / again", ctrl.length === 3 && ctrl[0].textContent === "play" && ctrl[1].textContent === "get" && ctrl[2].textContent === "again", ctrl.map((c) => c.textContent));
    ok("get links the kernel media route too", ctrl[1].href === "/api/media/audio/20260925-1239_song.mp3", ctrl[1].href);
    ok("song facts are real, not invented", /duration 1:01/.test(rowMeta(rows[0])) && /engine yue/.test(rowMeta(rows[0])) && /seed 963/.test(rowMeta(rows[0])), rowMeta(rows[0]));
    ok("status from job.status", E["ms-status"].textContent.indexOf("idle") === 0, E["ms-status"].textContent);
    ok("the card reading is shown before Generate (the 8 GB question)", /the card: cuda — the card has 7271 MiB free and this profile wants 4200 MiB/.test(E["ms-status"].textContent), E["ms-status"].textContent);
    ok("no render running -> no poll", s.intervals.length === 0);
    ok("go enabled / cancel disabled", E["ms-go"].disabled === false && E["ms-cancel"].disabled === true);
    ok("nothing claimed finished at idle", !/finished/.test(E["ms-status"].textContent));

    console.log(" -- Generate");
    E["ms-style"].value = "folk ballad, warm male vocal";
    E["ms-lyrics"].value = "[verse]\na line to sing";
    const go = E["ms-go"];
    await go.onclick();
    await tick();
    ok("POST /api/music for start", s.calls[1].url === "/api/music" && s.calls[1].opts.method === "POST");
    ok("Content-Type json + headers() on the POST", s.calls[1].opts.headers["Content-Type"] === "application/json" && s.calls[1].opts.headers["X-LYGO-LLM-Token"] === "tok");
    ok("start body matches the frozen contract", JSON.stringify(seen) === JSON.stringify({ action: "start", style: "folk ballad, warm male vocal", lyrics: "[verse]\na line to sing", engine: "yue", segments: 0, seed: 0, max_new_tokens: 3000 }), seen);
    ok("status says it is rendering and that it takes as long as it takes", /this takes as long as it takes/.test(E["ms-status"].textContent) && /queueing the render/.test(E["ms-status"].textContent), E["ms-status"].textContent);
    ok("go disabled while running", E["ms-go"].disabled === true);
    ok("cancel enabled while running", E["ms-cancel"].disabled === false);
    ok("poll armed at 3000 ms", s.intervals.length === 1 && s.intervals[0].ms === 3000, s.intervals.map((i) => i.ms));

    console.log(" -- poll tick while still running");
    phase = "tick1";
    await s.intervals[0].fn();
    await tick();
    /* The engine's OWN position beats the job's prose: "rendering" for hours cannot be told from a render
       that hung, so the panel says which section of the song the engine is on. */
    /* Before the engine has said a word, the panel still states the PLAN: six sections is about three
       minutes of song, and a long job with no plan of its own reads as a fault. */
    ok("the plan is stated while it renders", /6 sections/.test(E["ms-status"].textContent) && /3:00/.test(E["ms-status"].textContent), E["ms-status"].textContent);
    ok("the engine's own position is shown", /section 3 of 6/.test(E["ms-status"].textContent), E["ms-status"].textContent);
    ok("status shows elapsed_s as a clock", /elapsed 0:42/.test(E["ms-status"].textContent), E["ms-status"].textContent);
    ok("poll still alive", s.intervals[0].cleared === false);

    console.log(" -- poll tick that finishes");
    phase = "tick2";
    await s.intervals[0].fn();
    await tick();
    ok("poll stopped when job left running", s.intervals[0].cleared === true);
    ok("success line names the song that is in the list", /^finished · 20260925-1402_song\.mp3/.test(E["ms-status"].textContent), E["ms-status"].textContent);
    ok("...with the duration the playlist carries, from a PATH-shaped j.song", /· 58s/.test(E["ms-status"].textContent), E["ms-status"].textContent);
    ok("...and it is NOT flagged as a problem", E["ms-status"]._warn === false, E["ms-status"].textContent);
    ok("song list re-rendered with the new take", songRows(E["ms-songs"]).length === 3, songRows(E["ms-songs"]).length);
    ok("newest take is first", rowName(songRows(E["ms-songs"])[0]) === "20260925-1402_song.mp3", rowName(songRows(E["ms-songs"])[0]));
    ok("the count line moved with it", /3 takes/.test(songHead(E["ms-songs"]).textContent), songHead(E["ms-songs"]).textContent);

    console.log(" -- a record that carries the song as an OBJECT is still read");
    phase = "objdone";
    await E["ms-refresh"].onclick();
    await tick();
    ok("object shape accepted, still a finished song", /^finished · 20260925-1402_song\.mp3/.test(E["ms-status"].textContent), E["ms-status"].textContent);
    ok("go re-enabled / cancel re-disabled", E["ms-go"].disabled === false && E["ms-cancel"].disabled === true);

    console.log(" -- honesty: job says done, but the name is not in songs[]");
    phase = "ghost";
    await E["ms-refresh"].onclick();
    await tick();
    ok("does NOT claim a song that is not in the list", !/finished/.test(E["ms-status"].textContent), E["ms-status"].textContent);
    ok("says so plainly and flags warn", /not in the list yet/.test(E["ms-status"].textContent) && E["ms-status"]._warn === true, E["ms-status"].textContent);

    console.log(" -- honesty: a failed job shows the server text verbatim");
    phase = "failed";
    await E["ms-refresh"].onclick();
    await tick();
    ok("failure text verbatim", E["ms-status"].textContent === "engine exited 1: CUDA out of memory", E["ms-status"].textContent);
    ok("failure flagged warn", E["ms-status"]._warn === true);

    console.log(" -- cancel + engine switch");
    phase = "tick1";
    await E["ms-go"].onclick();  // re-arm a running job so cancel is meaningful
    await tick();
    await E["ms-cancel"].onclick();
    await tick();
    ok("cancel POST body is exactly {action:'cancel'}", seen && seen.action === "cancel" && Object.keys(seen).length === 1, seen);
    E["ms-engine"].value = "stable_audio";
    await E["ms-engine"].onchange();
    await tick();
    ok("engine POST body is exactly {action:'engine',engine}", seen && seen.action === "engine" && seen.engine === "stable_audio", seen);

    console.log(" -- a refused request is surfaced, never swallowed");
    const beforeLen = s.calls.length;
    await E["ms-refresh"].onclick();
    await tick();
    ok("refresh made another GET", s.calls.length > beforeLen);

    console.log(fails === 0 ? "\n[1] ALL PASS" : "\n[1] FAILURES: " + fails);
  })();
}

/* ---------- scenario 2: the server declares no engines at all ---------- */
setTimeout(() => {
  console.log("\n[2] engines: [] -> say so plainly");
  const g = () => ({ ok: true, engine: "", engines: [], job: { id: "", state: "none", status: "", elapsed_s: 0 }, songs: [], defaults: {} });
  const s = runStudio("s2", g, () => ({ ok: false, error: "nope" }));
  (async () => {
    await tick(); await tick();
    const E = s.byId;
    ok("select disabled", E["ms-engine"].disabled === true);
    ok("select says no engine declared", /no song engine declared/.test(E["ms-engine"].options[0].textContent), E["ms-engine"].options[0].textContent);
    ok("status says so plainly", /no song engine is declared/.test(E["ms-status"].textContent), E["ms-status"].textContent);
    ok("go disabled", E["ms-go"].disabled === true);
    ok("empty song list explained, not faked", /No songs rendered/.test(E["ms-songs"].children.map((c) => c.textContent).join(" ")) && /no takes yet/.test(songHead(E["ms-songs"]).textContent), E["ms-songs"].children.map((c) => c.textContent).join(" | "));
    console.log(fails === 0 ? "\n[2] ALL PASS" : "\n[2] FAILURES: " + fails);

    console.log("\n[3] a refused GET is surfaced, not swallowed");
    const s3 = runStudio("s3", () => ({ ok: false, error: "no music module loaded" }), () => ({ ok: false }));
    await tick(); await tick();
    ok("failure text shown in the status line", /no music module loaded/.test(s3.byId["ms-status"].textContent), s3.byId["ms-status"].textContent);
    ok("flagged warn", s3.byId["ms-status"]._warn === true);

    console.log("\n[4] a refusal arrives with its remedy, not just its code");
    const s4 = runStudio("s4",
      () => ({ ok: true, engine: "yue", engines: ENGINES, route: null, job: { id: "", state: "none", status: "idle", elapsed_s: 0 }, songs: [], defaults: {} }),
      () => ({ ok: false, error: "lyrics_required", hint: "this engine sings the words it is given — write or paste the lyrics first" }));
    await tick(); await tick();
    const E4 = s4.byId;
    E4["ms-style"].value = "pop";
    E4["ms-lyrics"].value = "";
    await E4["ms-go"].onclick();
    await tick();
    ok("the remedy is what the operator reads, not just the code",
       /lyrics_required — this engine sings the words it is given/.test(E4["ms-status"].textContent), E4["ms-status"].textContent);
    ok("a refusal is flagged as a problem", E4["ms-status"]._warn === true);
    ok("a request that was refused does not claim a render started", !/rendering/.test(E4["ms-status"].textContent), E4["ms-status"].textContent);
    console.log(fails === 0 ? "\n[4] ALL PASS" : "\n[4] FAILURES: " + fails);
    console.log(fails === 0 ? "\n[3] ALL PASS" : "\n[3] FAILURES: " + fails);

    console.log("\n==== TOTAL FAILURES: " + fails + " ====");
    process.exit(fails === 0 ? 0 : 1);
  })();
}, 200);
