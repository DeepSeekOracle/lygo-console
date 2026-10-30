/* Harness: run the REAL portal/archive.js in a stub DOM so the band's own behaviour can be checked
 * without launching the console - the fetch calls, the POST bodies, what lands on screen, and the
 * rules that only exist in the browser half: archived text is inserted as TEXT, a failed search is
 * not painted as an empty one, a field written from a result announces itself, and nothing is
 * fetched for an ask the operator has not made.
 *
 * It reads the shipped file (no copy), so a change to portal/archive.js is what this test sees.
 *
 * Run:  node tests/js/archive_panel.js     (exit 0 and "TOTAL FAILURES: 0" when green)
 */
"use strict";

const vm = require("vm");
const fs = require("fs");
const path = require("path");

const SRC = path.join(__dirname, "..", "..", "portal", "archive.js");
const CODE = fs.readFileSync(SRC, "utf8");

let failures = 0;
function check(ok, what, detail) {
  if (!ok) {
    failures += 1;
    console.log("  FAIL  " + what + (detail ? "  --  " + detail : ""));
  }
  return ok;
}

/* ---- the stub page --------------------------------------------------------------------------- */

const INNER_HTML_VIOLATIONS = [];

function mkEl(id) {
  const el = {
    id: id || "",
    tagName: id === "wb-read" ? "PRE" : "DIV",
    children: [],
    handlers: {},
    events: [],
    value: "",
    disabled: false,
    hidden: false,
    className: "",
    title: "",
    type: "",
    href: "",
    target: "",
    rel: "",
    _text: "",
    _warned: false,
    options: [],
    selected: false,
    classList: {
      toggle(name, on) {
        if (name === "warn") el._warned = !!on;
      },
    },
    addEventListener(type, fn) {
      (el.handlers[type] = el.handlers[type] || []).push(fn);
    },
    dispatchEvent(ev) {
      el.events.push(ev);
      (el.handlers[ev && ev.type] || []).forEach((fn) => fn(ev));
      return true;
    },
    fire(type, ev) {
      (el.handlers[type] || []).forEach((fn) => fn(ev || {}));
    },
    appendChild(child) {
      el.children.push(child);
      return child;
    },
    removeChild(child) {
      const i = el.children.indexOf(child);
      if (i >= 0) el.children.splice(i, 1);
      return child;
    },
    get firstChild() {
      return el.children[0] || null;
    },
  };
  Object.defineProperty(el, "textContent", {
    get() {
      return el._text;
    },
    set(v) {
      el._text = String(v);
      el.children = [];
    },
  });
  Object.defineProperty(el, "innerHTML", {
    get() {
      return "";
    },
    set(v) {
      INNER_HTML_VIOLATIONS.push((el.id || "?") + " <- " + String(v).slice(0, 60));
    },
  });
  return el;
}

const IDS = [
  "wb-band", "wb-target", "wb-word", "wb-asof", "wb-scope", "wb-go", "wb-watch", "wb-check",
  "wb-forget", "wb-undo", "wb-status", "wb-answer", "wb-closest", "wb-results", "wb-readwrap",
  "wb-readtitle", "wb-read", "wb-readclose", "wb-watched",
];

const page = {};
IDS.forEach((id) => (page[id] = mkEl(id)));
page["wb-scope"].value = "site";

const document = {
  readyState: "complete",
  getElementById: (id) => page[id] || null,
  createElement: (tag) => mkEl("made-" + tag),
  addEventListener: () => {},
};

/* ---- the wire -------------------------------------------------------------------------------- */

const wire = { calls: [], queue: [], reply: null };

function answer(body, status) {
  return { status: status || 200, body: body };
}

function fetchStub(url, opts) {
  const call = {
    url: url,
    method: (opts && opts.method) || "GET",
    headers: (opts && opts.headers) || {},
    body: opts && opts.body ? JSON.parse(opts.body) : null,
  };
  wire.calls.push(call);
  const reply = wire.queue.length ? wire.queue.shift() : wire.reply;
  if (!reply) {
    return Promise.reject(new Error("the harness was not told what to answer for " + url));
  }
  if (reply.network) return Promise.reject(new Error(reply.network));
  return Promise.resolve({
    status: reply.status || 200,
    json: () => Promise.resolve(reply.body),
  });
}

const win = { LYGO_TOKEN: "test-token" };
const sandbox = {
  document: document,
  window: win,
  sessionStorage: { getItem: () => "stored-token", setItem: () => {} },
  fetch: fetchStub,
  Event: class Event {
    constructor(type, opts) {
      this.type = type;
      this.bubbles = !!(opts && opts.bubbles);
    }
  },
  console: console,
  JSON: JSON,
  String: String,
  Number: Number,
  Array: Array,
  Object: Object,
  Promise: Promise,
  setTimeout: setTimeout,
};
sandbox.globalThis = sandbox;

const tick = () => new Promise((r) => setImmediate(r));

/* ---- the fixtures (the real wire shapes the server sends) ------------------------------------- */

const card = {
  doors_up: 3,
  doors: 3,
  cached: false,
  state_text: "all 3 doors answered",
  missing: [],
  groups: [
    { title: "The last searches", rows: [] },
    {
      title: "Watched links (link rot)",
      rows: [{ k: "https://chatagent.ca/old", v: "dead, archived copy exists", note: "live 404 · archived 20100501120000" }],
    },
  ],
};

const searchOK = {
  ok: true,
  action: "search",
  target: "https://chatagent.ca",
  count: 1,
  seconds: 3.5,
  found_nothing: false,
  errors: [],
  saved: { saved: true },
  closest: { ok: true, via: "memento timegate", drift: "asked 2020, served 2002",
             snapshot: { url: "https://web.archive.org/web/20020120142510/http://chatagent.ca/",
                         timestamp: "20020120142510" } },
  rows: [
    { timestamp: "20020120142510", date: "2002-01-20 14:25", statuscode: "200", mimetype: "text/html",
      length: "1792", original: "http://chatagent.ca/",
      snapshot_url: "https://web.archive.org/web/20020120142510/http://chatagent.ca/" },
  ],
};

const searchFailed = {
  ok: false,
  error: "cdx_timeout",
  why: "the index did not answer within 75s",
  hint: "narrow the ask: from/to, or match=exact",
  errors: ["captures: cdx_timeout"],
  found_nothing: false,
  asked: [{ door: "closest", ok: true, seconds: 1.2 }, { door: "captures", ok: false, error: "cdx_timeout" }],
};

const readOK = {
  ok: true,
  archived: true,
  served_timestamp: "20020120142510",
  bytes: 42,
  truncated: false,
  seconds: 0.9,
  text: '<img src=x onerror="window.__pwned=1"> some old words',
};

async function main() {
  const ctx = vm.createContext(sandbox);
  console.log("loaded portal/archive.js (" + CODE.length + " chars) into a stub DOM");

  /* [1] the read-out first, then the state --------------------------------------------------- */
  wire.queue = [answer({ card: card, queries: [{}, {}], watched: [{}] }), answer({ card: card, queries: [{}, {}], watched: [{}] })];
  vm.runInContext(CODE, ctx);
  await tick();
  console.log("[1] init");
  check(wire.calls.length === 2, "init asks the read-out then the state", JSON.stringify(wire.calls.map((c) => c.url)));
  check(wire.calls[0].url === "/api/webarchiveinfo", "the read-out is asked FIRST");
  check(wire.calls[0].headers["X-LYGO-LLM-Token"] === "stored-token", "the console token is sent");
  check(page["wb-status"]._text.indexOf("3/3 archive doors answered") === 0,
    "the status line reports the doors that answered", page["wb-status"]._text);
  check(page["wb-watched"].children.length === 1, "the watched list is painted from the card");
  check(page["wb-watched"].children[0]._text.indexOf("dead, archived copy exists") > 0,
    "a watched row keeps the server's own finding words", page["wb-watched"].children[0]._text);
  console.log("[1] ALL PASS");

  /* [2] a search that runs ------------------------------------------------------------------- */
  console.log("[2] search: happy path");
  page["wb-target"].value = "chatagent.ca";
  page["wb-word"].value = "";
  page["wb-asof"].value = "2020";
  wire.reply = answer(searchOK);
  page["wb-go"].fire("click");
  await tick();
  const post = wire.calls[wire.calls.length - 1];
  check(post.url === "/api/webarchive" && post.method === "POST", "the search is a POST to /api/webarchive");
  check(post.body.action === "search" && post.body.target === "chatagent.ca" && post.body.as_of === "2020",
    "the POST body carries the ask", JSON.stringify(post.body));
  check(post.body.scope === "site" && post.body.limit === 25, "scope and limit travel with it", JSON.stringify(post.body));
  check(page["wb-go"].disabled === false, "the button is usable again after an answer");
  check(page["wb-results"].children.length === 1, "one result row was painted");
  const row = page["wb-results"].children[0];
  const link = row.children[0];
  check(link.tagName === "a" || link.href === "https://web.archive.org/web/20020120142510/http://chatagent.ca/",
    "the row links to the snapshot the server named", link.href);
  check(link._text.indexOf("http://chatagent.ca/") === 0 && link._text.indexOf("2002-01-20") > 0,
    "the row shows the URL and the date as TEXT", link._text);
  check(link.rel === "noopener" && link.target === "_blank", "a snapshot link cannot reach back through window.opener");
  const kinds = row.children.map((c) => c._text);
  check(kinds.indexOf("Read") > 0 && kinds.indexOf("As of") > 0 && kinds.indexOf("Watch") > 0,
    "every row offers Read, As of and Watch", JSON.stringify(kinds));
  check(page["wb-closest"]._text.indexOf("closest capture 20020120142510") === 0,
    "the answer says which capture is closest", page["wb-closest"]._text);
  check(page["wb-closest"]._text.indexOf("via memento timegate") > 0, "the answer says which door answered");
  check(page["wb-status"]._warned === false, "a kept search is not painted as a warning");
  console.log("[2] ALL PASS");

  /* [3] a search that could NOT run is not painted as an empty one ---------------------------- */
  console.log("[3] search: the index was busy");
  wire.reply = answer(searchFailed, 409);
  page["wb-go"].fire("click");
  await tick();
  check(page["wb-status"]._warned === true, "a failed search warns");
  check(page["wb-status"]._text.indexOf("cdx_timeout") > 0, "the status carries the error NAME",
    page["wb-status"]._text);
  check(page["wb-status"]._text.indexOf("narrow the ask") > 0, "the hint the server sent is shown");
  check(page["wb-status"]._text.indexOf("cdx_timeout") > 0 && page["wb-status"]._text.indexOf("failed") < 0,
    "the band never says the archive found nothing when it did not answer");
  check(page["wb-closest"]._text.indexOf("doors asked") === 0, "the per-door report is shown", page["wb-closest"]._text);
  console.log("[3] ALL PASS");

  /* [4] reading an archived copy: somebody else's page arrives as TEXT ------------------------ */
  console.log("[4] read: an archived page with a payload in it");
  wire.reply = answer(readOK);
  row.children[2].fire("click"); // the Read button, captured from the painted row
  await tick();
  const readPost = wire.calls[wire.calls.length - 1].body;
  check(readPost.action === "read" && readPost.target === "http://chatagent.ca/",
    "Read asks for the URL that row is about", JSON.stringify(readPost));
  check(page["wb-readwrap"].hidden === false, "the read box opens");
  check(page["wb-read"]._text.indexOf("onerror") > 0, "the page's own words are kept", page["wb-read"]._text);
  check(page["wb-readtitle"]._text.indexOf("20020120142510") > 0, "the box says which capture was read");
  check(INNER_HTML_VIOLATIONS.length === 0,
    "archived text went through a text node, never innerHTML", JSON.stringify(INNER_HTML_VIOLATIONS));
  console.log("[4] ALL PASS");

  /* [5] a field written from a result announces itself --------------------------------------- */
  console.log("[5] the As-of button");
  const before = page["wb-asof"].events.length;
  row.children[3].fire("click");
  check(page["wb-asof"].value === "20020120142510", "the date lands in the field", page["wb-asof"].value);
  check(page["wb-asof"].events.length === before + 1 && page["wb-asof"].events[before].type === "input",
    "the field is told, exactly as a keystroke would tell it");
  console.log("[5] ALL PASS");

  /* [6] an ask the operator has not made is not sent ----------------------------------------- */
  console.log("[6] no target");
  const sent = wire.calls.length;
  page["wb-target"].value = "   ";
  page["wb-go"].fire("click");
  await tick();
  check(wire.calls.length === sent, "nothing was fetched for an empty target");
  check(page["wb-status"]._warned === true, "the operator is told to give a page first");
  console.log("[6] ALL PASS");

  /* [7] clearing the log is undoable ---------------------------------------------------------- */
  console.log("[7] clear then undo");
  wire.reply = answer({ ok: true, action: "forget", cleared: 3, can_restore: true, card: card });
  page["wb-forget"].fire("click");
  await tick();
  check(wire.calls[wire.calls.length - 1].body.action === "forget", "Clear log asks the server to forget");
  check(page["wb-undo"].hidden === false, "Undo appears only because the server said it could restore");
  wire.reply = answer({ ok: true, action: "restore", restored: 3, card: card });
  page["wb-undo"].fire("click");
  await tick();
  check(wire.calls[wire.calls.length - 1].body.action === "restore", "Undo asks the server to restore");
  check(page["wb-undo"].hidden === true, "Undo hides again once it has done its job");
  console.log("[7] ALL PASS");

  /* [8] a console that is not there ---------------------------------------------------------- */
  console.log("[8] the server is gone");
  wire.reply = { network: "Failed to fetch" };
  page["wb-check"].fire("click");
  await tick();
  check(page["wb-status"]._warned === true, "a dead console warns");
  check(page["wb-status"]._text.indexOf("the console did not answer") > 0,
    "and says so in the operator's words", page["wb-status"]._text);
  check(page["wb-status"]._text.indexOf("Boot server") > 0, "and says what to do about it");
  console.log("[8] ALL PASS");

  console.log("TOTAL FAILURES: " + failures);
  process.exit(failures ? 1 : 0);
}

main().catch((e) => {
  console.log("  FAIL  the harness itself threw: " + (e && e.stack));
  console.log("TOTAL FAILURES: " + (failures + 1));
  process.exit(1);
});
