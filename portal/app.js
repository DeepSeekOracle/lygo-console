(function () {
  /* One product name, one build stamp. The header, the document title and every status line read
     from here, so the console cannot drift back into three names and no version in the UI. */
  const LYGO_PRODUCT = "LYGO Manager Agent Console";
  const LYGO_BUILD = "1.9.0";
  const BUILD_STAMP = "build " + LYGO_BUILD;
  /* The build stamp the console actually serves (/api/health), so a release bump cannot disagree with
     the header. Declared up HERE, above its first use, because paintBrand() reads it and paintBrand()
     runs at load: the same `let` declared further down the file sat in the temporal dead zone, threw
     "Cannot access 'servedBuild' before initialization", and killed this whole script - which the
     operator sees as a console stuck on "probing..." with an empty model picker. */
  let servedBuild = "";
  const TOKEN_KEY = "lygo_llm_token";
  function captureTokenFromUrl() {
    const u = new URL(location.href);
    const t = u.searchParams.get("t") || u.searchParams.get("token");
    if (t) {
      sessionStorage.setItem(TOKEN_KEY, t);
      u.searchParams.delete("t");
      u.searchParams.delete("token");
      window.history.replaceState({}, "", u.pathname + u.search);
    }
    if (typeof window.LYGO_TOKEN === "string" && window.LYGO_TOKEN) {
      sessionStorage.setItem(TOKEN_KEY, window.LYGO_TOKEN);
    }
  }
  captureTokenFromUrl();
  function token() {
    return sessionStorage.getItem(TOKEN_KEY) || window.LYGO_TOKEN || "";
  }
  function headers() {
    return { "Content-Type": "application/json", "X-LYGO-LLM-Token": token(), "Cache-Control": "no-store" };
  }
  let lastScan = -1;
  const healthEl = document.getElementById("health");
  const log = document.getElementById("log");
  const form = document.getElementById("form");
  const msg = document.getElementById("msg");
  const models = document.getElementById("models");
  const limb = document.getElementById("limb-out");
  const img = document.getElementById("img");
  const filePick = document.getElementById("file");
  const attachStrip = document.getElementById("attach-strip");
  const stopBtn = document.getElementById("stop");
  const sendBtn = document.getElementById("send");
  const emptyState = document.getElementById("empty-state");
  const statusOut = document.getElementById("status-out");
  const traceList = document.getElementById("trace-list");
  const traceCount = document.getElementById("trace-count");
  const ctxNote = document.getElementById("ctx-note");
  const compactNow = document.getElementById("compact-now");
  const archiveOpen = document.getElementById("archive-open");
  const compactStatus = document.getElementById("compact-status");
  const vaultList = document.getElementById("vault-list");
  const vaultStatus = document.getElementById("vault-status");
  const vaultView = document.getElementById("vault-view");
  const vaultViewTitle = document.getElementById("vault-view-title");
  const vaultViewMeta = document.getElementById("vault-view-meta");
  const vaultTranscript = document.getElementById("vault-transcript");
  const vaultFile = document.getElementById("vault-file");
  const vaultAdopt = document.getElementById("vault-adopt");
  const vaultRefreshBtn = document.getElementById("vault-refresh");
  const vaultSearch = document.getElementById("vault-search");
  const vaultQ = document.getElementById("vault-q");
  const vaultResumeBtn = document.getElementById("vault-resume");
  const vaultLabelBtn = document.getElementById("vault-label");
  const vaultClose = document.getElementById("vault-close");
  let vaultPick = null;
  let pendingImage = null;   // the last photo as the data URL the engine takes (kept for the send path)
  let pendingAttach = [];    // everything the operator picked: {kind:"image"|"text"|"file", name, size, …}
  const ATTACH_TEXT_MAX = 200000;  // a text file this big is read into the message; bigger is saved instead
  const IMG_MAX_PX = 1600;         // photos are shrunk before they go near the engine: a phone photo is
                                   // 3-8 MB and the preview + the upload choke on a mid machine otherwise

  function attachBytes(n) {
    n = Number(n) || 0;
    return n >= 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB";
  }

  /* Show what is attached. The picker used to take a photo and give no sign of it anywhere, so the
     button looked broken even when the file was held. */
  function renderAttach() {
    if (!attachStrip) return;
    attachStrip.hidden = !pendingAttach.length;
    attachStrip.innerHTML = "";
    pendingAttach.forEach(function (a, i) {
      const chip = document.createElement("span");
      chip.className = "chip";
      if (a.kind === "image" && a.dataUrl) {
        const t = document.createElement("img");
        t.src = a.dataUrl;
        t.alt = a.name;
        chip.appendChild(t);
      }
      const label = document.createElement("span");
      label.textContent = (a.kind === "image" ? "photo " : a.kind === "text" ? "text " : "file ")
        + a.name + " · " + attachBytes(a.size);
      chip.appendChild(label);
      const x = document.createElement("button");
      x.type = "button";
      x.className = "chip-x";
      x.textContent = "✕";
      x.title = "Remove this attachment";
      x.onclick = function () {
        pendingAttach.splice(i, 1);
        syncAttach();
      };
      chip.appendChild(x);
      attachStrip.appendChild(chip);
    });
    /* A model with no projector cannot see a photo: say it under the chips, where the operator is
       looking, instead of letting them wait for an answer that only ever read the text. */
    if (pendingAttach.some(function (a) { return a.kind === "image"; }) && lastHealth && lastHealth.vision === false) {
      const note = document.createElement("span");
      note.className = "attach-note";
      note.textContent = "\u26a0 " + (lastHealth.selected || "this model")
        + " cannot see pictures - attach it with File to keep it in the workspace, or boot a model with vision";
      attachStrip.appendChild(note);
    }
  }

  function syncAttach() {
    const last = pendingAttach.filter(function (a) { return a.kind === "image" && a.dataUrl; }).pop();
    pendingImage = last ? last.dataUrl : null;
    renderAttach();
  }

  function addImageFile(f) {
    const reader = new FileReader();
    reader.onload = function () {
      const raw = String(reader.result || "");
      const im = new Image();
      im.onload = function () {
        let dataUrl = raw;
        let w = im.width;
        let h = im.height;
        try {
          const scale = Math.min(1, IMG_MAX_PX / Math.max(im.width || 1, im.height || 1));
          const c = document.createElement("canvas");
          c.width = Math.max(1, Math.round((im.width || 1) * scale));
          c.height = Math.max(1, Math.round((im.height || 1) * scale));
          c.getContext("2d").drawImage(im, 0, 0, c.width, c.height);
          dataUrl = c.toDataURL("image/jpeg", 0.85);
          w = c.width;
          h = c.height;
        } catch (e) { /* a format the canvas will not re-encode: send it as it came */ }
        pendingAttach.push({ kind: "image", name: f.name, size: f.size, dataUrl: dataUrl, width: w, height: h });
        syncAttach();
        setStatus("attached " + f.name + (w && h ? " (" + w + "×" + h + ")" : "") + " — type your question and press Send", false);
      };
      im.onerror = function () {
        pendingAttach.push({ kind: "image", name: f.name, size: f.size, dataUrl: raw });
        syncAttach();
        setStatus("attached " + f.name + " — type your question and press Send", false);
      };
      im.src = raw;
    };
    reader.readAsDataURL(f);
  }

  function isTexty(f) {
    if (String(f.type || "").indexOf("text/") === 0) return true;
    return /\.(txt|md|markdown|json|jsonl|csv|tsv|log|ini|cfg|conf|ya?ml|toml|py|js|mjs|ts|tsx|jsx|html?|css|scss|sh|bat|ps1|sql|xml|svg|rst|srt|vtt)$/i
      .test(String(f.name || ""));
  }

  function addFile(f) {
    // A photo picked through the FILE button is still a photo: send it the way the img button does, so
    // the engine's projector sees it. Sent as a path instead, the agent holds a file it cannot look at.
    if (String(f.type || "").indexOf("image/") === 0
        || /\.(png|jpe?g|gif|bmp|webp|avif|tiff?)$/i.test(String(f.name || ""))) {
      addImageFile(f);
      return;
    }
    if (isTexty(f) && f.size <= ATTACH_TEXT_MAX) {
      const r = new FileReader();
      r.onload = function () {
        pendingAttach.push({ kind: "text", name: f.name, size: f.size, text: String(r.result || "") });
        syncAttach();
        setStatus("attached " + f.name + " — its text rides with your question", false);
      };
      r.readAsText(f);
      return;
    }
    setStatus("saving " + f.name + " where the agent can open it…", false);
    fetch("/api/upload", {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream", "X-Lygo-Filename": encodeURIComponent(f.name) },
      body: f,
    })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (j && j.path) {
          pendingAttach.push({ kind: "file", name: j.name || f.name, size: f.size, path: j.path });
          syncAttach();
          setStatus("saved " + (j.name || f.name) + " to the workspace — the agent can read it there", false);
        } else {
          setStatus("could not save " + f.name + ": " + ((j && j.error) || "unknown"), true);
        }
      })
      .catch(function (e) { setStatus("could not save " + f.name + ": " + e, true); });
  }
  let bootedOnce = false;
  let chatHistory = [];
  const HIST_KEY = "lygo_llm_chatHistory";
  const MAX_MSGS = 60;        /* messages this browser keeps in the request; the console trims by TOKENS */
  const MAX_IMAGES = 2;       /* base64 images kept in chatHistory — they are re-sent every turn */
  /* The reply cap. Seeded from the console's own config (console.json `max_tokens`, surfaced on
     /api/health) on the first health poll. The hardcoded 768 that used to live here is what every
     long answer was ACTUALLY cut at, mid-sentence: the browser asked for 768, so the engine stopped
     at exactly 768 tokens no matter what the server or the config said. The server clamps to the
     configured cap, so this value can lower an answer but never raise one past config. */
  let MAX_TOKENS = 4096;
  const MAX_TOKENS_FALLBACK = 4096;
  /* The page does not time out a chat turn. Hermes and the local engine finish when they
     finish. Stop is the operator's button, not a clock. */

  /* ---- footer status: its own element, so a trace dump can never erase it -------------- */
  let stickyStatus = "";
  function setStatus(text, warn) {
    if (!statusOut) return;
    statusOut.textContent = text;
    statusOut.classList.toggle("warn", !!warn);
  }
  /* A failed action stays on screen until the user does something about it. */
  function setStickyStatus(text) { stickyStatus = text || ""; setStatus(text, true); }
  function clearStickyStatus() { stickyStatus = ""; }
  function statusSoft(text) { if (!stickyStatus) setStatus(text, false); }

  /* ---- tool traces: compact list in their own collapsible pane ------------------------- */
  function summarizeArgs(args) {
    try {
      const s = JSON.stringify(args || {});
      if (!s || s === "{}" || s === "null") return "";
      return s.length > 72 ? s.slice(0, 69) + "…" : s;
    } catch (_) { return ""; }
  }
  function traceFailed(t) {
    const res = t && (t.result !== undefined ? t.result : t.output);
    if (res && typeof res === "object") return res.ok === false || !!res.error;
    return false;
  }
  function renderTraces(traces) {
    if (!traceList || !traces) return;
    const list = Array.isArray(traces) ? traces : [traces];
    list.forEach((t) => {
      const name = (t && (t.name || t.tool || t.fn || t.function)) || "tool";
      const args = (t && (t.arguments || t.args || t.parameters)) || {};
      const bad = traceFailed(t);
      const li = document.createElement("li");
      li.className = "trace-item " + (bad ? "fail" : "ok");
      const badge = document.createElement("span");
      badge.className = "trace-badge";
      badge.textContent = bad ? "fail" : "ok";
      const nm = document.createElement("b");
      nm.textContent = name;
      const ar = document.createElement("span");
      ar.className = "trace-args";
      ar.textContent = summarizeArgs(args);
      li.appendChild(badge);
      li.appendChild(nm);
      li.appendChild(ar);
      try { li.title = JSON.stringify(t).slice(0, 900); } catch (_) {}
      traceList.appendChild(li);
      while (traceList.children.length > 60) traceList.removeChild(traceList.firstChild);
    });
    if (traceCount) traceCount.textContent = String(traceList.children.length);
    const pane = document.getElementById("trace-pane");
    if (pane) pane.open = true;
  }
  function clearTraces() {
    if (traceList) traceList.innerHTML = "";
    if (traceCount) traceCount.textContent = "0";
  }

  /* ---- transcript helpers ------------------------------------------------------------- */
  function contentText(c) {
    if (typeof c === "string") return c;
    if (!c) return "";
    if (Object.prototype.toString.call(c) === "[object Array]") {
      return c.map((p) => {
        if (!p) return "";
        if (p.type === "image_url") return "[image]";
        return p.text || "";
      }).filter(Boolean).join("\n");
    }
    return JSON.stringify(c);
  }
  function clearTranscript() {
    if (!log) return;
    const kids = log.querySelectorAll(".bubble");
    for (let i = 0; i < kids.length; i++) log.removeChild(kids[i]);
    if (emptyState) emptyState.hidden = false;
    log.scrollTop = 0;
  }
  function renderHistory() {
    if (!log) return;
    const kids = log.querySelectorAll(".bubble");
    for (let i = 0; i < kids.length; i++) log.removeChild(kids[i]);
    if (emptyState) emptyState.hidden = chatHistory.length > 0;
    chatHistory.forEach((m) => {
      bubble(m.role === "user" ? "user" : "assistant", contentText(m.content));
    });
  }
  let lastPruneDropped = false;
  let restoredFromServer = 0;
  function pruneHistory(list) {
    /* Message window first, then images: only the newest MAX_IMAGES base64 payloads survive, or
       every turn re-sends the whole pile of them. */
    let h = list.slice(-MAX_MSGS);
    let kept = 0;
    for (let i = h.length - 1; i >= 0; i--) {
      const m = h[i];
      if (!m || Object.prototype.toString.call(m.content) !== "[object Array]") continue;
      const room = MAX_IMAGES - kept;
      const imgs = m.content.filter((p) => p && p.type === "image_url");
      let seen = 0;
      const next = [];
      m.content.forEach((p) => {
        if (!p || p.type !== "image_url") { next.push(p); return; }
        seen += 1;
        const fromEnd = imgs.length - seen;
        if (fromEnd < room) { kept += 1; next.push(p); }
        else lastPruneDropped = true;
      });
      const text = next.filter((p) => p && p.type !== "image_url" && p.text).map((p) => p.text).join("\n");
      m.content = next.length ? next : [{ type: "text", text: text || "[older image dropped from history]" }];
      if (!next.some((p) => p && p.type === "image_url") && !text) {
        m.content = "[older image dropped from history]";
      }
    }
    return h;
  }
  let lastPerf = null;   /* what llama.cpp reported for the last turn (passed through by the console) */

  function notePerf(perf) {
    /* The console forwards the engine's own timings, so the operator can see what a turn cost
       without benchmarking the kit. An older console sends no perf: then this stays silent. */
    if (!perf || typeof perf !== "object" || !(perf.gen_tok_s || perf.prompt_tok_s || perf.gen_tokens)) return;
    lastPerf = perf;
    paintCtx();
  }

  /* The gap between what the engine generated and what the operator was shown - the one number
     that makes every other speed claim falsifiable. Measured 2026-09-22 on this box: a two-word
     ask, "Reply with exactly: CACHE TEST", had the engine generate 137 tokens at 47.7 tok/s to
     display 10 characters - 2.87 s of a 3.2 s turn was text nobody saw, and no surface in the
     console could see it, which is why it went unnoticed for so long. */
  function gapBit(p) {
    if (!p || !p.gen_tokens) return "";
    if (typeof p.shown_tokens !== "number") return "";
    const surplus = typeof p.surplus_tokens === "number" ? p.surplus_tokens : 0;
    let s = "shown " + p.shown_tokens + "/" + p.gen_tokens;
    if (surplus > 0) s += " (" + surplus + " unseen, " + (p.surplus_pct || 0) + "%)";
    return s;
  }

  function perfBit() {
    const p = lastPerf;
    if (!p || !(p.gen_tok_s || p.prompt_tok_s)) return "";
    let s = " · last turn:";
    if (p.gen_tok_s) s += " " + p.gen_tok_s + " tok/s gen";
    if (p.prompt_tok_s) s += (p.gen_tok_s ? " /" : "") + " " + Math.round(p.prompt_tok_s) + " tok/s prefill";
    if (p.gen_tokens) s += " (" + p.gen_tokens + " token" + (p.gen_tokens === 1 ? "" : "s");
    if (p.engine_calls > 1) s += ", " + p.engine_calls + " engine calls";
    if (p.gen_tokens) s += ")";
    const gap = gapBit(p);
    if (gap) s += " · " + gap;
    return s;
  }

  /* ---- the conversation record ----------------------------------------------------------------
     The engine window is decided in TOKENS by the console, not by a message count here. So this line
     reports the console's own numbers when /api/compaction answers, and keeps the old local counters
     when it does not (an older console has no record route). Nothing is dropped any more: what leaves
     the window is stamped into a journal on disk that is sealed, indexed and searchable by the agent
     (recall_history) - so "window full" now means "the record carries it", not "the chat lost it". */
  let lastRecord = null;

  function bytesBit(n) {
    n = Number(n) || 0;
    if (n >= 1073741824) return (n / 1073741824).toFixed(2) + " GB";
    if (n >= 1048576) return (n / 1048576).toFixed(1) + " MB";
    if (n >= 1024) return Math.round(n / 1024) + " KB";
    return n + " B";
  }

  function recordBit() {
    const r = lastRecord;
    if (!r || !r.ok) return "";
    let s = " · record: " + (r.turns_total || 0) + " turns stamped";
    if (r.turns_compacted) s += ", " + r.turns_compacted + " folded";
    if (r.turns_sealed) s += ", " + r.turns_sealed + " sealed";
    if (r.last_save_iso) s += " · saved " + String(r.last_save_iso).replace("T", " ").slice(0, 16);
    return s;
  }

  function paintRecord() {
    if (!compactStatus) return;
    const r = lastRecord;
    if (!r || !r.ok) {
      compactStatus.textContent = "record: —";
      compactStatus.className = "compact-status";
      return;
    }
    const w = r.window || {};
    const pct = Number(w.used_pct) || 0;
    compactStatus.textContent =
      "window " + pct + "% · journal " + bytesBit(r.journal_bytes) +
      " · sealed " + (r.sessions_sealed || 0) + " (" + bytesBit(r.sealed_bytes) + ")" +
      " · checkpoints " + (r.checkpoints || 0);
    compactStatus.className = "compact-status " + (pct >= 78 ? "warn" : "ok");
    compactStatus.title =
      "session " + (r.session_id || "—") + "\n" +
      "turns live " + (r.turns_live || 0) + " of " + (r.turns_total || 0) + " in the record\n" +
      "window " + (w.live_tokens || 0) + " / " + (w.history_tokens || 0) + " tokens (auto-compact at " +
      (w.auto_compact_pct || 78) + "%)\n" +
      "journal " + (r.paths ? r.paths.journal : "") + "\n" +
      "archive " + (r.paths ? r.paths.archive : "");
  }

  async function refreshRecord() {
    /* Server truth, never a guess: if the route is missing the line simply stays blank. */
    try {
      const r = await fetch("/api/compaction", { headers: headers(), cache: "no-store" });
      if (r.ok) {
        const d = await r.json();
        lastRecord = d && d.ok ? d : null;
      }
    } catch (e) { /* keep whatever we had */ }
    paintRecord();
    paintCtx();
  }

  /* ---- the session vault: what every finished chat became --------------------------------- */
  function vaultMeta(r) {
    const bits = [];
    if (r.turns) bits.push(r.turns + " turns");
    if (r.created_iso) bits.push(String(r.created_iso).replace("T", " ").slice(0, 16));
    if (r.tags && r.tags.length) bits.push(r.tags.join(", "));
    if (r.zip_bytes) bits.push(bytesBit(r.zip_bytes));
    return bits.join(" · ");
  }

  function paintVault(d) {
    if (!vaultList) return;
    const rows = (d && d.sessions) || [];
    const hits = (d && d.hits) || [];
    /* Two shapes arrive here: catalog rows, and transcript search hits (a phrase found inside a
       session). Both render as the same list, or the panel would lie about finding nothing. */
    const items = rows.length
      ? rows.map((r) => ({
        sid: r.sid,
        title: (r.pinned ? "★ " : "") + (r.title || r.sid || "?"),
        meta: vaultMeta(r),
        folder: r.folder,
      }))
      : hits.map((h) => ({
        sid: h.sid,
        title: "“" + (h.title || h.sid) + "”",
        meta: (h.where === "transcript" ? "in the transcript" : "in the catalog") + " · " +
          (h.turns || 0) + " turns · " + String(h.iso || "").slice(0, 16),
        folder: h.folder,
      }));
    vaultList.innerHTML = "";
    if (!items.length) {
      const li = document.createElement("li");
      li.className = "hint";
      li.textContent = (d && (d.count || d.q))
        ? "no session matches"
        : "nothing filed yet — press File this chat, or File old history";
      vaultList.appendChild(li);
      if (vaultStatus) vaultStatus.textContent = "vault empty · " + ((d && d.vault) || "");
      return;
    }
    items.forEach(function (it) {
      const li = document.createElement("li");
      li.className = "vault-row";
      const open = document.createElement("button");
      open.type = "button";
      open.textContent = it.title;
      open.title = (it.sid || "") + "  ·  " + (it.folder || "");
      open.onclick = function () { openVaultSession(it.sid); };
      const side = document.createElement("small");
      side.textContent = it.meta;
      li.appendChild(open);
      li.appendChild(side);
      vaultList.appendChild(li);
    });
    if (vaultStatus) {
      vaultStatus.textContent = rows.length
        ? ("filed " + (d.count || 0) + " sessions · " + (d.turns || 0) + " turns vaulted · " +
           bytesBit(d.bytes || 0))
        : (items.length + " session(s) matched “" + (d.q || "") + "”");
    }
  }

  async function refreshVault(q) {
    if (!vaultList) return;
    try {
      const url = q
        ? "/api/sessions?q=" + encodeURIComponent(q) + "&limit=60"
        : "/api/sessions?limit=60";
      const r = await fetch(url, { headers: headers(), cache: "no-store" });
      const d = await r.json().catch(() => ({}));
      if (!d || !d.ok) {
        if (vaultStatus) vaultStatus.textContent = "vault unavailable on this console";
        return;
      }
      paintVault(d);
      /* Nothing matched the titles or tags: look inside the transcripts before giving up. */
      if (q && !((d.sessions || []).length)) {
        const hits = await vaultPost({ action: "search", q: q, k: 20 });
        if (hits && hits.ok && (hits.hits || []).length) paintVault(hits);
      }
    } catch (e) { /* the panel keeps whatever it had */ }
  }

  async function openVaultSession(sid) {
    try {
      const r = await fetch("/api/sessions?sid=" + encodeURIComponent(sid) + "&chars=20000",
        { headers: headers(), cache: "no-store" });
      const d = await r.json().catch(() => ({}));
      if (!d || !d.ok) { setStatus("vault: " + ((d && d.error) || r.status), true); return; }
      vaultPick = d.sid;
      if (vaultView) vaultView.hidden = false;
      if (vaultViewTitle) vaultViewTitle.textContent = (d.pinned ? "★ " : "") + (d.title || d.sid);
      if (vaultViewMeta) {
        vaultViewMeta.textContent = d.sid + " · " + d.turns + " turns · " +
          String(d.created_iso || "").slice(0, 16) + " → " + String(d.ended_iso || "").slice(0, 16) +
          (d.tags && d.tags.length ? " · " + d.tags.join(", ") : "") +
          (d.note ? " · " + d.note : "") +
          (d.truncated ? " · shown head+tail of the file" : "") +
          (d.folder ? "\n" + d.folder : "");
      }
      if (vaultTranscript) vaultTranscript.value = d.transcript_text || "";
    } catch (e) {
      setStatus("vault: " + (e && e.message ? e.message : e), true);
    }
  }

  async function vaultPost(body) {
    try {
      const r = await fetch("/api/sessions", { method: "POST", headers: headers(), body: JSON.stringify(body) });
      return await r.json().catch(() => ({}));
    } catch (e) {
      return { ok: false, error: String(e && e.message ? e.message : e) };
    }
  }

  async function saveAndCompact() {
    if (!compactNow) return;
    compactNow.classList.add("busy");
    compactNow.disabled = true;
    const was = compactStatus ? compactStatus.textContent : "";
    if (compactStatus) { compactStatus.textContent = "stamping…"; compactStatus.className = "compact-status"; }
    let line;
    try {
      /* No messages in the body: the console already journals every turn, and a body carrying two
         base64 images can blow past the route's read cap for no gain. */
      const r = await fetch("/api/compaction", {
        method: "POST", headers: headers(), body: JSON.stringify({ action: "save" }),
      });
      const d = await r.json().catch(() => ({}));
      const cp = (d && d.checkpoint) || {};
      if (d && d.ok) {
        lastRecord = d.status && d.status.ok ? d.status : lastRecord;
        line = "saved " + (cp.turns || 0) + " turns · stamped " + String(d.at || "").replace("T", " ").slice(0, 19);
        setStatus(line, false);
      } else {
        line = "save failed: " + ((d && (d.error || (d.compact && d.compact.error))) || r.status);
        setStatus(line, true);
      }
    } catch (e) {
      line = "save failed: " + (e && e.message ? e.message : e);
      setStatus(line, true);
    }
    compactNow.classList.remove("busy");
    compactNow.disabled = false;
    if (compactStatus) {
      compactStatus.textContent = line;
      compactStatus.className = "compact-status " + (line.indexOf("failed") === 0 ? "warn" : "ok");
    }
    /* Do not leave the button in "stamping…" if the record refresh is slow. */
    await Promise.race([
      refreshRecord(),
      new Promise(function (resolve) { setTimeout(resolve, 6000); }),
    ]);
    if (compactStatus && line) { compactStatus.textContent = line; compactStatus.className = "compact-status " + (line.indexOf("failed") === 0 ? "warn" : "ok"); }
    if (was && compactStatus && !lastRecord) compactStatus.textContent = line;
  }

  function paintCtx(dropped) {
    if (!ctxNote) return;
    const msgN = chatHistory.length;
    const imgN = chatHistory.reduce((n, m) => n + (Object.prototype.toString.call(m.content) === "[object Array]" ? m.content.filter((p) => p && p.type === "image_url").length : 0), 0);
    const r = lastRecord;
    if (r && r.ok) {
      const w = r.window || {};
      const pct = Number(w.used_pct) || 0;
      ctxNote.textContent =
        "context: " + msgN + " messages sent · live ~" + (w.live_tokens || 0) + "/" + (w.history_tokens || 0) +
        " tokens (" + pct + "% of " + (w.ctx || 0) + " ctx)" +
        " · images kept " + imgN + "/" + MAX_IMAGES + " · max_tokens " + MAX_TOKENS +
        recordBit() +
        (w.will_compact_next_turn ? " · auto-compact next turn" : "") +
        perfBit();
      ctxNote.classList.toggle("warn", pct >= 78);
      return;
    }
    /* Older console (no /api/compaction): the local message window is all we know. */
    const full = msgN >= MAX_MSGS;
    ctxNote.textContent = "context: " + msgN + "/" + MAX_MSGS + " messages · images kept " + imgN + "/" + MAX_IMAGES + " · max_tokens " + MAX_TOKENS +
      (dropped || full ? " · history window full — older turns are dropped from what the engine sees" : "") +
      perfBit();
    ctxNote.classList.toggle("warn", !!(dropped || full));
  }
  function fillComposerIfEmpty(text) {
    /* Workspace clicks used to overwrite whatever the operator had typed. */
    if (!msg) return false;
    if ((msg.value || "").trim()) {
      setStatus("left your draft alone — the folder is open in the Workspace panel", false);
      return false;
    }
    msg.value = text;
    return true;
  }
  function paintBrand() {
    const nameEl = document.getElementById("brand-name");
    const buildEl = document.getElementById("brand-build");
    if (nameEl) nameEl.textContent = LYGO_PRODUCT;
    const stamp = servedBuild || BUILD_STAMP;   /* health build once the console answers; else the page fallback */
    if (buildEl) buildEl.textContent = stamp;
    if (typeof document.title === "string") document.title = LYGO_PRODUCT + " · " + stamp;
  }
  paintBrand();

  document.querySelectorAll("[data-prompt]").forEach((btn) => {
    btn.onclick = function () {
      if (!msg) return;
      msg.value = btn.getAttribute("data-prompt") || "";
      msg.focus();
    };
  });

  function setHealth(j) {
    const err = j.error ? " err=" + j.error : "";
    const hz = j.hermes || {};
    const cloud = j.cloud && j.cloud.mode === "api"
      ? " hermes=API " + (hz.model || j.cloud.model || "")
      : " hermes=LOCAL " + (hz.model || j.selected || "");
    const hoff = j.fallback && j.fallback.why && j.cloud && j.cloud.degraded ? " handoff=" + j.fallback.why : "";
    healthEl.textContent =
      `build=${j.build || "?"} engine=${j.brain || "?"} selected=${j.selected || "—"} models=${j.scan_n || 0} limbs=${(j.tools||[]).length} up=${j.engine_present} ram=${Math.round((j.ram_avail || 0) / 1e9)}GB${cloud}${hoff}${err}`;
  }

  let healthDown = false;

/* --- the standalone Boot server button ---------------------------------------------------------
   The page is served BY the server it would be asking to start, and a browser cannot spawn a process,
   so the button rings the doorbell instead: tools/doorbell.py, on its own port, whose whole job is to run
   LYGO_LLM_CONSOLE.bat. The console hands this page the doorbell's port and token while it is alive; the
   token is also kept in localStorage so a reload *after* a crash can still ring. Nothing here pretends:
   if the doorbell is not listening, the button says what to run instead. */
let doorbell = { port: 0, token: "", up: false, url: "" };
try {
  const saved = JSON.parse(localStorage.getItem("lygo-doorbell") || "null");
  if (saved && saved.token) doorbell = Object.assign(doorbell, saved);
} catch (_) {}
async function refreshDoorbell() {
  try {
    const r = await fetch("/api/doorbell", { headers: headers(), cache: "no-store" });
    if (!r.ok) throw new Error(String(r.status));
    const j = await r.json();
    if (j && j.port) {
      doorbell = Object.assign(doorbell, j);
      try { localStorage.setItem("lygo-doorbell", JSON.stringify({ port: j.port, token: j.token || doorbell.token })); } catch (_) {}
    }
    return doorbell;
  } catch (_) { return doorbell; }
}
async function bootServer() {
  const btn = document.getElementById("server-boot");
  const say = (t) => { if (btn) btn.textContent = t; };
  if (!doorbell.port || !doorbell.token) { try { await refreshDoorbell(); } catch (_) {} }
  if (!doorbell.port || !doorbell.token) {
    const _dport = Number(location.port || doorbell.port || 9641) - 1;
    setStatus("no doorbell token yet \u2014 open http://127.0.0.1:" + _dport + "/ and press \"Boot the console\" there, or run LYGO_LLM_CONSOLE.bat", true);
    return;
  }
  say("Ringing\u2026");
  let rang = false;
  try {
    let r = await fetch("http://127.0.0.1:" + doorbell.port + "/boot?token=" + encodeURIComponent(doorbell.token) + "&t=" + Date.now(),
                        { mode: "cors", cache: "no-store" });
    rang = r.ok;
    if (!r.ok) {
      /* The doorbell refused it, for the one honest reason: the token this page cached is no longer its own
         (it was restarted, or the cache was written before a fix). Forget it, learn the current one while the
         console still answers, ring once more - and if it will still not take it, say so. Polling a dead
         server while the ring had already been refused is how this cost 88 seconds in silence. */
      try { localStorage.removeItem("lygo-doorbell"); } catch (_) {}
      doorbell.token = "";
      await refreshDoorbell();
      if (doorbell.token) {
        r = await fetch("http://127.0.0.1:" + doorbell.port + "/boot?token=" + encodeURIComponent(doorbell.token) + "&t=" + Date.now(),
                        { mode: "cors", cache: "no-store" });
        rang = r.ok;
      }
      if (!rang) {
        say("Boot server");
        setStatus("the doorbell refused the ring (stale token) \u2014 open http://127.0.0.1:"
                  + (Number(location.port || 9641) - 1) + "/ and press \"Boot the console\" there", true);
        return;
      }
    }
  } catch (_) { /* unreachable doorbell: the polling below decides, and says so if it never answers */ }
  const t0 = Date.now();
  while (Date.now() - t0 < 180000) {
    await new Promise((r) => setTimeout(r, 1500));
    try {
      const r = await fetch("/api/health", { cache: "no-store", headers: headers() });
      if (r.ok) { say("Back up"); location.reload(); return; }
    } catch (_) {}
    say("Starting… " + Math.round((Date.now() - t0) / 1000) + "s");
  }
  say("Boot server");
  setStatus("the doorbell rang but the console did not come up \u2014 read save/logs/doorbell.log", true);
}
document.addEventListener("click", (e) => {
  const t = e.target && e.target.closest ? e.target.closest("#server-boot") : null;
  if (t) { e.preventDefault(); bootServer(); }
});
  async function refreshHealth() {
    try {
      const r = await fetch("/api/health", { headers: headers() });
      if (!r.ok) {
        healthEl.textContent = "health " + r.status + (r.status === 401 ? " unauthorized — open the console from the launcher link that carries the token" : "");
        if (r.status === 401 && !stickyStatus) {
          setStickyStatus("unauthorized (401) — this page has no token; reopen the console from its launcher link (?token=…), which sets it for this tab");
        }
        return null;
      }
      const j = await r.json();
      lastHealth = j;
      /* The console's configured reply cap, so the page asks for what the config says instead of
         carrying its own constant that no config change could ever move. */
      if (j && Number(j.max_tokens) > 0) MAX_TOKENS = Number(j.max_tokens);
    if (j && j.build) { servedBuild = String(j.build); paintBrand(); }
      setHealth(j);
      healthDown = false;
      /* While the console answers, learn where the doorbell is. After a crash this page can no longer
         ask anything - it is served by the thing that died - so this is the one moment it can be told. */
      if (!doorbell.port) { refreshDoorbell().catch(() => {}); }
      if (j.cloud) paintApi(j.cloud);
      document.body.dataset.brain = j.brain || "";
      if (typeof j.scan_n === "number" && j.scan_n !== lastScan) {
        lastScan = j.scan_n;
        try { await refreshModels(); } catch (_) {}
      }
      return j;
    } catch (e) {
      healthEl.textContent = "health failed — console not reachable";
      if (!healthDown) {
        healthDown = true;
        statusSoft("console not reachable (/api/health failed) — press Boot server to restart it, or run LYGO_LLM_CONSOLE.bat");
      const _sb = document.getElementById("server-boot");
      if (_sb) { _sb.classList.add("need"); }
      }
      return null;
    }
  }
  async function refreshModels() {
    const r = await fetch("/api/models", { headers: headers(), cache: "no-store" });
    if (!r.ok) {
      setStatus("model list " + r.status + " — press Scan drives in the header", true);
      const o = document.createElement("option");
      o.textContent = "(scan failed " + r.status + ")";
      o.value = "";
      models.innerHTML = "";
      models.appendChild(o);
      return;
    }
    const j = await r.json();
    models.innerHTML = "";
    const list = j.models || [];
    if (!list.length) {
      const o = document.createElement("option");
      o.textContent = "(none — click Scan drives)";
      o.value = "";
      models.appendChild(o);
      return;
    }
    list.forEach((m) => {
      const o = document.createElement("option");
      o.value = m.id;
      const gb = m.bytes ? (m.bytes / 1e9).toFixed(1) + "GB" : "";
      /* A travelling kit must not list a model it does not hold: reach.portable means the files sit
         inside storage this kit carries. Reachable-but-not-portable runs on the machine the stick is
         plugged into today and is gone on the next one, so say which is which. */
      /* A drive that is not plugged in is not a deleted file. The server names which one this is
         (reach.label), because "not found here" is what both cases used to say - L10. */
      const where = m.reach && !m.reach.portable
        ? (m.reach.reachable ? " . not carried by this kit" : " . " + (m.reach.label || "not found here"))
        : "";
      /* One weights file can carry several names (a LYGO turbo variant is the same GGUF under another
         name), and the operator asked to see every model on this machine - so the other names ride
         along here instead of vanishing from the list. */
      const also = (m.also_known_as && m.also_known_as.length) ? " · also: " + m.also_known_as.join(", ") : "";
      /* What it can actually DO, as measured by the checker (src/model_check.py): text, coder,
         image-in, image-out, sound, embed. A model the checker proved too large for this host says so
         HERE, in the choice box, instead of failing once it is picked. */
      const caps = (m.caps && m.caps.length) ? " " + m.caps.join("+") : "";
      const chk = (m.checked && m.checked.verdict === "too_large") ? " too-big-here"
                : (m.checked && m.checked.verdict === "runs" && m.checked.gen_tps) ? ` ${m.checked.gen_tps}tok/s` : "";
      o.textContent = `${m.id} [${m.kind || "?"}${caps} ${m.runnable ? "ok" : m.status} ${gb}${where}${chk}]${also}`;
      if (m.reach && m.reach.why) o.title = m.reach.why;
      if (m.id === j.selected) o.selected = true;
      models.appendChild(o);
    });
  }
  function pictureNames(text) {
    const s = String(text || "");
    const out = [];
    const seen = {};
    const add = function (n) {
      const name = String(n || "").split(/[\\/]/).pop();
      if (!name || seen[name]) return;
      seen[name] = 1;
      out.push(name);
    };
    let m;
    const inFolder = /workspace[\\/]+images[\\/]+([A-Za-z0-9._-]+\.(?:png|jpe?g|gif|webp|bmp))/gi;
    while ((m = inFolder.exec(s))) add(m[1]);
    const stamped = /\b(gen-\d{8}-\d{6}\.(?:png|jpe?g|gif|webp|bmp))\b/gi;
    while ((m = stamped.exec(s))) add(m[1]);
    return out.slice(0, 6);
  }
  function mediaSrc(name) {
    return "/api/media/image/" + encodeURIComponent(name);
  }
  function bubbleTextEl(el) {
    if (!el) return null;
    return el.querySelector(".bubble-text") || el;
  }
  function getBubbleText(el) {
    const t = el && el.querySelector(".bubble-text");
    return t ? (t.textContent || "") : ((el && el.textContent) || "");
  }
  function setBubbleText(el, s) {
    if (!el) return;
    let t = el.querySelector(".bubble-text");
    if (!t) {
      t = document.createElement("div");
      t.className = "bubble-text";
      el.insertBefore(t, el.firstChild);
    }
    t.textContent = s == null ? "" : String(s);
  }
  function paintBubbleLinks(el, text) {
    const t = bubbleTextEl(el);
    if (!t) return;
    const src = text == null ? "" : String(text);
    const re = /((?:[A-Za-z]:[\\/][^\n`"'<>]*?[\\/])?workspace[\\/]+images[\\/]+[A-Za-z0-9._-]+\.(?:png|jpe?g|gif|webp|bmp)|\bgen-\d{8}-\d{6}\.(?:png|jpe?g|gif|webp|bmp)\b)/gi;
    t.textContent = "";
    let last = 0, m;
    while ((m = re.exec(src))) {
      if (m.index > last) t.appendChild(document.createTextNode(src.slice(last, m.index)));
      const name = m[0].split(/[\\/]/).pop();
      const a = document.createElement("a");
      a.className = "bubble-pic-link";
      a.href = mediaSrc(name);
      a.target = "_blank";
      a.rel = "noopener";
      a.textContent = m[0];
      t.appendChild(a);
      last = m.index + m[0].length;
    }
    if (last < src.length) t.appendChild(document.createTextNode(src.slice(last)));
    if (!t.childNodes.length) t.textContent = src;
  }
  function finishBubblePictures(el) {
    if (!el) return;
    const text = getBubbleText(el);
    paintBubbleLinks(el, text);
    el.querySelectorAll(".bubble-pic").forEach(function (n) { n.parentNode.removeChild(n); });
    pictureNames(text).forEach(function (name) {
      const wrap = document.createElement("a");
      wrap.className = "bubble-pic";
      wrap.href = mediaSrc(name);
      wrap.target = "_blank";
      wrap.rel = "noopener";
      wrap.title = "Open " + name;
      const im = document.createElement("img");
      im.className = "bubble-img gen";
      im.src = mediaSrc(name);
      im.alt = name;
      wrap.appendChild(im);
      el.appendChild(wrap);
    });
    if (log) log.scrollTop = log.scrollHeight;
  }
  function bubble(role, text, images) {
    const d = document.createElement("div");
    d.className = "bubble " + role;
    const t = document.createElement("div");
    t.className = "bubble-text";
    t.textContent = text || "";
    d.appendChild(t);
    (images || []).forEach(function (src) {
      const im = document.createElement("img");
      im.className = "bubble-img";
      im.src = src;
      im.alt = "attached photo";
      d.appendChild(im);
    });
    if (role === "assistant") finishBubblePictures(d);
    log.appendChild(d);
    if (emptyState) emptyState.hidden = true;
    log.scrollTop = log.scrollHeight;
    return d;
  }
  function bubbleError(text) {
    const d = document.createElement("div");
    d.className = "bubble assistant error";
    d.textContent = text;
    log.appendChild(d);
    if (emptyState) emptyState.hidden = true;
    log.scrollTop = log.scrollHeight;
    return d;
  }
  let activeAbort = null;   /* AbortController for the answer in flight */
  function stopStream(why) {
    if (!activeAbort) return;
    activeAbort.__stopped = true;
    try { activeAbort.abort(); } catch (_) {}
    setStatus(why || "stopped by user", true);
  }
  function markBrain(el, evn) {
    if (!el || !evn) return;
    const active = evn.active || (evn.brain === "cloud" ? "cloud" : "local");
    const isApi = active === "cloud" || active === "api";
    el.classList.remove("via-api", "via-local");
    el.classList.add(isApi ? "via-api" : "via-local");
    if (evn.brain === "hermes") {
      el.title = "HERMES · " + (isApi ? "API" : "LOCAL") + (evn.model ? " · " + evn.model : "");
    }
    const why = evn.fallback && evn.fallback.why ? evn.fallback.why : "";
    el.classList.toggle("takeover", !!why);
    if (why) el.title = "API handoff: " + why;
  }

  async function boot(id) {
    setStatus("booting " + (id || "selected") + " … first load can take a minute");
    limb.textContent = "booting " + (id || "selected") + " …";
    let r;
    try {
      r = await fetch("/api/boot", {
        method: "POST",
        headers: headers(),
        body: JSON.stringify({ id: id || models.value }),
      });
    } catch (e) {
      setStatus("boot request failed — " + ((e && e.message) ? e.message : e), true);
      return null;
    }
    const j = await r.json().catch(function () { return {}; });
    limb.textContent = JSON.stringify(j, null, 2);
    if (!r.ok) setStatus("boot failed — " + (j.error || ("HTTP " + r.status)), true);
    else setStatus("boot: " + (j.brain || j.status || "asked") + " · engine=" + ((j.state && j.state.brain) || lastHealth.brain || "?"));
    await refreshHealth();
    return j;
  }

  document.getElementById("scan").onclick = async () => {
    setStatus("scanning drives…");
    limb.textContent = "scanning…";
    let r;
    try {
      /* Ask for the WIDE scan. The kit's own roots are cheap but only ever re-find what was already
         listed, so the button the operator presses is the one that walks the drives and reads any
         store it finds there. The reply says whether it came from the cache and how old that is,
         rather than implying a fresh walk of every disk. */
      r = await fetch("/api/scan", {
        method: "POST",
        headers: headers(),
        body: JSON.stringify({ all_drives: true, per_root_s: 25 }),
      });
    } catch (e) {
      setStatus("scan failed — " + ((e && e.message) ? e.message : e), true);
      return;
    }
    const j = await r.json().catch(function () { return {}; });
    const all = j.models || (j.registry && j.registry.models) || [];
    const n = all.length;
    const runnable = all.filter(function (m) { return m && m.runnable; }).length;
    limb.textContent = JSON.stringify(
      {
        n: n,
        runnable_here: runnable,
        cached: !!j.cached,
        cache_age_s: j.age_s === undefined ? null : j.age_s,
        truncated: j.scan_truncated,
        seconds: j.seconds,
        roots: j.roots,
        selected: (j.registry || {}).selected,
      },
      null,
      2
    );
    setStatus(
      r.ok
        ? ("scan found " + n + " model(s), " + runnable + " runnable here" +
           (j.cached ? " (cached, " + j.age_s + "s old)" : "") +
           (j.scan_truncated ? " · list truncated" : "") + " — pick one and press Boot LLM")
        : ("scan failed — " + (j.error || r.status)),
      !r.ok
    );
    await refreshModels();
    await refreshHealth();
  };
  document.getElementById("select").onclick = async () => {
    await boot(models.value);
  };
  const apiProv = document.getElementById("api-provider");
  const apiKey = document.getElementById("api-key");
  const apiModel = document.getElementById("api-model");
  const apiUrl = document.getElementById("api-url");
  const apiStatus = document.getElementById("api-status");
  const brainStatus = document.getElementById("brain-status");
  const brainLocalBtn = document.getElementById("brain-local");
  const brainApiBtn = document.getElementById("brain-api");
  let brainMode = "local"; /* local (default) | api */
  let lastHealth = {};
  let brainBusy = false;

  function localLabel() {
    return (models && models.value) || (lastHealth && lastHealth.selected) || "engine";
  }
  function paintBrain(j) {
    const cloud = j || lastHealth.cloud || {};
    brainMode = cloud.mode === "api" ? "api" : "local";
    const hasKey = !!cloud.has_key;
    if (brainLocalBtn) {
      brainLocalBtn.classList.toggle("on", brainMode === "local");
      brainLocalBtn.setAttribute("aria-pressed", brainMode === "local" ? "true" : "false");
      brainLocalBtn.disabled = brainBusy;
      brainLocalBtn.title = "Local GGUF engine on this PC — the default, always complete";
    }
    if (brainApiBtn) {
      brainApiBtn.classList.toggle("on", brainMode === "api");
      brainApiBtn.classList.toggle("degraded", !!cloud.degraded);
      brainApiBtn.setAttribute("aria-pressed", brainMode === "api" ? "true" : "false");
      /* No key = no API brain. Say it on the control instead of letting the press do nothing. */
      brainApiBtn.disabled = brainBusy || !hasKey;
      brainApiBtn.setAttribute("aria-disabled", brainApiBtn.disabled ? "true" : "false");
      brainApiBtn.classList.toggle("needs-key", !hasKey);
      brainApiBtn.title = hasKey
        ? "Run the chat on the cloud API. The local engine stays booted as the safety net."
        : "add key first — paste your API key in the row below, press Save key, then press API.";
    }
    document.body.classList.toggle("api-on", brainMode === "api");
    document.body.dataset.brainMode = brainMode;
    if (brainStatus && !stickyStatus) {
      if (brainBusy) {
        brainStatus.textContent = "switching brain…";
      } else if (brainMode === "api") {
        const hz = lastHealth.hermes || {};
        brainStatus.textContent =
          "HERMES · API · " + (hz.model || cloud.model || "") +
          (cloud.degraded ? " · API died, local answering" : " · local GGUF is the fallback");
      } else {
        const hz = lastHealth.hermes || {};
        brainStatus.textContent = "HERMES · LOCAL · " + (hz.model || localLabel()) +
          (hasKey ? " · API ready as fallback" : " · add key below for API rails");
      }
      brainStatus.classList.toggle("warn", !!cloud.degraded || !hasKey);
    }
    if (apiStatus) {
      if (cloud.degraded) {
        apiStatus.textContent =
          "API handoff " + (cloud.last_code ? "HTTP " + cloud.last_code + " · " : "") + (cloud.last_error || "") +
          " — local answered (" + (cloud.handoffs || 0) + " handoffs). Press API to try the API again.";
      } else if (brainBusy) {
        apiStatus.textContent = "switching brain — waiting on the console…";
      } else if (brainMode === "api") {
        apiStatus.textContent = "API ON · " + (cloud.label || "") + " / " + (cloud.model || "") + " · local engine stays booted as the safety net";
      } else if (cloud.has_key) {
        apiStatus.textContent = "API key saved · press the API segment above to run the chat on the cloud (local is the default)";
      } else {
        apiStatus.textContent = "API: off (local default) — paste a key and press Save key to enable the API brain";
      }
    }
    if (apiKey) apiKey.placeholder = cloud.has_key ? "key saved on this PC — paste to replace" : "paste key then Save key";
  }
  function fillApiModels(provider) {
    const list = document.getElementById("api-models");
    const src = lastHealth && lastHealth.cloud && lastHealth.cloud.providers;
    if (!list || !src) return;
    const p = src[provider] || {};
    list.innerHTML = (p.models || []).map(function (m) { return "<option value=\"" + String(m).replace(/"/g, "&quot;") + "\"></option>"; }).join("");
  }

  function paintApi(j) {
    if (!j) return;
    if (apiProv && j.provider) apiProv.value = j.provider;
    fillApiModels((apiProv && apiProv.value) || j.provider);
    if (apiModel && j.model) apiModel.value = j.model;
    if (apiUrl) {
      apiUrl.hidden = j.provider !== "custom";
      if (j.provider === "custom" && j.url) apiUrl.value = j.url;
    }
    paintBrain(j);
  }
  async function postCloud(body) {
    const r = await fetch("/api/cloud", { method: "POST", headers: headers(), body: JSON.stringify(body) });
    const j = await r.json().catch(function () { return {}; });
    if (!r.ok && !j.error) j.error = "HTTP " + r.status;
    if (!r.ok) j.ok = false;
    paintApi(j);
    await refreshHealth();
    return j;
  }
  async function refreshCloud() {
    const r = await fetch("/api/cloud", { headers: headers(), cache: "no-store" });
    const j = await r.json().catch(function () { return {}; });
    if (!r.ok) j.error = j.error || ("HTTP " + r.status);
    paintApi(j);
    return j;
  }
  async function postBrain(mode) {
    if (brainBusy) return {};
    brainBusy = true;
    clearStickyStatus();
    setStatus("switching brain to " + mode.toUpperCase() + "…");
    paintBrain(lastHealth.cloud || {}); /* applies the busy/disabled state straight away */
    try {
      const r = await fetch("/api/brain", { method: "POST", headers: headers(), body: JSON.stringify({ mode: mode }) });
      const j = await r.json().catch(function () { return {}; });
      if (!r.ok || j.error) {
        const why = j.hint || j.error || ("HTTP " + r.status);
        /* Sticky: the next 3s health refresh must not wipe the reason off the screen. */
        setStickyStatus("cannot switch to " + mode.toUpperCase() + ": " + why + " (still on " + brainMode.toUpperCase() + ")");
        if (brainStatus) {
          brainStatus.textContent = "cannot switch to " + mode + ": " + why;
          brainStatus.classList.add("warn");
        }
        return j;
      }
      paintApi(j.cloud || {});
      await refreshHealth();
      setStatus("brain = " + j.mode + " · local engine " + (j.brain || "?") + " · selected " + (j.selected || "—"));
      return j;
    } catch (e) {
      setStickyStatus("cannot switch to " + mode.toUpperCase() + ": " + ((e && e.message) ? e.message : e) + " (still on " + brainMode.toUpperCase() + ")");
      return {};
    } finally {
      brainBusy = false;
      paintBrain(lastHealth.cloud || {});
    }
  }
  if (brainLocalBtn) brainLocalBtn.onclick = function () { clearStickyStatus(); postBrain("local"); };
  if (brainApiBtn) {
    brainApiBtn.onclick = async function () {
      clearStickyStatus();
      const j = await refreshCloud();
      if (!j || !j.has_key) {
        setStickyStatus("add key first: paste the API key in the row below, press Save key, then press the API segment");
        if (apiKey) apiKey.focus();
        return;
      }
      postBrain("api");
    };
  }
  const brainBootApi = document.getElementById("brain-boot-api");
  if (brainBootApi) brainBootApi.onclick = function () {
    const target = document.getElementById("api-boot");
    if (target) target.click();
  };
  const brainBootBtn = document.getElementById("brain-boot");
  if (brainBootBtn) brainBootBtn.onclick = function () { boot(models.value); };

  async function resetSession(label) {
    /* The transcript lives on the server: only clear it after the server agrees it is gone. */
    const text = label || "New chat";
    const ss = document.getElementById("session-status");
    if (ss) { ss.textContent = text + " · resetting the server session…"; ss.className = "hint"; }
    try {
      const r = await fetch("/api/session", { method: "POST", headers: headers(), body: JSON.stringify({ new: true }) });
      const j = await r.json().catch(function () { return {}; });
      if (!r.ok || j.ok === false) throw new Error(j.error || ("HTTP " + r.status));
      chatHistory = [];
      clearTranscript();
      clearTraces();
      try { sessionStorage.removeItem(HIST_KEY); } catch (_) {}
      clearStickyStatus();
      const cm = (lastHealth.cloud && lastHealth.cloud.model) || "";
      setStatus(text + " · server session reset · now on " + (brainMode === "api" ? "API (" + cm + ")" : "LOCAL (" + localLabel() + ")"));
      if (ss) { ss.textContent = text + " · reset complete · new server session is live"; ss.className = "hint"; }
      paintCtx(false);
      if (msg) msg.focus();
      return true;
    } catch (e) {
      setStickyStatus(text + " failed: " + ((e && e.message) ? e.message : e) + " — nothing was cleared, the engine may still hold the old context");
      if (ss) { ss.textContent = text + " failed — " + ((e && e.message) ? e.message : e); ss.className = "hint warn"; }
      return false;
    }
  }
  function confirmNewChat(what) {
    return confirm("Start a " + what + "?\n\nThe whole transcript is cleared and the server-side session is reset. This cannot be undone.");
  }
  const brainNewBtn = document.getElementById("brain-newchat");
  if (brainNewBtn) {
    brainNewBtn.onclick = async function () {
      if (!confirmNewChat("new chat")) return;
      await resetSession("New chat");
    };
  }
  const apiBootBtn = document.getElementById("api-boot");
  if (apiBootBtn) {
    apiBootBtn.onclick = async function () {
      clearStickyStatus();
      const provider = (apiProv && apiProv.value) || "";
      const model = (apiModel && apiModel.value.trim()) || "";
      if (!provider || !model) { setStickyStatus("pick a provider and a model first"); return; }
      const body = { enabled: true, provider: provider, model: model };
      if (apiKey && apiKey.value.trim()) body.key = apiKey.value.trim();
      if (provider === "custom" && apiUrl && apiUrl.value.trim()) body.url = apiUrl.value.trim();
      try {
        setStatus("saving API route " + provider + " \u00b7 " + model + "\u2026");
        const r = await fetch("/api/cloud", { method: "POST", headers: headers(), body: JSON.stringify(body) });
        const saved = await r.json().catch(function () { return {}; });
        if (!r.ok || saved.error) { setStickyStatus("save failed: " + (saved.error || ("HTTP " + r.status))); return; }
        paintApi(saved);
        const boot = { mode: "api", provider: provider, model: model };
        if (apiKey && apiKey.value.trim()) boot.key = apiKey.value.trim();
        const br = await fetch("/api/brain", { method: "POST", headers: headers(), body: JSON.stringify(boot) });
        const j = await br.json().catch(function () { return {}; });
        if (!br.ok || j.error) {
          setStickyStatus("API boot failed: " + (j.hint || j.error || ("HTTP " + br.status)));
          return;
        }
        paintApi(j.cloud || {});
        await refreshHealth();
        setStickyStatus("API booted: " + provider + " \u00b7 " + model + " \u2014 local engine kept as the safety net.");
      } catch (e) {
        setStickyStatus("API boot error: " + ((e && e.message) ? e.message : e));
      }
    };
  }

  const apiOffBtn = document.getElementById("api-off");
  if (apiOffBtn) apiOffBtn.onclick = function () { clearStickyStatus(); postBrain("local"); };
  if (apiProv) {
    apiProv.onchange = async function () {
      clearStickyStatus();
      if (apiUrl) apiUrl.hidden = apiProv.value !== "custom";
      fillApiModels(apiProv.value);
      const defs = { gemini: "gemini-2.0-flash", deepseek: "deepseek-chat", groq: "openai/gpt-oss-20b", openai: "gpt-4o-mini", xai: "grok-2-latest", nvidia: "meta/llama-3.2-11b-vision-instruct", "forge-nvidia": "nvidia/nemotron-3-ultra-550b-a55b", opencode: "opencode/deepseek-v4.1-flash-free", ofm: "kilo-auto/free" };
      if (apiModel && defs[apiProv.value]) apiModel.value = defs[apiProv.value];
      await postCloud({ provider: apiProv.value, model: apiModel && apiModel.value, url: apiUrl && apiUrl.value });
    };
  }
  const apiBtn = document.getElementById("api-connect");
  if (apiBtn) {
    apiBtn.onclick = async function () {
      clearStickyStatus();
      const body = {
        enabled: brainMode === "api", /* saving a key must not silently switch the brain */
        provider: apiProv && apiProv.value,
        model: apiModel && apiModel.value,
        url: apiUrl && apiUrl.value,
      };
      if (apiKey && apiKey.value) body.key = apiKey.value;
      apiBtn.disabled = true;
      setStatus("saving API key…");
      try {
        const j = await postCloud(body);
        if (apiKey) apiKey.value = "";
        if (j.has_key) {
          setStatus("Key saved for " + (j.label || "") + " / " + (j.model || "") + " · press the API segment in the brain switch to run on the cloud. Local stays the default.");
        } else {
          setStickyStatus("Save key failed — " + (j.error || j.hint || "paste a key in the row above first"));
        }
      } catch (e) {
        setStickyStatus("Save key failed — " + ((e && e.message) ? e.message : e));
      } finally {
        apiBtn.disabled = false;
        paintBrain(lastHealth.cloud || {});
      }
    };
  }
  if (apiKey) {
    apiKey.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter") { ev.preventDefault(); if (apiBtn) apiBtn.click(); }
    });
  }
  refreshCloud().catch(function () {});
  if (img) {
    img.onchange = () => {
      const f = img.files && img.files[0];
      if (f) addImageFile(f);
      img.value = "";   // clearing it lets the same file be picked again after a removal
    };
  }
  if (filePick) {
    filePick.onchange = () => {
      const f = filePick.files && filePick.files[0];
      if (f) addFile(f);
      filePick.value = "";
    };
  }
  if (stopBtn) stopBtn.onclick = function () { stopStream("stopped by user — the answer above may be incomplete"); };
  function setSending(on) {
    if (stopBtn) stopBtn.disabled = !on;
    if (sendBtn) sendBtn.disabled = !!on;
  }
  function failBubble(b, text) {
    if (b) {
      b.classList.add("failed");
      const cur = getBubbleText(b).trim();
      setBubbleText(b, cur ? cur + "\n\n⚠ " + text : "⚠ " + text);
    }
    setStatus(text, true);
    if (msg) msg.focus();
  }

  async function sendChat() {
    const text = (msg && msg.value ? msg.value : "").trim();
    if (!text && !pendingAttach.length) return;
    if (activeAbort) {
      setStatus("still answering — press Stop before sending again", true);
      return;
    }
    if (msg) msg.value = "";
    const parts = [];
    if (text) parts.push({ type: "text", text: text });
    pendingAttach.forEach(function (a) {
      if (a.kind === "text") {
        // Inlined on purpose: the agent reads it in the same breath as the question, so a small file
        // needs no limb call and cannot be "forgotten" between turns.
        parts.push({
          type: "text",
          text: "Attached file " + a.name + " (" + attachBytes(a.size) + "), its contents follow:\n```\n"
            + a.text + "\n```",
        });
      } else if (a.kind === "file") {
        parts.push({
          type: "text",
          text: "Attached file " + a.name + " (" + attachBytes(a.size) + ") is saved in the workspace as:\n"
            + a.path + "\nOpen it with the read_file limb before you answer, and say plainly if you cannot.",
        });
      } else if (a.kind === "image" && a.dataUrl) {
        parts.push({ type: "image_url", image_url: { url: a.dataUrl } });
      }
    });
    const content = parts.length === 1 && parts[0].type === "text" ? parts[0].text
      : (parts.length ? parts : text);
    const shownImages = pendingAttach.filter(function (a) { return a.kind === "image" && a.dataUrl; })
      .map(function (a) { return a.dataUrl; });
    const shownNames = pendingAttach.map(function (a) { return a.name; });
    pendingAttach = [];
    syncAttach();
    if (img) img.value = "";
    if (filePick) filePick.value = "";
    bubble("user", text || "[" + (shownNames.join(", ") || "attachment") + "]", shownImages);
    chatHistory.push({ role: "user", content });
    const beforeN = chatHistory.length;
    lastPruneDropped = false;
    chatHistory = pruneHistory(chatHistory);
    paintCtx(lastPruneDropped || beforeN > MAX_MSGS);

    const b = bubble("assistant", "");
    b.classList.add("streaming");
    const ctl = new AbortController();
    activeAbort = ctl;
    setSending(true);
    let stopped = false, parseFails = 0, done = false;
    const say = function (delta) {
      if (!delta) return;
      setBubbleText(b, getBubbleText(b) + delta);
      log.scrollTop = log.scrollHeight;
    };
    try {
      const h = await refreshHealth();
      const apiOn = brainMode === "api";
      if (!apiOn && h && h.brain !== "ready" && h.brain !== "booting") {
        setStatus("local engine is " + (h.brain || "down") + " — booting the selected model, then asking…");
        await boot(models.value);
      }
      const body = {
        messages: chatHistory,
        model: models.value || undefined,
        tools: document.getElementById("tools").checked,
        stream: true,
        max_tokens: MAX_TOKENS,
        use_api: apiOn,
      };
      const r = await fetch("/api/chat", {
        method: "POST",
        headers: headers(),
        body: JSON.stringify(body),
        signal: ctl.signal,
      });
      if (!r.ok) {
        const j = await r.json().catch(function () { return {}; });
        const why = j.error || j.hint || ("HTTP " + r.status);
        failBubble(b, "engine error " + r.status + " — " + why +
          (r.status === 401
            ? " · this page has no token: reopen the console from its launcher link"
            : " · press Scan drives then Boot LLM, and check the health box"));
        return;
      }
      const ctype = r.headers.get("content-type") || "";
      if (ctype.includes("event-stream") && r.body && r.body.getReader) {
        const reader = r.body.getReader();
        const dec = new TextDecoder();
        let buf = "";
        const handle = function (raw) {
          const line = raw.replace(/^data:\s*/, "").trim();
          if (!line) return;
          try {
            const evn = JSON.parse(line);
            if (evn.delta) say(evn.delta);
            /* The turn streamed and the finished text differs from what arrived as deltas
               (sanitised echo, or the output-window gate): this frame is the authoritative one. */
            if (evn.replace) setBubbleText(b, evn.replace);
            if (evn.traces) renderTraces(evn.traces);
            if (evn.type === "brain") markBrain(b, evn);
            if (evn.error) failBubble(b, "engine error — " + evn.error);
            if (evn.type === "done") {
              markBrain(b, evn);
              notePerf(evn.perf);
              refreshRecord(); /* the turn just changed the record: journal, rollup, maybe a seal */
              if (evn.perf && evn.perf.gen_tok_s) {
                b.title = "gen " + evn.perf.gen_tok_s + " tok/s · prefill " + Math.round(evn.perf.prompt_tok_s || 0) + " tok/s";
              }
              done = true;
              const doneText = getBubbleText(b);
              if (doneText) chatHistory.push({ role: "assistant", content: doneText });
              finishBubblePictures(b);
            }
          } catch (_) {
            /* A malformed event used to vanish silently and the answer just stopped mid-sentence. */
            parseFails += 1;
          }
        };
        while (true) {
          const chunk = await reader.read();
          if (chunk.done) break;
          buf += dec.decode(chunk.value, { stream: true });
          const parts = buf.split("\n\n");
          buf = parts.pop();
          for (const p of parts) handle(p);
        }
        handle(buf); /* a last event that never got its blank-line terminator */
      } else {
        const j = await r.json().catch(function () { return {}; });
        if (j.error && !j.text) {
          failBubble(b, "engine error — " + j.error);
        } else {
          setBubbleText(b, j.text || "");
          markBrain(b, j);
          notePerf(j.perf);
          refreshRecord(); /* the turn just changed the record */
          if (j.text) chatHistory.push({ role: "assistant", content: j.text });
          finishBubblePictures(b);
        }
        if (j.traces) renderTraces(j.traces);
      }
    } catch (e) {
      stopped = !!ctl.__stopped;
      if (stopped) {
        b.classList.add("stopped");
        setBubbleText(b, getBubbleText(b) + "\n[stopped by user]");
      } else if (e && e.name === "AbortError") {
        b.classList.add("stopped");
      } else {
        failBubble(b, "chat request failed — " + ((e && e.message) ? e.message : e) +
          " · is the console window still running? (answers are kept in the transcript)");
      }
      const partial = getBubbleText(b).replace(/\[stopped by user\]/g, "").replace(/⚠[^\n]*/g, "").trim();
      if (!done && partial && stopped) {
        chatHistory.push({ role: "assistant", content: partial + " [answer was cut short]" });
      }
    } finally {
      if (parseFails) {
        b.classList.add("failed");
        setBubbleText(b, getBubbleText(b) + "\n⚠ stream error — " + parseFails + " malformed event(s) skipped, so this answer may be incomplete.");
        setStatus("stream error — " + parseFails + " malformed event(s) from the engine", true);
      }
      b.classList.remove("streaming");
      if (!getBubbleText(b).trim() && !b.classList.contains("failed") && !b.classList.contains("stopped")) {
        /* The old failure mode: an empty bubble and a rejection in the console. */
        b.classList.add("failed");
        setBubbleText(b, "⚠ the engine sent no text for this turn — press Boot LLM (health box says engine=ready when it is up) and ask again.");
        setStatus("engine returned no text for this turn", true);
      }
      if (getBubbleText(b).trim() && !b.classList.contains("failed")) finishBubblePictures(b);
      activeAbort = null;
      setSending(false);
      chatHistory = pruneHistory(chatHistory);
      paintCtx(false);
      await persistHistory();
      await refreshHealth();
    }
  }

  form.onsubmit = function (ev) {
    ev.preventDefault();
    sendChat();
  };

  let wsBrowse = "";
  async function refreshWorkspace() {
    const ul = document.getElementById("ws");
    const box = document.getElementById("ws-mounts");
    const st = document.getElementById("ws-status");
    const q = wsBrowse ? ("?path=" + encodeURIComponent(wsBrowse)) : "";
    let r, j = {};
    try {
      r = await fetch("/api/workspace" + q, { headers: headers(), cache: "no-store" });
      j = await r.json().catch(function () { return {}; });
    } catch (e) {
      if (st) st.textContent = "workspace unreachable — " + ((e && e.message) ? e.message : e);
      return;
    }
    if (!r.ok || j.ok === false) {
      /* 403 "denied" used to render as an empty panel, which reads exactly like "no folders". */
      const why = j.error || ("HTTP " + r.status);
      const where = wsBrowse || j.path || "that path";
      if (ul) ul.innerHTML = "";
      if (box) {
        box.innerHTML = "";
        const row = document.createElement("div");
        row.className = "ws-mount ws-error";
        row.textContent = r.status === 403
          ? "denied: " + where + " is not inside a mapped root — add it under Add access first"
          : "workspace error " + r.status + " · " + why + (j.hint ? " · " + j.hint : "");
        box.appendChild(row);
      }
      if (st) {
        st.textContent = r.status === 403 ? ("denied (403) · " + where) : ("workspace " + r.status + " · " + why);
        st.classList.add("warn");
      }
      return;
    }
    if (st) st.classList.remove("warn");
    if (box) {
      box.innerHTML = "";
      (j.mounts || []).forEach((m) => {
        const row = document.createElement("div");
        row.className = "ws-mount";
        const p = document.createElement("span");
        p.className = "p";
        p.textContent = m.path;
        p.title = (m.label || "") + " · " + (m.source || "") + " — click to browse this folder in the panel";
        p.onclick = () => {
          /* Browse in the panel. This used to type "list_dir <path>" over whatever was in the composer. */
          wsBrowse = m.path;
          refreshWorkspace();
        };
        row.appendChild(p);
        ["read", "write", "search"].forEach((k) => {
          const t = document.createElement("span");
          t.className = "tag " + (m[k] ? "on" : "off");
          t.textContent = k === "search" ? "find" : k;
          t.title = (m[k] ? "allowed: " : "not allowed: ") + k + " (" + m.path + ")";
          t.setAttribute("aria-label", k + (m[k] ? " allowed" : " not allowed"));
          row.appendChild(t);
        });
        if (m.pinned) {
          const pin = document.createElement("span");
          pin.className = "tag on";
          pin.textContent = "pin";
          row.appendChild(pin);
        } else if (m.status !== "revoked") {
          const rm = document.createElement("button");
          rm.type = "button";
          rm.textContent = "Remove";
          rm.onclick = async () => {
            let rr;
            try {
              rr = await fetch("/api/workspace", {
                method: "POST",
                headers: headers(),
                body: JSON.stringify({ action: "remove", path: m.path }),
              });
            } catch (e) {
              setStatus("remove failed — " + ((e && e.message) ? e.message : e), true);
              return;
            }
            const rj = await rr.json().catch(function () { return {}; });
            if (!rr.ok || rj.ok === false) {
              setStatus("remove failed — " + (rj.error || ("HTTP " + rr.status)), true);
              return;
            }
            wsBrowse = "";
            refreshWorkspace();
          };
          row.appendChild(rm);
        }
        if (m.status === "revoked") {
          const t = document.createElement("span");
          t.className = "tag off";
          t.textContent = "off";
          row.appendChild(t);
        }
        box.appendChild(row);
      });
    }
    if (st) {
      st.textContent = (j.n_live || 0) + " live maps · browsing " + (j.path || "") +
        (j.truncated || (j.n && j.entries && j.n > j.entries.length)
          ? " · showing first " + (j.entries || []).length + " of " + (j.n || "?")
          : "");
    }
    if (!ul) return;
    ul.innerHTML = "";
    (j.entries || []).forEach((e) => {
      const li = document.createElement("li");
      li.textContent = (e.dir ? "📁 " : "📄 ") + e.name;
      li.onclick = () => {
        if (e.dir) {
          wsBrowse = e.path || ((j.path || "") + "\\" + e.name);
          refreshWorkspace();
          fillComposerIfEmpty("list_dir " + wsBrowse);
        } else {
          fillComposerIfEmpty("Read file " + (e.path || e.name) + " and summarize.");
        }
        if (msg) msg.focus();
      };
      ul.appendChild(li);
    });
  }
  const wsAdd = document.getElementById("ws-add");
  if (wsAdd) {
    wsAdd.onclick = async () => {
      const path = (document.getElementById("ws-path") || {}).value || "";
      const st = document.getElementById("ws-status");
      wsAdd.disabled = true;
      setStatus("adding workspace access to " + (path || "…"));
      try {
        const r = await fetch("/api/workspace", {
          method: "POST",
          headers: headers(),
          body: JSON.stringify({
            action: "add",
            path,
            read: !!(document.getElementById("ws-read") || {}).checked,
            write: !!(document.getElementById("ws-write") || {}).checked,
            search: !!(document.getElementById("ws-search") || {}).checked,
          }),
        });
        const j = await r.json().catch(() => ({}));
        if (!r.ok || j.ok === false) {
          const why = "failed: " + (j.error || ("HTTP " + r.status)) + (j.hint ? " · " + j.hint : "");
          if (st) st.textContent = why;
          setStatus("add access " + why, true);
          return;
        }
        if (st) st.textContent = "added " + (j.path || path);
        setStatus("workspace access added: " + (j.path || path));
        wsBrowse = j.path || path;
        refreshWorkspace();
      } catch (e) {
        setStatus("add access failed — " + ((e && e.message) ? e.message : e), true);
      } finally {
        wsAdd.disabled = false;
      }
    };
  }
  async function refreshLimbs() {
    const box = document.getElementById("limbs");
    if (!box) return;
    const r = await fetch("/api/tools", { headers: headers(), cache: "no-store" });
    const j = await r.json().catch(() => ({}));
    box.innerHTML = "";
    (j.names || []).forEach((n) => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = n;
      b.onclick = async () => {
        let args = {};
        if (n === "web_search") args = { q: prompt("search q") || "LYGO" };
        else if (n === "web_fetch") args = { url: prompt("https url") || "https://chatagent.ca/" };
        else if (n === "list_dir") args = { path: "." };
        else if (n === "shell") args = { cmd: prompt("workspace command") || "dir" };
        else if (n === "now" || n === "whoami" || n === "kernel_status" || n === "todo_list" || n === "notepad_list" || n === "skill_list" || n === "self_check" || n === "steward_map") args = {};
        else {
          msg.value = "Use tool " + n + " as needed: ";
          msg.focus();
          return;
        }
        const res = await fetch("/api/limb", { method: "POST", headers: headers(), body: JSON.stringify({ name: n, arguments: args }) });
        const out = await res.json().catch(function () { return {}; });
        limb.textContent = JSON.stringify(out, null, 2);
        const bad = !res.ok || out.ok === false || !!out.error;
        setStatus("limb " + n + (bad ? " failed — " + (out.error || ("HTTP " + res.status)) : " finished"), bad);
        if (out.traces) renderTraces(out.traces);
      };
      box.appendChild(b);
    });
  }
  async function persistHistory() {
    /* sessionStorage is only this tab's draft cache. The server session is the transcript of
       record (see loadContinuity), so a failure here has to be visible. */
    try {
      sessionStorage.setItem(HIST_KEY, JSON.stringify(chatHistory.slice(-40)));
    } catch (_) {}
    try {
      const r = await fetch("/api/session", { method: "POST", headers: headers(), body: JSON.stringify({ messages: chatHistory }) });
      if (!r.ok) setStatus("server session save failed (" + r.status + ") — this transcript is only in this tab now", true);
    } catch (e) {
      setStatus("server session save failed — " + ((e && e.message) ? e.message : e) + " · transcript is only in this tab", true);
    }
  }
  async function loadContinuity() {
    /* Server-side truth: GET /api/session decides what the transcript is, so a second tab cannot
       disagree with the engine about the conversation. sessionStorage is the draft cache. */
    try {
      const s = await fetch("/api/session", { headers: headers(), cache: "no-store" });
      const j = await s.json().catch(function () { return {}; });
      if (!s.ok) throw new Error("HTTP " + s.status);
      const msgs = Object.prototype.toString.call(j.messages) === "[object Array]" ? j.messages : [];
      if (msgs.length) {
        chatHistory = msgs.slice(-40).map((m) => ({ role: m.role === "user" ? "user" : "assistant", content: m.content }));
        restoredFromServer = msgs.length;
        renderHistory();
        statusSoft("session restored from the engine · " + msgs.length + " messages");
      } else {
        let draft = [];
        try {
          const raw = sessionStorage.getItem(HIST_KEY);
          if (raw) draft = JSON.parse(raw) || [];
        } catch (_) { draft = []; }
        if (draft.length) {
          chatHistory = draft;
          renderHistory();
          statusSoft("no server session yet — restored this tab's draft (" + draft.length + " messages); it is saved on the next send");
        } else {
          chatHistory = [];
          renderHistory();
        }
      }
    } catch (e) {
      /* Keep the draft rather than showing an empty transcript, but say the server was not read. */
      setStickyStatus("could not read the engine's session — " + ((e && e.message) ? e.message : e) + " · the transcript below may be this tab's draft only");
      try {
        const raw = sessionStorage.getItem(HIST_KEY);
        if (raw && !chatHistory.length) {
          chatHistory = JSON.parse(raw) || [];
          renderHistory();
        }
      } catch (_) {}
    }
    paintCtx(false);
    try {
      const soul = await (await fetch("/api/soul", { headers: headers(), cache: "no-store" })).json();
      const ident = await (await fetch("/api/identity", { headers: headers(), cache: "no-store" })).json();
      const mem = await (await fetch("/api/memory", { headers: headers(), cache: "no-store" })).json();
      const se = document.getElementById("soul-edit");
      const ie = document.getElementById("id-edit");
      const me = document.getElementById("mem-edit");
      if (se) se.value = soul.text || "";
      if (ie) ie.value = ident.text || "";
      if (me) me.value = mem.memory_md || "";
      const st = document.getElementById("cont-status");
      if (st) st.textContent = "SOUL " + ((soul.text || "").length) + "c · ID " + ((ident.text || "").length) + "c · MEMORY " + ((mem.memory_md || "").length) + "c";
    } catch (_) {}
  }
  const contTabs = Array.prototype.slice.call(document.querySelectorAll("[data-cont]"));
  function selectContTab(btn, focus) {
    contTabs.forEach(function (b) {
      const on = b === btn;
      b.classList.toggle("on", on);
      /* Complete the tab pattern the markup declares: role=tab needs aria-selected + roving tabindex. */
      b.setAttribute("aria-selected", on ? "true" : "false");
      b.tabIndex = on ? 0 : -1;
      const pane = document.getElementById("pane-" + b.getAttribute("data-cont"));
      if (pane) pane.hidden = !on;
    });
    if (focus && btn) btn.focus();
  }
  contTabs.forEach(function (btn, i) {
    btn.onclick = function () { selectContTab(btn, false); };
    btn.onkeydown = function (ev) {
      const k = ev.key;
      if (k !== "ArrowRight" && k !== "ArrowLeft" && k !== "Home" && k !== "End") return;
      ev.preventDefault();
      let next = i;
      if (k === "ArrowRight") next = (i + 1) % contTabs.length;
      if (k === "ArrowLeft") next = (i - 1 + contTabs.length) % contTabs.length;
      if (k === "Home") next = 0;
      if (k === "End") next = contTabs.length - 1;
      selectContTab(contTabs[next], true);
    };
  });
  async function saveCont(kind) {
    const se = document.getElementById("soul-edit");
    const ie = document.getElementById("id-edit");
    const me = document.getElementById("mem-edit");
    const st = document.getElementById("cont-status");
    const path = kind === "soul" ? "/api/soul" : kind === "id" ? "/api/identity" : "/api/memory";
    const text = kind === "soul" ? (se && se.value) : kind === "id" ? (ie && ie.value) : (me && me.value);
    const r = await fetch(path, { method: "POST", headers: headers(), body: JSON.stringify({ text }) });
    const j = await r.json().catch(() => ({}));
    if (st) st.textContent = j.ok ? (kind + " saved") : ("save failed " + (j.error || r.status));
  }
  const ss = document.getElementById("soul-save");
  if (ss) ss.onclick = () => saveCont("soul");
  const ids = document.getElementById("id-save");
  if (ids) ids.onclick = () => saveCont("id");
  const ms = document.getElementById("mem-save");
  if (ms) ms.onclick = () => saveCont("mem");
  const ns = document.getElementById("new-session");
  if (ns) {
    ns.onclick = async function () {
      if (!confirmNewChat("new session")) return;
      await resetSession("New session");
    };
  }
  /* The save button on a game, for a chat: stamp what has been said, fold what left the window. */
  if (compactNow) compactNow.onclick = saveAndCompact;
  /* ---- session vault wiring ---------------------------------------------------------------- */
  if (vaultRefreshBtn) vaultRefreshBtn.onclick = () => refreshVault(vaultQ ? vaultQ.value.trim() : "");
  if (vaultSearch) vaultSearch.onclick = () => refreshVault(vaultQ ? vaultQ.value.trim() : "");
  if (vaultQ) {
    vaultQ.onkeydown = function (ev) {
      if (ev.key === "Enter") { ev.preventDefault(); refreshVault(vaultQ.value.trim()); }
    };
  }
  if (vaultFile) {
    vaultFile.onclick = async function () {
      vaultFile.disabled = true;
      const d = await vaultPost({ action: "vault_live" });
      vaultFile.disabled = false;
      if (d && d.ok) {
        setStatus("filed to the vault: " + (d.title || d.sid || "") + " · " + (d.turns || 0) +
          " turns · " + (d.folder || ""), false);
      } else {
        setStatus("vault: " + ((d && (d.error || d.skipped)) || "could not file"), !(d && d.ok));
      }
      await refreshVault();
    };
  }
  if (vaultAdopt) {
    vaultAdopt.onclick = async function () {
      vaultAdopt.disabled = true;
      const d = await vaultPost({ action: "adopt" });
      vaultAdopt.disabled = false;
      setStatus(d && d.ok
        ? ("filed older history: " + (d.count || 0) + " sessions into " + (d.vault || "the vault"))
        : ("vault: " + ((d && d.error) || "could not file older history")), !(d && d.ok));
      await refreshVault();
    };
  }
  if (vaultResumeBtn) {
    vaultResumeBtn.onclick = async function () {
      if (!vaultPick) return;
      vaultResumeBtn.disabled = true;
      const d = await vaultPost({ action: "resume", sid: vaultPick });
      vaultResumeBtn.disabled = false;
      if (d && d.ok) {
        chatHistory = (d.messages || []).map((m) => ({
          role: m.role === "user" ? "user" : "assistant", content: m.content,
        }));
        renderHistory();
        try { sessionStorage.setItem(HIST_KEY, JSON.stringify(pruneHistory(chatHistory))); } catch (_) {}
        setStatus("reopened “" + (d.title || vaultPick) + "” (" + (d.turns || 0) +
          " turns) — this console is continuing that thread now", false);
        await refreshVault();
        await refreshRecord();
      } else {
        setStatus("vault: " + ((d && d.error) || "could not reopen that session"), true);
      }
    };
  }
  if (vaultLabelBtn) {
    vaultLabelBtn.onclick = async function () {
      if (!vaultPick) return;
      const title = prompt("Name this session", (vaultViewTitle && vaultViewTitle.textContent) || "");
      if (title === null) return;
      const tags = prompt("Tags, comma separated", "");
      if (tags === null) return;
      const d = await vaultPost({ action: "label", sid: vaultPick, title: title, tags: tags });
      if (d && d.ok) {
        setStatus("named: " + (d.title || "") + (d.tags && d.tags.length ? " · " + d.tags.join(", ") : ""), false);
        if (vaultViewTitle) vaultViewTitle.textContent = (d.pinned ? "★ " : "") + (d.title || vaultPick);
        await openVaultSession(vaultPick);
      } else {
        setStatus("vault: " + ((d && d.error) || "could not name it"), true);
      }
      await refreshVault();
    };
  }
  if (vaultClose) vaultClose.onclick = function () { if (vaultView) vaultView.hidden = true; vaultPick = null; };
  refreshVault();
  if (archiveOpen) {
    archiveOpen.onclick = async function () {
      try {
        const r = await fetch("/api/archive", { headers: headers(), cache: "no-store" });
        const d = await r.json();
        const list = (d && d.sessions) || [];
        const lines = list.slice(0, 12).map((s) => "  " + (s.sid || "?") + "  " + (s.turns || 0) + " turns  " + bytesBit(s.zip_bytes) + "  " + String(s.sealed_iso || "").slice(0, 19));
        const head = "record on disk\n  archive: " + ((d && d.archive) || "") + "\n  index:   " + ((d && d.index) || "") + "\n  sealed sessions: " + list.length + " · " + bytesBit((d && d.bytes) || 0);
        setStatus(head + (lines.length ? "\n" + lines.join("\n") : "\n  (nothing sealed yet — the live journal holds it all)"), false);
      } catch (e) {
        setStatus("archive: " + (e && e.message ? e.message : e), true);
      }
    };
  }
  refreshRecord();
  const wsr = document.getElementById("ws-refresh");
  if (wsr) wsr.onclick = refreshWorkspace;

  let skillRows = [];

  function skillIsUnity(s) {
    const slug = String((s && s.slug) || "");
    const source = String((s && s.source) || "");
    return source === "unity" || /unity/i.test(slug);
  }

  function paintSkillRows() {
    const box = document.getElementById("skills-list");
    const count = document.getElementById("skills-count");
    if (!box) return;
    const q = String((document.getElementById("skills-local") || {}).value || "").trim().toLowerCase();
    const unityN = skillRows.filter(skillIsUnity).length;
    const shown = skillRows.filter((s) => {
      if (!q) return true;
      const blob = (s.slug + " " + (s.description || "") + " " + (s.source || "")).toLowerCase();
      return blob.indexOf(q) >= 0;
    });
    if (count) {
      count.textContent = skillRows.length + " skills · " + unityN + " unity · showing " + shown.length;
    }
    box.innerHTML = "";
    shown.forEach((s) => {
      const row = document.createElement("label");
      row.className = "skill-row" + (skillIsUnity(s) ? " unity" : "");
      const ck = document.createElement("input");
      ck.type = "checkbox";
      ck.checked = !!s.enabled;
      ck.onchange = async () => {
        await fetch("/api/skills", {
          method: "POST",
          headers: headers(),
          body: JSON.stringify({ action: ck.checked ? "enable" : "disable", slug: s.slug }),
        });
        s.enabled = ck.checked;
      };
      const name = document.createElement("span");
      name.className = "sn";
      name.textContent = s.slug;
      name.title = s.description || "";
      name.onclick = (ev) => {
        ev.preventDefault();
        msg.value = "Invoke skill " + s.slug + " — read the SKILL.md and follow it.";
        msg.focus();
      };
      const src = document.createElement("span");
      src.className = "src";
      src.textContent = s.source || "";
      row.appendChild(ck);
      row.appendChild(name);
      row.appendChild(src);
      box.appendChild(row);
    });
  }

  async function refreshSkills() {
    const box = document.getElementById("skills-list");
    if (!box) return;
    const r = await fetch("/api/skills", { headers: headers(), cache: "no-store" });
    const j = await r.json().catch(() => ({}));
    skillRows = j.skills || [];
    paintSkillRows();
  }
  async function paintHub(src, q) {
    const hub = document.getElementById("skills-hub");
    if (hub) hub.textContent = "SkillHub…";
    const r = await fetch("/api/skills?src=" + encodeURIComponent(src) + "&q=" + encodeURIComponent(q || ""), { headers: headers(), cache: "no-store" });
    const j = await r.json().catch(() => ({}));
    if (!hub) return;
    hub.innerHTML = "";
    const cap = document.createElement("div");
    cap.className = "src";
    cap.textContent = (j.n || 0) + " on SkillHub · " + (j.hub || "");
    hub.appendChild(cap);
    (j.hits || []).slice(0, 24).forEach((h) => {
      const d = document.createElement("div");
      const t = document.createElement("span");
      const ch = h.channel === "full_zip" ? "FULL" : "tentacle";
      t.textContent = ch + " · " + (h.display || h.slug) + " — " + (h.summary || "").slice(0, 72);
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = h.channel === "full_zip" ? "Install FULL" : "Install";
      b.onclick = async () => {
        b.textContent = "…";
        const action = h.channel === "full_zip" ? "install_full" : "skillhub_install";
        const res = await fetch("/api/skills", {
          method: "POST",
          headers: headers(),
          body: JSON.stringify({ action: action, slug: h.slug, full: h.channel === "full_zip" }),
        });
        const out = await res.json().catch(() => ({}));
        b.textContent = out.ok ? "on" : (out.error || "fail");
        limb.textContent = JSON.stringify(out, null, 2);
        setStatus("skill install " + (out.ok ? "ok: " + h.slug : "failed: " + (out.error || res.status)), !out.ok);
        await refreshSkills();
      };
      d.appendChild(t);
      d.appendChild(b);
      hub.appendChild(d);
    });
    if (!(j.hits || []).length) hub.textContent = j.error || "no SkillHub hits";
  }
  const skLocal = document.getElementById("skills-local");
  if (skLocal) skLocal.addEventListener("input", paintSkillRows);
  const skSearch = document.getElementById("skills-search");
  if (skSearch) {
    skSearch.onclick = async () => {
      const q = (document.getElementById("skills-q") || {}).value || "";
      await paintHub("hub", q);
    };
  }
  const skHub = document.getElementById("skills-hub-btn");
  if (skHub) skHub.onclick = () => paintHub("hub", (document.getElementById("skills-q") || {}).value || "");
  const skFull = document.getElementById("skills-full-btn");
  if (skFull) skFull.onclick = () => paintHub("full", (document.getElementById("skills-q") || {}).value || "");

  const npList = document.getElementById("np-list");
  const npTitle = document.getElementById("np-title");
  const npBody = document.getElementById("np-body");
  const npStatus = document.getElementById("np-status");
  let npId = "scratch";
  let npDirty = false;
  let npTimer = null;

  function npSetStatus(t) {
    if (npStatus) npStatus.textContent = t;
  }

  async function npRefreshList(selectId) {
    if (!npList) return;
    const r = await fetch("/api/notepad", { headers: headers(), cache: "no-store" });
    const j = await r.json().catch(() => ({}));
    const notes = j.notes || [];
    npList.innerHTML = "";
    notes.forEach((n) => {
      const o = document.createElement("option");
      o.value = n.id;
      o.textContent = (n.title || n.id) + (n.id === "scratch" ? " · scratch" : "");
      npList.appendChild(o);
    });
    const want = selectId || npId;
    if ([].some.call(npList.options, (o) => o.value === want)) npList.value = want;
    npId = npList.value || "scratch";
  }

  async function npLoad(id) {
    const nid = id || npId || "scratch";
    const r = await fetch("/api/notepad?id=" + encodeURIComponent(nid), { headers: headers(), cache: "no-store" });
    const j = await r.json().catch(() => ({}));
    if (!j.ok) {
      npSetStatus("missing");
      return;
    }
    npId = j.id;
    if (npTitle) npTitle.value = j.title || j.id;
    if (npBody) npBody.value = j.text || "";
    if (npList) npList.value = j.id;
    npDirty = false;
    npSetStatus("loaded · " + (j.bytes || 0) + "b");
  }

  async function npSave() {
    const body = {
      action: "save",
      id: npId || "scratch",
      title: npTitle ? npTitle.value : "",
      text: npBody ? npBody.value : "",
    };
    const r = await fetch("/api/notepad", { method: "POST", headers: headers(), body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (!j.ok) {
      npSetStatus("save failed · " + (j.error || r.status));
      return;
    }
    npId = j.id;
    npDirty = false;
    npSetStatus("saved · " + (j.bytes || 0) + "b");
    await npRefreshList(npId);
  }

  function npMarkDirty() {
    npDirty = true;
    npSetStatus("unsaved");
    if (npTimer) clearTimeout(npTimer);
    npTimer = setTimeout(() => { npSave(); }, 900);
  }

  if (npList) {
    npList.onchange = async () => {
      if (npDirty) await npSave();
      await npLoad(npList.value);
    };
  }
  if (npTitle) npTitle.oninput = npMarkDirty;
  if (npBody) npBody.oninput = npMarkDirty;
  const npNew = document.getElementById("np-new");
  if (npNew) {
    npNew.onclick = async () => {
      if (npDirty) await npSave();
      const r = await fetch("/api/notepad", {
        method: "POST",
        headers: headers(),
        body: JSON.stringify({ action: "new", title: "Untitled" }),
      });
      const j = await r.json().catch(() => ({}));
      if (j.ok && j.id) {
        npId = j.id;
        await npRefreshList(j.id);
        await npLoad(j.id);
        if (npTitle) { npTitle.focus(); npTitle.select(); }
      }
    };
  }
  const npSaveBtn = document.getElementById("np-save");
  if (npSaveBtn) npSaveBtn.onclick = npSave;
  const npDel = document.getElementById("np-del");
  if (npDel) {
    npDel.onclick = async () => {
      const id = npId || "scratch";
      if (id !== "scratch" && !confirm("Delete this note?")) return;
      const r = await fetch("/api/notepad", {
        method: "POST",
        headers: headers(),
        body: JSON.stringify({ action: "delete", id }),
      });
      const j = await r.json().catch(() => ({}));
      if (!j.ok) {
        npSetStatus("delete failed");
        return;
      }
      npId = "scratch";
      await npRefreshList("scratch");
      await npLoad("scratch");
      npSetStatus(id === "scratch" ? "scratch cleared" : "deleted");
    };
  }
  const npCap = document.getElementById("np-capture");
  if (npCap) {
    npCap.onclick = () => {
      const sel = (window.getSelection && window.getSelection().toString()) || "";
      if (!sel.trim()) {
        npSetStatus("select text in chat first");
        return;
      }
      if (npBody) {
        npBody.value = (npBody.value ? npBody.value.replace(/\s*$/, "\n\n") : "") + sel.trim();
        npMarkDirty();
        npSave();
      }
    };
  }
  const npAsk = document.getElementById("np-ask");
  if (npAsk) {
    npAsk.onclick = () => {
      const title = (npTitle && npTitle.value) || npId || "scratch";
      msg.value = "Look at my notepad notes. Read note id " + (npId || "scratch") + " titled " + title + " and use it.";
      msg.focus();
    };
  }

  /* ---- Song studio --------------------------------------------------------
     The third making limb, beside pictures and voice. `music_generate` can now
     render a whole song - vocals and accompaniment together - with the local
     engine, and this block is the operator's handle on it from inside the page.

     It is deliberately a THIN client. The server owns the render, the job it is
     on and the folder of finished songs; this block posts the operator's intent
     and paints what GET /api/music says. Nothing here invents a filename, a
     duration or a seed - if the server does not report a fact, the panel does not
     show one. That is the rule worth being stubborn about, because a render takes
     MINUTES on this machine and a single ~30 s segment can run tens of minutes: a
     cheerful fake "done" would be worse than no panel at all, since the operator
     would sit waiting on a song that was never written.

     Shape copied from the notepad block above, so the two panels behave alike:
     fetch() with headers() and cache:"no-store", the ids held in local consts,
     one tiny msSet() for the status line. Two things are new here, and both come
     from the job being long-lived:

       * a POLL. start() puts a 3000 ms timer on GET /api/music, and that timer -
         not the button, not the POST response - is the only thing allowed to
         declare the render over, and only once the server's own job.state has
         left "running". Cancel exists for the same reason: a long render has to
         be abandonable.
       * a SIGNATURE. Rebuilding the song list on every tick would tear down and
         recreate the <audio> elements, so playback would restart every 3 s. Each
         renderer therefore runs only when the data behind it really changed. */
  (function songStudio() {
    const msRoot = document.getElementById("ms-studio");
    if (!msRoot) return;
    const msEngine = document.getElementById("ms-engine");
    const msRefBtn = document.getElementById("ms-refresh");
    const msStyleEl = document.getElementById("ms-style");
    const msLyricsEl = document.getElementById("ms-lyrics");
    const msSegEl = document.getElementById("ms-segments");
    const msSeedEl = document.getElementById("ms-seed");
    const msTokEl = document.getElementById("ms-tokens");
    const msGo = document.getElementById("ms-go");
    const msCancel = document.getElementById("ms-cancel");
    const msStatus = document.getElementById("ms-status");
    const msSongsEl = document.getElementById("ms-songs");
    const msTemplateBtn = document.getElementById("ms-template");
    const msConvertBtn = document.getElementById("ms-convert");
    const msCheckBtn = document.getElementById("ms-check");
    const msClearBtn = document.getElementById("ms-clear");
    const msWriteBtn = document.getElementById("ms-write");
    const msSurpriseBtn = document.getElementById("ms-surprise");
    const msRestoreBtn = document.getElementById("ms-restore");
    const msSheetMsgEl = document.getElementById("ms-sheetmsg");
    const msStylePresets = document.getElementById("ms-style-presets");

    let msPoll = null;        // the 3000 ms handle, alive only while a render is out there
    let msBusy = false;       // a control POST (start/cancel/engine) is in flight
    let msApplied = false;    // the server's defaults have been applied once already
    let msEngineSig = null;   // what the engine select was last built from (null = never built)
    let msSongSig = null;     // what the song list was last built from (null = never built)
    let msSongsLast = [];     // the last song list the server reported (msPaintJob checks a name against it)
    let msRouteLast = null;   // the last card reading the server sent (the "will it fit" line)

    function msSet(text, warn) {
      if (!msStatus) return;
      msStatus.textContent = text;
      msStatus.classList.toggle("warn", !!warn);
    }

    /* A refusal is only useful with the remedy it came with. The server sends `hint` with every named
       refusal (and a p0 refusal sends `gate.reason`), so the panel says what to DO rather than only
       which code came back - `could not start: lyrics_required` is not an instruction. */
    function msWhy(j, http) {
      const code = (j && j.error) || ("HTTP " + http);
      const why = (j && (j.hint || (j.gate && j.gate.reason) || j.why)) || "";
      return why ? code + " — " + why : code;
    }

    /* "Will it even fit?" is the question an 8 GB box makes the operator ask before every render, and
       the server answers it on every poll (`route`: the profile chosen, the VRAM it read, and who is
       holding the card). Painting it is the whole point of paying for that reading. */
    function msRouteLine() {
      const r = msRouteLast;
      if (!r || !r.route) return "";
      /* The server's own sentence, not a second phrasing of the same numbers: `why` is what the strip
         card says too, and two copies of one rule drift apart the moment either changes. */
      return " · the card: " + r.route + (r.why ? " — " + r.why : "");
    }

    /* Songs ride the kernel's OWN media route, the identical mechanism pictures
       already use (mediaSrc above): /api/media/audio/<name>. The module exposes
       only GET/POST /api/music, so the module has no audio route of its own to
       call - the kernel serves the bytes.
       The names are file names off disk, so always encode them. */
    function msAudioSrc(name) {
      return "/api/media/audio/" + encodeURIComponent(String(name || ""));
    }

    /* Seconds as a person reads them: 95 -> 1:35, 3850 -> 1:04:10. A full song on this machine is
       HOURS, so the clock has to be able to say an hour without sounding like a fault. */
    function msClock(s) {
      const n = Math.max(0, Math.round(Number(s) || 0));
      const h = Math.floor(n / 3600), m = Math.floor((n % 3600) / 60), sec = n % 60;
      const two = (x) => (x < 10 ? "0" + x : String(x));
      return h ? h + ":" + two(m) + ":" + two(sec) : m + ":" + two(sec);
    }

    function msNum(el, fallback) {
      const n = parseInt((el && el.value) || "", 10);
      return isFinite(n) ? n : fallback;
    }

    /* The engine choices are the server's list, verbatim. An engine that is not
       installed here is still SHOWN - disabled, with its own state and note in the
       label - because hiding it would make the panel lie about what this build can
       do the moment the weights land. */
    /* The rows the dropdown was built from. The estimate line needs them: a declared-but-unwired
       engine has no honest cost, and quoting the usable engine's rate for it would be this page
       inventing a number for a render the server is going to refuse. */
    let msEngineRows = [];

    function msEngineRow(id) {
      for (let i = 0; i < msEngineRows.length; i++) if (msEngineRows[i].id === id) return msEngineRows[i];
      return null;
    }

    function msFillEngines(engines, current) {
      if (!msEngine) return;
      const list = engines || [];
      msEngineRows = list;
      msEngine.innerHTML = "";
      if (!list.length) {
        const o = document.createElement("option");
        o.value = "";
        o.textContent = "no song engine declared on this console";
        msEngine.appendChild(o);
        msEngine.disabled = true;
        return;
      }
      msEngine.disabled = false;
      list.forEach((e) => {
        const o = document.createElement("option");
        o.value = e.id;
        const missing = e.state && e.state !== "installed";
        /* MEASURED 2026-09-25: this read "Stable Audio — declared (declared, not usable yet: …)"
           because the server's note already opens with the state word. Say it once. */
        let suffix = "";
        if (missing) {
          const note = String(e.note || "").trim();
          const stateWord = String(e.state || "").toLowerCase();
          suffix = note && note.toLowerCase().indexOf(stateWord) === 0
            ? " (" + note + ")"
            : " — " + e.state + (note ? " (" + note + ")" : "");
        }
        o.textContent = (e.label || e.id) + suffix;
        if (e.note) o.title = e.note;
        if (missing) o.disabled = true;
        if (e.id === current) o.selected = true;
        msEngine.appendChild(o);
      });
      const usable = [].some.call(msEngine.options, (o) => o.value === current && !o.disabled);
      if (usable) msEngine.value = current;
    }

    /* The server's defaults land ONCE, on the first good GET, and never again:
       after that the boxes belong to the operator, and a poll that re-filled them
       would wipe a half-typed lyric sheet out from under the render. */
    function msApplyDefaults(d) {
      if (msApplied || !d) return;
      msApplied = true;
      if (msStyleEl && !msStyleEl.value && d.style != null) msStyleEl.value = d.style;
      /* The section count starts EMPTY, and 0 means "one section per part of the words": writing the
         server's 0 into the box would read as a demand for no sections at all, and a 1 there caps
         every song at a single section (measured: 15 s of audio). The lyric sheet decides unless the
         operator says otherwise. */
      if (msSegEl && Number(d.segments) > 0) msSegEl.value = d.segments;
      if (msSeedEl && d.seed != null) msSeedEl.value = d.seed;
      if (msTokEl && d.max_new_tokens != null) msTokEl.value = d.max_new_tokens;
    }

    /* Only the facts the server actually reported, in that order. */
    function msSongLine(s) {
      const bits = [];
      if (s.seconds != null && s.seconds !== "") bits.push("duration " + msClock(s.seconds));
      if (s.sections) bits.push(s.sections + (Number(s.sections) === 1 ? " section" : " sections"));
      if (s.engine) bits.push("engine " + s.engine);
      if (s.seed != null) bits.push("seed " + s.seed);
      if (s.bytes) bits.push(Math.round(Number(s.bytes) / 1024) + " KB");
      if (s.style) bits.push("style: " + s.style);
      return bits.length ? bits.join(" · ") : "the server reported no other facts for this song";
    }

    /* Newest first. Song names are date-stamped on disk (20260925-1239_song.mp3),
       so a descending name sort is newest-first even if the folder listing the
       server read came back in some other order. A name with no stamp still sorts
       stably - it just sorts by its own text. The signatures above start at null
       rather than "", so an EMPTY list still renders: a blank panel where the
       explanation belongs is the failure mode this avoids. */
    function msRenderSongs(songs) {
      if (!msSongsEl) return;
      const list = (songs || []).slice().sort((a, b) => String(b.name || "").localeCompare(String(a.name || "")));
      msSongsEl.innerHTML = "";
      /* A count line, because "the list" with no number in it is how an operator cannot tell a render
         that never landed from one that landed two screens down. */
      const head = document.createElement("div");
      head.className = "ms-songs-head";
      /* The count stays the head element's OWN textContent, not a child span's: the studio's node
         harness reads songHead().textContent directly (tests/js/music_studio_panel.js asserts "2
         takes" / "no takes yet"), and a stub DOM does not aggregate a parent's text from its
         children the way a browser does. So the text is set FIRST and the button appended after -
         setting textContent on a real element wipes its children, which would remove the button. */
      head.textContent = list.length
        ? list.length + (list.length === 1 ? " take" : " takes") + " · newest first"
        : "no takes yet";
      /* An explicit refresh, next to the count.
         The poll below already re-reads GET /api/music every 3 s and repaints only when the list's
         own signature changes, so a take that landed while the page was open DOES arrive on its own.
         What the poll cannot cover is the operator's own filesystem: a take they moved or deleted in
         Explorer, or a render that finished while this tab was asleep, changes nothing the poll
         notices until it happens to look. Pressing this drops the signature to null and re-reads at
         once, so "show me what is in the folder NOW" is one click and never a wait. */
      const re = document.createElement("button");
      re.type = "button";
      re.className = "ms-songs-refresh";
      re.textContent = "refresh";
      re.title = "Re-read the songs folder now (the list also refreshes itself every few seconds)";
      re.onclick = () => {
        msSongSig = null;
        msLoad().catch(() => {});
      };
      head.appendChild(re);
      msSongsEl.appendChild(head);
      if (!list.length) {
        const p = document.createElement("p");
        p.className = "ms-empty";
        p.textContent = "No songs rendered on this console yet. Write a style and some lyrics above, press Generate, and the first finished take lands here — with its player, its real duration and the seed that made it.";
        msSongsEl.appendChild(p);
        return;
      }
      list.forEach((s) => {
        const row = document.createElement("div");
        row.className = "ms-song";
        let au = null;
        if (s.name) {
          /* The <audio> element IS the player; the button only drives it. A native control bar on
             every row is ~54px of band height, which is why one take filled the whole list. */
          au = document.createElement("audio");
          au.preload = "none";
          au.src = msAudioSrc(s.name);
          row.appendChild(au);
          const play = document.createElement("button");
          play.type = "button";
          play.className = "ms-song-play";
          play.textContent = "play";
          play.title = "Play " + s.name;
          /* `.onclick` / `.onpause` and `classList.toggle(cls, bool)` are the studio's own convention, not a
             preference: its node harness (tests/js/music_studio_panel.js) runs this block against a stub DOM
             whose elements carry values but no listener registration, no querySelectorAll, and a classList
             with only `toggle`. Same behaviour on the page; the harness stays honest. */
          play.onclick = () => {
            if (au.paused) {
              msOnlyOne(au);
              if (au.play) { const p = au.play(); if (p && p.catch) p.catch(() => {}); }
            } else if (au.pause) au.pause();
          };
          au.onplay = () => { play.textContent = "stop"; row.classList.toggle("playing", true); };
          const off = () => { play.textContent = "play"; row.classList.toggle("playing", false); };
          au.onpause = off;
          au.onended = off;
          row.appendChild(play);
        }
        const main = document.createElement("div");
        main.className = "ms-song-main";
        const nm = document.createElement("span");
        nm.className = "ms-song-name";
        /* A title is what the operator called the take; the file name is what is really on disk.
           Both are shown, because they answer different questions and either one alone has misled
           someone: a title with no file name hides what to look for on disk, and a stamped file name
           with no title is the thing this row used to be. */
        nm.textContent = s.title || s.name || "(song without a name)";
        nm.title = s.title ? s.title + "  ·  " + s.name : (s.name || "");
        const meta = document.createElement("div");
        meta.className = "ms-song-meta";
        meta.textContent = [s.when, msSongLine(s)].filter(Boolean).join(" · ");
        main.appendChild(nm);
        main.appendChild(meta);
        if (s.name) {
          /* Name a take after the fact. The audio is never re-rendered and never renamed on disk -
             the title is written into the take's own sidecar - so this costs nothing and cannot
             break a link the operator already has. Blank clears the title and the file name returns. */
          const rn = document.createElement("input");
          rn.type = "text";
          rn.className = "ms-song-title";
          rn.value = s.title || "";
          rn.placeholder = "name this take";
          rn.title = "Name this take. Blank clears the name and it is listed by its file name again.";
          rn.onchange = () => {
            msPost({ action: "retitle", name: s.name, title: rn.value })
              .then(() => { msSongSig = null; return msLoad(); })
              .catch((e) => msSet("studio: " + ((e && e.message) || e), true));
          };
          main.appendChild(rn);
        }
        row.appendChild(main);
        if (s.name) {
          const get = document.createElement("a");
          get.className = "ms-song-get";
          get.href = msAudioSrc(s.name);
          get.download = "";   /* the property, not setAttribute: the harness's stub elements carry values only */
          get.textContent = "get";
          get.title = "Download this take";
          row.appendChild(get);
        }
        /* "Make another one like that": this take's own style and seed go back in the boxes. Offered
           only when the server actually reported them - a button that fills nothing is a lie. */
        if (s.name && (s.style || s.seed != null)) {
          const again = document.createElement("button");
          again.type = "button";
          again.className = "ms-song-again";
          again.textContent = "again";
          again.title = "Put this take's style and seed back in the boxes above";
          again.onclick = () => {
            if (s.style && msStyleEl) msStyleEl.value = s.style;
            if (s.seed != null && msSeedEl) msSeedEl.value = s.seed;
            msSet("this take's style" + (s.seed != null ? " and seed " + s.seed : "") + " are back in the boxes above");
          };
          row.appendChild(again);
        }
        msSongsEl.appendChild(row);
      });
    }

    /* One take at a time: two rows singing over each other is not a feature. */
    function msOnlyOne(keep) {
      if (!msSongsEl || !msSongsEl.querySelectorAll) return;   // the node harness's fake elements have no querySelectorAll
      [].forEach.call(msSongsEl.querySelectorAll("audio"), (a) => { if (a !== keep) a.pause(); });
    }

    /* ------------------------------------------------------------------ the quick picks
       The boxes above are the ENGINE'S contract - tokens per section - which is precise and useless
       to a songwriter: nobody wants to work out that 3000 tokens is about 30 s of song before they
       can hear their own words. These buttons write those same boxes, and the line under them says
       what the current words and numbers will BECOME: how many sections, how much song, and what
       that costs on the engine selected right now (MEASURED 2026-09-25: one 1500-token section took
       3850 s at profile 3 on this 8 GB card; the other engine renders a whole song in minutes). */
    (function msQuick() {
      const quick = document.getElementById("ms-quick");
      const est = document.getElementById("ms-est");
      const dice = document.getElementById("ms-dice");
      if (!quick || !est) return;
      function msSections() {
        const asked = msNum(msSegEl, 0);
        if (asked > 0) return asked;
        const words = (msLyricsEl && msLyricsEl.value) || "";
        const tags = (words.match(/^[ \t]*\[[^\]]+\][ \t]*$/gm) || []).length;
        const paras = words.split(/\n[ \t]*\n/).filter((t) => t.trim().length).length;
        return tags || paras || 0;
      }
      /* "~" everywhere: this is the panel's own arithmetic from the engine's documented rate, not a
         promise the server made. */
      function msSay() {
        const secs = msSections();
        const tok = msNum(msTokEl, 3000) || 3000;
        const per = Math.max(1, Math.round(tok / 100));   // the engine's own rate: 1000 tokens ~ 10 s
        if (!secs) { est.textContent = "no sections in the sheet yet — write the words, or press template"; return; }
        const eng = (msEngine && msEngine.value) || "";
        const row = msEngineRow(eng);
        /* The cost belongs to the engine that can actually render. MEASURED 2026-09-25: with
           `stable_audio` selected the line quoted the OTHER engine's profile-3 rate - hours on this
           card - for an engine this build has no adapter for and no weights of. A number is a claim. */
        if (row && (row.state !== "installed" || row.wired === false)) {
          est.textContent = secs + (secs === 1 ? " section" : " sections") + " · " + (row.label || eng)
            + " cannot render on this build — " + (row.note || "it is not installed, or this build has no adapter for it");
          return;
        }
        const fast = eng === "acestep" || eng === "ace_step" || !!(row && row.adapter === "acestep");
        let cost;
        if (fast) {
          cost = "this engine renders a whole song in a minute or two";
        } else {
          const mins = Math.round(secs * tok * 2.567 / 60);
          cost = "~" + (mins >= 90 ? (Math.round(mins / 6) / 10) + " h" : mins + " min") + " on this card at profile 3";
        }
        est.textContent = secs + (secs === 1 ? " section" : " sections") + " -> ~" + msClock(secs * per) + " of song · " + cost;
      }
      quick.onclick = (ev) => {
        const b = ev.target && ev.target.closest ? ev.target.closest("button[data-ms-seg]") : null;
        if (!b) return;
        if (msSegEl) msSegEl.value = b.getAttribute("data-ms-seg");
        if (msTokEl) msTokEl.value = b.getAttribute("data-ms-tok");
        msSay();
      };
      if (dice) dice.onclick = () => {
        if (msSeedEl) msSeedEl.value = Math.floor(Math.random() * 999999999);
        msSay();
      };
      /* addEventListener, not `el.onchange = msSay`. MEASURED 2026-09-25: the engine-switch handler
         further down assigns msEngine.onchange itself, silently REPLACING this one - so switching
         engines left the cost line describing the engine that was selected before, which is how the
         studio came to quote hours on this card for an engine this build cannot even drive. A
         listener cannot be overwritten by a later assignment the way a property can. */
      [msStyleEl, msLyricsEl, msSegEl, msTokEl, msSeedEl, msEngine].forEach((el) => {
        if (!el || !el.addEventListener) return;
        el.addEventListener("input", msSay);
        el.addEventListener("change", msSay);
      });
      msSay();
    })();

    /* The single place that decides what "the job" looks like, so the button
       states, the status line and the poll can never disagree. Running is the only
       state that keeps the poll alive; every other state stops it, and failed or
       cancelled shows the server's status string VERBATIM - a paraphrase here is
       how an operator ends up debugging the wrong thing. */
    function msPaintJob(job) {
      const j = job || {};
      const running = j.state === "running";
      if (msGo) msGo.disabled = running || msBusy;
      if (msCancel) msCancel.disabled = !running;
      if (running) {
        /* The plan first, then the engine's OWN position in it. A full song here is hours, so
           "rendering" with no position cannot be told from a render that hung: the server reads
           `section 3 of 6` out of the engine's own log and hands it over as j.progress. */
        const plan = (typeof j.sections === "number" && j.sections > 0)
          ? j.sections + (j.sections === 1 ? " section" : " sections")
            + (j.planned_audio_seconds ? " (~" + msClock(j.planned_audio_seconds) + " of song)" : "")
            + " · "
          : "";
        const pos = (j.progress && j.progress.text) ? j.progress.text : (j.status || "working");
        msSet(plan + pos + " · this takes as long as it takes; leaving the window open is safe"
              + (typeof j.elapsed_s === "number" ? ", elapsed " + msClock(j.elapsed_s) : ""));
        return true;
      }
      if (!j.state || j.state === "none") {
        msSet((j.status || "idle — write a style and some lyrics, then press Generate") + msRouteLine());
        return false;
      }
      if (j.state === "done") {
        /* The wire carries the song as a PATH (a string): the job record and the playlist both hold a
           path, and the file on disk is the claim. The object shape is still accepted so a record that
           ever carries one is not read as "no song" - but what the server really sends is the path, and
           reading it as an object told the operator "nothing here is claimed as finished" over a song
           that had just been written. */
        const raw = j.song;
        const path = typeof raw === "string" ? raw : ((raw && raw.path) || "");
        const name = path ? path.split(/[\\/]/).pop() : ((raw && raw.name) || "");
        const rec = name ? msSongsLast.find((s) => (path && s.path === path) || s.name === name) : null;
        const secs = (rec && rec.seconds) || (raw && raw.seconds) || 0;
        if (rec) msSet("finished · " + name + (secs ? " · " + secs + "s" : "") + " — it is in the list below" + msRouteLine());
        else if (name) msSet("the job says done and names " + name + ", but that song is not in the list yet — press Refresh", true);
        else msSet("the job says done but names no song, so nothing here is claimed as finished — press Refresh to ask again", true);
        return false;
      }
      msSet(j.status || (j.state + " — the engine reported no detail"), true);
      return false;
    }

    /* One GET, painted. Each renderer is skipped unless its own input changed,
       which is what stops the poll from restarting the operator's playback every
       3 s. msSongsLast is set before msPaintJob because the "done" wording checks
       the finished name against that very list. */
    async function msLoad() {
      let r;
      let j;
      try {
        r = await fetch("/api/music", { headers: headers(), cache: "no-store" });
        j = await r.json().catch(() => ({}));
      } catch (e) {
        msSet("studio: GET /api/music failed — " + ((e && e.message) || e), true);
        return null;
      }
      if (!r.ok || j.ok === false) {
        msSet("studio: the server refused /api/music — " + msWhy(j, r.status), true);
        return null;
      }
      const engines = j.engines || [];
      msRouteLast = j.route || null;   // painted with the job: the "will it fit" line before Generate
      const engSig = engines.map((e) => e.id + ":" + e.state).join(",") + "|" + (j.engine || "");
      if (engSig !== msEngineSig) {
        msEngineSig = engSig;
        msFillEngines(engines, j.engine);
      }
      msApplyDefaults(j.defaults);
      msApplySheet(j);
      const songs = j.songs || [];
      const songSig = songs.map((s) => s.name + ":" + s.bytes + ":" + (s.when || "")).join(",");
      if (songSig !== msSongSig) {
        msSongSig = songSig;
        msSongsLast = songs;
        msRenderSongs(songs);
      }
      if (!engines.length) {
        if (msGo) msGo.disabled = true;
        if (msCancel) msCancel.disabled = true;
        msSet("no song engine is declared on this console — the studio has nothing to render with, so it stays idle", true);
      } else {
        msPaintJob(j.job);
      }
      return j;
    }

    /* Every write goes through here, so a refused request can never be silent:
       the contract's error string goes into the status line, verbatim. */
    async function msPost(body) {
      const r = await fetch("/api/music", {
        method: "POST",
        headers: Object.assign(headers(), { "Content-Type": "application/json" }),
        body: JSON.stringify(body),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok || j.ok === false) throw new Error(msWhy(j, r.status));
      return j;
    }

    /* ---- the lyric sheet ---------------------------------------------------------------
       The template, the tag vocabulary and every rule the writer is given come from the
       server (GET /api/music -> `sheet`), so this page holds no second copy of the engine's
       contract that could drift from the one the render actually uses. */
    let msSheet = null;
    let msVocabDone = false;
    let msSheetUndo = "";

    /* Every replacement of the sheet keeps the previous one, so "restore" is always the way back.
       Deliberately not confirm(): a native dialog blocks the whole page, and this console is a page
       the operator leaves running. */
    /* Setting .value in code fires no event, so everything that listens to the sheet - the estimate
       line above all - would keep describing the sheet that USED to be in the box. MEASURED
       2026-09-25: with the engine's template sitting in the box the line still said "no sections yet
       - write the words below", because the prefill never announced itself. */
    function msSheetChanged() {
      if (!msLyricsEl || !msLyricsEl.dispatchEvent) return;
      try {
        msLyricsEl.dispatchEvent(new Event("input", { bubbles: true }));
      } catch (e) {
        const ev = document.createEvent("Event");
        ev.initEvent("input", true, false);
        msLyricsEl.dispatchEvent(ev);
      }
    }

    function msSheetPut(text, note) {
      if (!msLyricsEl) return;
      msSheetUndo = msLyricsEl.value || "";
      msLyricsEl.value = text;
      msSheetChanged();
      if (note) msSheetSay(note + (msSheetUndo.trim() ? " · restore puts back what was there" : ""));
    }

    function msSheetSay(text, bad) {
      if (!msSheetMsgEl) return;
      msSheetMsgEl.textContent = text || "";
      msSheetMsgEl.title = text || "";
      msSheetMsgEl.classList.toggle("bad", !!bad);
    }

    function msApplySheet(j) {
      const s = j && j.sheet;
      if (!s || s.error) return;
      msSheet = s;
      if (msLyricsEl && !msLyricsEl.value.trim()) {
        msLyricsEl.value = s.template || "";
        msSheetChanged();
      }
      if (!msVocabDone && msStylePresets && s.vocab) {
        msVocabDone = true;
        const seen = {};
        const put = (tag) => {
          const t = String(tag || "").trim();
          if (!t || seen[t.toLowerCase()]) return;
          seen[t.toLowerCase()] = 1;
          const o = document.createElement("option");
          o.value = t;
          msStylePresets.appendChild(o);
        };
        /* Genre first, then the character words: this datalist is the engine's own 200-tag
           vocabulary, so a style written from it is one the model was trained to follow. */
        (s.vocab.genre || []).forEach(put);
        (s.vocab.mood || []).slice(0, 60).forEach(put);
        (s.vocab.timbre || []).slice(0, 60).forEach(put);
        (s.vocab.instrument || []).slice(0, 40).forEach(put);
        (s.vocab.gender || []).slice(0, 12).forEach(put);
      }
    }

    function msSheetSections() {
      const max = (msSheet && msSheet.max_sections) || 10;
      const text = (msLyricsEl && msLyricsEl.value) || "";
      const tags = text.match(/^\s*\[\w+\]\s*$/gm) || [];
      const n = tags.length || (text.trim() ? 6 : 6);
      return Math.max(2, Math.min(max, n));
    }

    function msPickStyle() {
      const v = (msSheet && msSheet.vocab) || {};
      const pick = (axis) => {
        const list = v[axis] || [];
        return list.length ? list[Math.floor(Math.random() * list.length)] : "";
      };
      const bits = [pick("genre"), pick("genre"), pick("mood"), pick("instrument"), pick("timbre")];
      return bits.filter(Boolean).join(", ");
    }

    function msInstruction(idea) {
      if (!msSheet || !msSheet.format) return "";
      const genre = (msStyleEl && msStyleEl.value.trim()) || msPickStyle();
      if (msStyleEl && !msStyleEl.value.trim()) msStyleEl.value = genre;
      const brief = String(idea || "").trim() || msSheet.brief_fallback || "";
      return msSheet.format
        .replace("{genre}", genre)
        .replace("{language}", "English")
        .replace("{sections}", String(msSheetSections()))
        .replace("{idea}", brief);
    }

    /* The server's own text, cut down to the sheet: fences, a title line and any sign-off the
       agent added are not words to sing, and the engine would sing them. */
    function msCutSheet(text) {
      let t = String(text || "").replace(/\r\n/g, "\n").replace(/```[a-zA-Z]*/g, "");
      const first = t.search(/^\s*\[[\w ]+\]\s*$/m);
      if (first < 0) return "";
      t = t.slice(first);
      const stop = t.search(/^\s*(note|notes|explanation|i hope|hope this|let me know|here'?s|this song|the song)\b[^\n]*$/im);
      if (stop > 0) t = t.slice(0, stop);
      /* the trailing newline is load-bearing: the engine's splitter only ends a section on a
         newline, so a sheet whose last tag sits flush against the end loses that section */
      return t.replace(/\n{3,}/g, "\n\n").trim() + "\n";
    }

    /* One turn, off to the side: the draft is not a conversation, so it does not go through
       chatHistory — but it IS the operator's own selected brain, local or API, that writes it. */
    async function msAskAgent(instruction) {
      const useApi = (typeof brainMode !== "undefined") && brainMode === "api";
      const r = await fetch("/api/chat", {
        method: "POST",
        headers: Object.assign(headers(), { "Content-Type": "application/json" }),
        body: JSON.stringify({ messages: [{ role: "user", content: instruction }], tools: false, stream: false, use_api: useApi }),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(msWhy(j, r.status));
      }
      const ctype = r.headers.get("content-type") || "";
      if (ctype.includes("event-stream") && r.body && r.body.getReader) {
        const reader = r.body.getReader();
        const dec = new TextDecoder();
        let buf = "";
        let out = "";
        for (;;) {
          const step = await reader.read();
          if (step.done) break;
          buf += dec.decode(step.value, { stream: true });
          const lines = buf.split("\n");
          buf = lines.pop();
          for (const raw of lines) {
            const line = raw.replace(/^data:\s*/, "").trim();
            if (!line) continue;
            try {
              const evn = JSON.parse(line);
              if (evn.delta) out += evn.delta;
              if (evn.replace) out = evn.replace;
              if (evn.error) throw new Error(evn.error);
            } catch (e) {
              if (e instanceof Error && e.message && !/JSON/.test(e.message)) throw e;
            }
          }
        }
        return out;
      }
      const j = await r.json().catch(() => ({}));
      const msg = j.choices && j.choices[0] && j.choices[0].message;
      return String((msg && msg.content) || j.content || j.text || j.reply || "").trim();
    }

    async function msWriteSong(idea, label) {
      if (!msSheet || !msSheet.format) {
        msSheetSay("the sheet has not loaded yet — press Refresh, then try again", true);
        return;
      }
      msSheetSay(label + ": the agent is writing — with a local brain this takes a moment…");
      let text;
      try {
        text = await msAskAgent(msInstruction(idea));
      } catch (e) {
        msSheetSay(label + " failed: " + ((e && e.message) || e), true);
        return;
      }
      const sheet = msCutSheet(text);
      if (!sheet) {
        msSheetSay(label + ": the agent replied without a single [verse]/[chorus] tag, so nothing was put in the box — the turn is in the conversation to read", true);
        return;
      }
      if (msLyricsEl) {
        msSheetUndo = msLyricsEl.value || "";
        msLyricsEl.value = sheet;
        msSheetChanged();
      }
      let chk = {};
      try {
        chk = (await msPost({ action: "check", lyrics: sheet })).check || {};
      } catch (e) {
        msSheetSay(label + ": written, but the server would not check it — " + ((e && e.message) || e), true);
        return;
      }
      if (chk.style && msStyleEl && !msStyleEl.value.trim()) msStyleEl.value = chk.style;
      const probs = chk.problems || [];
      msSheetSay(label + ": " + (probs.length
        ? "written, but check says — " + probs.join(" · ")
        : "written and clean: " + (chk.sections || 0) + " sections (" + (chk.tags || []).join(" ") + ")"),
        !!probs.length);
    }

    if (msTemplateBtn) {
      msTemplateBtn.onclick = () => {
        if (!msSheet || !msSheet.template) { msSheetSay("the sheet has not loaded yet — press Refresh", true); return; }
        msSheetPut(msSheet.template, "the engine's template is in the box — fill it in, or press clear");
      };
    }
    if (msClearBtn) {
      msClearBtn.onclick = () => {
        msSheetPut("", "empty — the template button puts the fill-in sheet back");
      };
    }
    if (msConvertBtn) {
      msConvertBtn.onclick = async () => {
        if (!msLyricsEl || !msLyricsEl.value.trim()) { msSheetSay("there is nothing in the box to convert", true); return; }
        msSheetSay("re-cutting the sheet…");
        let out;
        try {
          out = (await msPost({ action: "convert", lyrics: msLyricsEl.value })).convert || {};
        } catch (e) {
          msSheetSay("convert refused: " + ((e && e.message) || e), true);
          return;
        }
        const lifted = !!(out.style && msStyleEl && !msStyleEl.value.trim());
        if (lifted) msStyleEl.value = out.style;
        msSheetPut(out.text || "", "re-cut: " + (out.sections || 0) + " sections (" + (out.tags || []).join(" ") + ")"
          + (out.headers_moved ? ", " + out.headers_moved + " header line(s) moved to # comments so they are read and not sung" : "")
          + (lifted ? " · STYLE lifted into the style box"
                    : (out.style ? " · STYLE found in the sheet (" + out.style + ") but the style box already has one" : "")));
      };
    }
    if (msCheckBtn) {
      msCheckBtn.onclick = async () => {
        if (!msLyricsEl) return;
        msSheetSay("checking…");
        let out;
        try {
          out = (await msPost({ action: "check", lyrics: msLyricsEl.value })).check || {};
        } catch (e) {
          msSheetSay("check refused: " + ((e && e.message) || e), true);
          return;
        }
        const probs = out.problems || [];
        msSheetSay(probs.length
          ? "check: " + probs.join(" · ")
          : "check: singable — " + (out.sections || 0) + " sections (" + (out.tags || []).join(" ") + ")"
            + (out.would_fix && out.would_fix.length ? " · make it singable would fix: " + out.would_fix.join(", ") : ""),
          !!probs.length);
      };
    }
    if (msWriteBtn) {
      msWriteBtn.onclick = () => {
        const box = document.getElementById("msg");   // the composer: what you typed is the brief
        const idea = (box && box.value && box.value.trim()) || "";
        msWriteSong(idea, idea ? "from your brief" : "invented (the composer was empty)");
      };
    }
    if (msRestoreBtn) {
      msRestoreBtn.onclick = () => {
        if (!msSheetUndo.trim()) { msSheetSay("there is no earlier sheet to put back yet", true); return; }
        const now = (msLyricsEl && msLyricsEl.value) || "";
        if (msLyricsEl) msLyricsEl.value = msSheetUndo;
        msSheetUndo = now;
        msSheetChanged();
        msSheetSay("put back the previous sheet (pressing restore again swaps them)");
      };
    }
    if (msSurpriseBtn) {
      msSurpriseBtn.onclick = () => {
        if (!msSheet || !msSheet.vocab) { msSheetSay("the vocabulary has not loaded yet — press Refresh", true); return; }
        const style = msPickStyle();
        if (msStyleEl) msStyleEl.value = style;
        msWriteSong("", "random");
      };
    }

    function msStopPoll() {
      if (msPoll) {
        clearInterval(msPoll);
        msPoll = null;
      }
    }

    function msStartPoll() {
      msStopPoll();
      msPoll = setInterval(async () => {
        const j = await msLoad();
        /* A failed tick keeps the timer: the render is still out there and the
           next tick may well reach the server. Only the SERVER's own job state
           ends the watch - never a timeout of our own invention. */
        if (!j) return;
        if (j.job && j.job.state === "running") return;
        msStopPoll();
      }, 3000);
    }

    if (msGo) {
      msGo.onclick = async () => {
        msBusy = true;
        msGo.disabled = true;
        msSet("asking the engine to start — this one takes minutes");
        let j;
        try {
          j = await msPost({
            action: "start",
            style: msStyleEl ? msStyleEl.value.trim() : "",
            lyrics: msLyricsEl ? msLyricsEl.value : "",
            engine: msEngine ? msEngine.value : "",
            /* `segments: 0` is the studio's way of saying "one section per part of the words" - the
               server reads the lyric sheet and sings exactly its sections. Sending the field's value
               when it is blank used to send a 1, which capped every song at a single section. */
            segments: msNum(msSegEl, 0),
            seed: msNum(msSeedEl, 0),
            max_new_tokens: msNum(msTokEl, 3000),
          });
        } catch (e) {
          msBusy = false;
          msGo.disabled = false;
          msSet("could not start: " + ((e && e.message) || e), true);
          return;
        }
        msBusy = false;
        msPaintJob(j.job);
        /* Watch from here on, whatever the POST said: if the job is already over
           the first tick closes the watch, and while it is running the timer is
           the only thing that will ever tell us how it went. */
        msStartPoll();
      };
    }

    if (msCancel) {
      msCancel.onclick = async () => {
        msCancel.disabled = true;
        msSet("asking the engine to stop — it stops at its next checkpoint, so give it a moment");
        let j;
        try {
          j = await msPost({ action: "cancel" });
        } catch (e) {
          msSet("cancel failed: " + ((e && e.message) || e), true);
          msCancel.disabled = false;
          return;
        }
        msPaintJob(j.job);
        /* Keep watching: "cancel asked for" is not "cancelled". The engine still
           has to reach a checkpoint, and the server's next job.state is the truth. */
        msStartPoll();
      };
    }

    if (msRefBtn) {
      msRefBtn.onclick = async () => {
        msSet("asking the server…");
        const j = await msLoad();
        /* A one-off look. Nothing running means nothing to watch, so the timer
           goes away rather than living on; a running render means the operator
           just re-armed the watch. */
        if (j && (!j.job || j.job.state !== "running")) msStopPoll();
      };
    }

    if (msEngine) {
      msEngine.onchange = async () => {
        const want = msEngine.value;
        if (!want) return;
        msSet("switching engine to " + want + "…");
        let j;
        try {
          j = await msPost({ action: "engine", engine: want });
        } catch (e) {
          msSet("engine switch failed: " + ((e && e.message) || e), true);
          msEngineSig = null;   // force the box back to whatever the server still says
          await msLoad();
          return;
        }
        msSet("engine · " + (j.engine || want));
        msEngineSig = null;     // re-read the list so the box shows the server's own choice
        msLoad();
      };
    }

    /* On load, ask once; the poll only starts if a render is ALREADY running
       (started from a previous window, or by the agent). Otherwise the studio
       waits quietly for the operator to press Generate. */
    (async function msInit() {
      const j = await msLoad();
      if (j && j.job && j.job.state === "running") msStartPoll();
    })();
  })();

  (async function start() {
    await refreshHealth();
    await refreshModels();
    await refreshWorkspace();
    await refreshLimbs();
    try { await refreshSkills(); } catch (_) {}
    await loadContinuity();
    try {
      await npRefreshList();
      await npLoad(npId);
    } catch (_) {}
    const h = await refreshHealth();
    if (h && h.brain !== "ready" && h.brain !== "booting" && h.selected && !bootedOnce) {
      bootedOnce = true;
      await boot(h.selected);
    }
    paintCtx(false);
    if (!stickyStatus) {
      const hh = lastHealth || {};
      setStatus((hh.brain === "ready"
        ? ("ready · engine " + (hh.selected || "?") + " · brain " + brainMode.toUpperCase())
        : ("ready · engine=" + (hh.brain || "?") + " — press Scan drives, pick a model, then Boot LLM")) +
        (restoredFromServer ? " · transcript restored from the engine (" + restoredFromServer + " messages)" : ""));
    }
  })();
  setInterval(refreshHealth, 3000);

  const worldLocal = document.getElementById("world-local");
  const worldUtc = document.getElementById("world-utc");
  const worldCities = document.getElementById("world-cities");
  let worldSnap = null;
  async function refreshWorld() {
    try {
      const r = await fetch("/api/world", { cache: "no-store" });
      worldSnap = await r.json();
    } catch (_) {
      return;
    }
    paintWorld();
  }
  /* The city cards are built ONCE per data change and then only their clock text is ticked.
     They used to be rebuilt with innerHTML = "" every second, which re-created ten cards per
     tick - the weather row visibly flickered, and it did so while the operator was reading it.
     Measured before this change: 220 DOM mutations in 10s on an idle console. */
  let worldCardClocks = new Map();
  let worldCardSig = "";
  function paintWorld() {
    const now = new Date();
    if (worldLocal) {
      worldLocal.textContent = now.toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" });
    }
    if (worldUtc) {
      worldUtc.textContent = "UTC " + now.toISOString().slice(0, 19).replace("T", " ");
    }
    if (!worldCities || !worldSnap || !worldSnap.cities) return;
    const sig = worldSnap.cities.map((c) => (c.name || "") + "|" + (c.tz || "") + "|" +
      JSON.stringify(c.weather || {})).join("~");
    if (sig !== worldCardSig) {
      worldCardSig = sig;
      worldCardClocks = new Map();
      worldCities.textContent = "";
      worldSnap.cities.forEach((c) => {
        const d = document.createElement("div");
        d.className = "wcity";
        d.innerHTML = '<div class="n"></div><div class="t"></div><div class="w"></div>';
        const wx = c.weather || {};
        d.querySelector(".n").textContent = c.name;
        d.querySelector(".w").textContent = (wx.c != null ? Math.round(wx.c) + "\u00b0 " : "") + (wx.label || "");
        worldCities.appendChild(d);
        worldCardClocks.set(c.name, d.querySelector(".t"));
      });
    }
    worldSnap.cities.forEach((c) => {
      const t = worldCardClocks.get(c.name);
      if (!t) return;
      let clock = c.clock || "";
      try {
        clock = now.toLocaleTimeString(undefined, { timeZone: c.tz, hour: "2-digit", minute: "2-digit" });
      } catch (_) {}
      if (t.textContent !== clock) t.textContent = clock;
    });
  }
  refreshWorld();
  setInterval(paintWorld, 1000);
  setInterval(refreshWorld, 120000);

  /* ---- LYGO Function Modules: the bottom strip --------------------------------------------
     A module offers a pane (id "dock.<name>") and a GET route. This shell places it by that
     prefix — one card per module, down the full length of the page, the module's groups tiled
     across the strip — and renders whatever the route answers: the lights first, then groups of
     key/value rows, then the facts the module admits it could NOT get. No module is named in
     this file: a new pane down here is a manifest change, not a shell change.

     The strip sits BELOW the working area and the page scrolls to it, so no module can take
     room from the chat column — a module that publishes a lot of data makes the page longer,
     which is the whole point of putting it here. The top-right box stays a one-line status
     readout (`#health`), untouched by any of this.

     The pane carries the durable facts (the model record, the token budget, the rate window).
     The live numbers — tok/s, GPU layers, RAM — come from this tab's own 3s health poll and the
     last turn's timings, which the page already had; they are labelled as such rather than
     fetched twice. */

  const paneHost = document.getElementById("module-panes");
  const paneDock = document.getElementById("modules-dock");
  const paneNote = document.getElementById("modules-note");
  const paneState = document.getElementById("modules-state");
  const PANE_REFRESH_MS = 10000;  /* diagnostics, not a live feed: was 5000 */

  function paneRoute(mod) {
    const routes = (mod && mod.routes) || [];
    for (let i = 0; i < routes.length; i++) {
      const bits = String(routes[i]).trim().split(/\s+/);
      if (bits.length > 1 && bits[0].toUpperCase() === "GET") return bits[1];
      if (bits.length === 1 && bits[0].charAt(0) === "/") return bits[0];
    }
    return "";
  }

  /* The route a pane's OWN buttons post to: the module declares it in its manifest (POST /api/...),
     and if it declares none the pane's read route is used - a module that publishes buttons and
     cannot answer them fails loudly rather than silently. */
  function panePostRoute(mod) {
    const routes = (mod && mod.routes) || [];
    for (let i = 0; i < routes.length; i++) {
      const bits = String(routes[i]).trim().split(/\s+/);
      if (bits.length > 1 && bits[0].toUpperCase() === "POST") return bits[1];
    }
    return paneRoute(mod);
  }

  function paneEl(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null && text !== "") n.textContent = String(text);
    return n;
  }

  function paneRow(k, v, note, dot) {
    const row = paneEl("div", "llm-row");
    const key = paneEl("span", "llm-k");
    if (dot) key.appendChild(paneEl("i", "llm-rdot " + dot));  /* inside the key: the row grid is 42/58 */
    key.appendChild(document.createTextNode(String(k)));
    row.appendChild(key);
    const val = paneEl("span", "llm-v", v);
    row.appendChild(val);
    if (note) {
      const n = paneEl("span", "llm-note", note);
      n.title = note;             /* the note is clamped to one line; hover reads the whole thing */
      val.appendChild(n);
    }
    return row;
  }

  /* The live half of the panel: what this tab already polls, plus the engine's own timings. */
  function paneLiveRows() {
    const rows = [];
    const push = (k, v, note) => {
      if (v === undefined || v === null || v === "" || v === "—") return;
      rows.push([k, v, note]);
    };
    const h = lastHealth || {};
    push("Engine", h.brain, h.engine_present ? "the engine's port is open" : "no engine on this port — an API brain needs none");
    push("Selected", h.selected, h.selected_source ? "chosen by " + h.selected_source : "");
    push("Scanned", h.scan_n ? h.scan_n + " models" : "", "what this tree can see right now");
    push("Limbs", (h.tools || []).length ? (h.tools || []).length + " offered" : "", "what the console can do this turn");
    push("RAM free", h.ram_avail ? Math.round(h.ram_avail / 1e9) + " GB" : "");
    const p = h.perf || {};
    push("Mode", p.mode, p.reason || "");
    push("GPU layers", p.ngl, p.threads ? p.threads + " CPU threads" : "");
    push("Device", p.device || p.backend, p.vram_free_mib ? Math.round(p.vram_free_mib / 1024) + " GB VRAM free" : "");
    if (h.fallback && h.fallback.why) push("Handoff", h.fallback.why, "the API failed and the engine took the turn");
    const lp = lastPerf;
    if (lp) {
      const speed = (lp.gen_tok_s ? lp.gen_tok_s + " tok/s gen" : "") +
        (lp.prompt_tok_s ? (lp.gen_tok_s ? " · " : "") + Math.round(lp.prompt_tok_s) + " tok/s prefill" : "");
      push("Last turn", speed || "measured", "the engine's own timings, forwarded by the console");
      push("Tokens moved", lp.gen_tokens ? lp.gen_tokens + " generated" : "", lp.prompt_tokens ? lp.prompt_tokens + " in the prompt" : "");
      if (typeof lp.shown_tokens === "number" && lp.gen_tokens) {
        const surplus = typeof lp.surplus_tokens === "number" ? lp.surplus_tokens : 0;
        let note = surplus > 0
          ? surplus + " of them (" + (lp.surplus_pct || 0) + "%) were generated for the engine and never shown to you"
          : "every generated token reached the page";
        if (lp.unaccounted_tokens) note += " · " + lp.unaccounted_tokens + " not explained by the answer, a limb call or reasoning";
        if (lp.stopped && lp.stopped.why) note += " · generation stopped early: " + lp.stopped.why;
        if (lp.dropped_head) note += " · sample of what was dropped: " + lp.dropped_head;
        push("Shown to you", lp.shown_tokens + " of " + lp.gen_tokens + " tokens", note);
      }
      if (typeof lp.cache_hit_pct === "number" && lp.prompt_tokens) {
        push("Prefix reused", lp.cache_hit_pct + "%",
             lp.cache_hit_pct >= 90
               ? "the head of the prompt was already in the engine, so this turn only paid for the new tokens"
               : "a volatile line near the head of the prompt (clock, VRAM, a rewritten digest) forces the engine to re-read it");
      }
      if (typeof lp.limbs === "number" && lp.limbs) push("Limbs this turn", String(lp.limbs), "each limb call is a whole generate-and-prefill cycle of its own");
    } else {
      push("Speed", "no turn measured yet", "send one message and this fills in");
    }
    return rows;
  }

  function panePaint(meta, payload, host) {
    /* A pane may declare its own overall `state` (green/amber/red/grey) and a `state_text`. The
       shell paints the card, the badge and the strip's chip from that one field, so a module that
       answers "what needs fixing" is drawn by the same code as one that answers "what is hooked
       up" — no module is named anywhere in this file. A pane that declares nothing stays plain. */
    const state = String(payload.state || "").toLowerCase();
    const known = state === "green" || state === "amber" || state === "red";
    const card = paneEl("div", known ? "llm-pane sev-" + state : "llm-pane");
    if (known) card.dataset.state = state;
    const head = paneEl("div", "llm-head");
    head.appendChild(paneEl("span", "llm-title", meta.title || meta.id));
    if (payload.state_text) {
      const badge = paneEl("span", known ? "llm-badge sev-" + state : "llm-badge", payload.state_text);
      badge.title = payload.state_text;
      head.appendChild(badge);
    }
    const stamp = payload.at ? String(payload.at).replace("T", " ").slice(0, 19) : "";
    head.appendChild(paneEl("span", "llm-src", stamp));
    card.appendChild(head);

    const lights = payload.lights || [];
    if (lights.length) {
      const wrap = paneEl("div", "llm-lights");
      lights.forEach((l) => {
        const one = paneEl("span", "llm-light");
        one.title = [l.text, l.detail, l.next_change].filter(Boolean).join(" · ");
        one.appendChild(paneEl("i", "llm-dot " + (l.state || "grey")));
        one.appendChild(paneEl("span", "llm-ltext", (l.label ? l.label + ": " : "") + (l.text || "")));
        wrap.appendChild(one);
      });
      card.appendChild(wrap);
    }

    /* The groups tile across the strip: the dock is full length, so a module's groups sit side by
       side instead of stacking into one tall column. A group is one tile. */
    const grid = paneEl("div", "llm-groups");
    (payload.groups || []).forEach((g) => {
      const box = paneEl("div", "llm-group");
      box.appendChild(paneEl("div", "llm-gt", g.title));
      const rows = g.rows || [];
      /* Anything amber or red in a group makes that group the one the eye lands on first. */
      if (rows.some((r) => r.dot === "amber" || r.dot === "red")) box.classList.add("llm-group-fix");
      rows.forEach((r) => box.appendChild(paneRow(r.k, r.v, r.note, r.dot)));
      grid.appendChild(box);
    });
    card.appendChild(grid);

    const live = paneLiveRows();
    if (live.length) {
      const box = paneEl("div", "llm-group");
      box.appendChild(paneEl("div", "llm-gt", "Live (this page)"));
      box.appendChild(paneEl("div", "llm-note", "this tab's health poll + the last turn's timings — not asked twice"));
      live.forEach((r) => box.appendChild(paneRow(r[0], r[1], r[2])));
      grid.appendChild(box);
    }

    const gaps = payload.missing || [];
    if (gaps.length) {
      const box = paneEl("div", "llm-gap");
      box.appendChild(paneEl("div", "llm-gap-t", "Named gaps — what no source here can tell us"));
      const ul = paneEl("ul");
      gaps.forEach((m) => ul.appendChild(paneEl("li", null, m)));
      box.appendChild(ul);
      card.appendChild(box);
    }

    /* A module may publish a small strip of its OWN actions: [{action, label, title}]. The shell
       knows nothing about what any of them do - it posts {action} to the pane's own POST route,
       opens a `url` the answer names in a new tab, and shows the module's own `why` under the
       buttons. This is the seam that lets a module put a button on the console without the shell
       naming a single module. A module that publishes nothing gets no strip. */
    const acts = payload.actions || [];
    if (acts.length) {
      const bar = paneEl("div", "llm-actions");
      acts.forEach((a) => {
        const b = paneEl("button", "llm-act", a.label || a.action);
        b.type = "button";
        if (a.title) b.title = a.title;
        b.addEventListener("click", () => paneActSend(meta, a, b, bar));
        bar.appendChild(b);
      });
      bar.appendChild(paneEl("span", "llm-act-said"));
      card.dataset.paneRoute = meta.post || meta.route || "";
      card.appendChild(bar);
    }

    (host || paneHost).appendChild(card);
  }

  /* A pane button posts its action to the pane's own route and shows the module's own answer. The
     reply is an ANSWER, not a promise: a `url` it names is opened in a new tab, its `why` is shown
     under the buttons until the next refresh, and a refusal is shown too - a button that fails
     silently is worse than no button. One click is one action: the button disables itself for the
     duration so a double-click cannot start an editor twice. */
  async function paneActSend(meta, act, button, bar) {
    const route = meta.post || meta.route;
    const said = bar.querySelector(".llm-act-said");
    button.disabled = true;
    if (said) {
      said.classList.remove("llm-act-bad");
      said.textContent = (act.label || act.action) + "…";
    }
    try {
      const r = await fetch(route, {
        method: "POST",
        headers: headers(),
        body: JSON.stringify({ action: act.action }),
      });
      const data = await r.json().catch(() => ({}));
      if (data && data.url) {
        const w = window.open(data.url, "_blank", "noopener");
        if (!w && said) said.textContent = "the browser blocked the new tab — open " + data.url;
      }
      if (said) {
        said.textContent = (data && (data.why || data.state_text || data.error)) ||
          (r.ok ? "done" : "refused with no reason given");
        said.classList.toggle("llm-act-bad", !r.ok || data.ok === false);
      }
    } catch (e) {
      if (said) {
        said.textContent = "no answer: " + String((e && e.message) || e);
        said.classList.add("llm-act-bad");
      }
    }
    button.disabled = false;
    paneRefresh();
  }

  async function paneRefresh() {
    if (!paneHost) return;
    try {
      const r = await fetch("/api/modules", { headers: headers(), cache: "no-store" });
      if (!r.ok) return;
      const table = await r.json();
      const wanted = [];
      (table.modules || []).forEach((m) => {
        const route = paneRoute(m);
        if (!route || m.enabled === false) return;
        const panes = m.panes || [];
        for (let i = 0; i < panes.length; i++) {
          const pid = typeof panes[i] === "string" ? panes[i] : (panes[i] && panes[i].id) || "";
          if (pid.indexOf("dock.") === 0) wanted.push({ id: pid, route: route, post: panePostRoute(m), title: m.title || m.id });
        }
      });
      if (!wanted.length) { if (paneDock) paneDock.hidden = true; return; }
      const answers = await Promise.all(wanted.map((w) =>
        fetch(w.route, { headers: headers(), cache: "no-store" })
          .then((res) => (res.ok ? res.json() : null))
          .catch(() => null)));
      const parts = [];
      wanted.forEach((w, i) => { if (answers[i]) parts.push([w, answers[i]]); });
      if (!parts.length) return;
      /* The strip is rebuilt on every refresh. It does not scroll on its own any more — the PAGE
         scrolls, and the browser keeps the window's scroll position across a DOM replace inside
         the strip, so a rebuild every 5s no longer yanks the reader back to the top. */
      /* Built into a fragment and swapped in one go. This used to be textContent = "" followed by
         a repaint, so the strip went blank and refilled on every tick - with a slow or out-of-order
         answer that read as the strip flashing. Nothing is destroyed before its replacement exists. */
      const frag = document.createDocumentFragment();
      parts.forEach(([meta, payload]) => panePaint(meta, payload, frag));
      paneHost.replaceChildren(frag);
      if (paneDock) paneDock.hidden = false;
      if (paneState) {
        let worst = "";
        let red = 0;
        let amber = 0;
        let grey = 0;
        const lines = [];
        parts.forEach(([m, p]) => {
          const s = String(p.state || "").toLowerCase();
          if (s === "red") red++;
          else if (s === "amber") amber++;
          else if (s !== "green") grey++;   /* no state, or grey: this pane cannot say it is good */
          if (s === "red") worst = "red";
          else if (s === "amber" && worst !== "red") worst = "amber";
          else if (s === "green" && !worst) worst = "green";
          lines.push((m.title || m.id) + ": " + (p.state_text || (s ? s : "no state published")));
        });
        const bits2 = [];
        if (red) bits2.push(red + (red === 1 ? " broken" : " broken"));
        if (amber) bits2.push(amber + " need" + (amber === 1 ? "s" : "") + " attention");
        if (grey) bits2.push(grey + " unchecked");
        paneState.className = "modules-state sev-" + (worst || "grey");
        paneState.textContent = bits2.length ? bits2.join(" · ") : "all green";
        paneState.title = lines.join(" · ");
        paneState.hidden = false;
      }
      if (paneNote) {
        const counts = table.counts || {};
        const bits = [parts.length + (parts.length === 1 ? " module pane" : " module panes"), "wired " + (counts.wired !== undefined ? counts.wired : "—")];
        if (counts.refused) bits.push("refused " + counts.refused);
        if (counts.degraded) bits.push("degraded " + counts.degraded);
        paneNote.textContent = bits.join(" · ") + " · the strip grows down the page as modules are added";
      }
    } catch (e) { /* the box stays as it was: a panel that cannot refresh must not break the page */ }
  }

  if (paneHost && paneDock) {
    paneRefresh();
    setInterval(paneRefresh, PANE_REFRESH_MS);
  }

  /* VoiceStudio sits under the module cards. The app itself stays a separate install.
     This block only starts that service and asks it to speak. */
  const vsStatus = document.getElementById("vs-status");
  const vsState = document.getElementById("vs-state");
  const vsText = document.getElementById("vs-text");
  const vsEngine = document.getElementById("vs-engine");
  const vsVoice = document.getElementById("vs-voice");
  const vsAudio = document.getElementById("vs-audio");
  const vsClip = document.getElementById("vs-clip");
  const vsStart = document.getElementById("vs-start");
  const vsStop = document.getElementById("vs-stop");
  const vsSpeak = document.getElementById("vs-speak");

  function vsSet(text, bad) {
    if (!vsStatus) return;
    vsStatus.textContent = text || "";
    vsStatus.className = bad ? "hint warn" : "hint";
  }

  function vsFill(select, items, current) {
    if (!select) return;
    const chosen = select.value || current || "";
    const next = [""].concat(items.filter(Boolean));
    const same = next.join("\n") === Array.from(select.options).map((o) => o.value).join("\n");
    if (!same) {
      select.replaceChildren();
      next.forEach((id) => {
        const opt = document.createElement("option");
        opt.value = id;
        opt.textContent = id || "service default";
        select.appendChild(opt);
      });
    }
    if (chosen && Array.from(select.options).some((o) => o.value === chosen)) select.value = chosen;
  }

  function vsPlay(card) {
    if (!vsAudio) return;
    const url = card && card.clip_data_url;
    if (!url) {
      vsAudio.hidden = true;
      vsAudio.removeAttribute("src");
      return;
    }
    if (vsAudio.getAttribute("data-clip") === url) {
      vsAudio.hidden = false;
      return;
    }
    vsAudio.hidden = false;
    vsAudio.setAttribute("data-clip", url);
    vsAudio.src = url;
  }

  async function vsRefresh() {
    if (!vsStatus) return;
    try {
      const r = await fetch("/api/voice", { headers: headers(), cache: "no-store" });
      if (!r.ok) { vsSet("VoiceStudio route answered " + r.status, true); return; }
      const st = await r.json();
      const up = !!(st.service && st.service.up);
      const installed = !!(st.install && st.install.present);
      if (vsState) {
        vsState.hidden = false;
        vsState.className = "modules-state sev-" + (up ? "green" : (installed ? "amber" : "red"));
        vsState.textContent = up ? "service up" : (installed ? "installed, service down" : "not installed");
      }
      const models = (st.service && st.service.models) || [];
      const voices = (st.voices || []).map((v) => (typeof v === "string" ? v : (v.id || v.name || ""))).filter(Boolean);
      const saved = st.state || {};
      vsFill(vsEngine, models, saved.engine || "");
      vsFill(vsVoice, voices, saved.voice || "");
      const where = st.service && st.service.base ? st.service.base : "127.0.0.1:3900";
      vsSet(up ? "VoiceStudio is answering at " + where : (installed ? "Installed. Start the service to speak." : "VoiceStudio is not installed on this machine."), !up);
      const card = st.card || {};
      if (vsClip) vsClip.textContent = card.clip ? "Last clip: " + card.clip : "";
      if (card.clip_data_url) vsPlay(card);
    } catch (e) {
      vsSet("The console voice route is not answering.", true);
    }
  }

  async function vsPost(body) {
    const r = await fetch("/api/voice", {
      method: "POST",
      headers: headers(),
      body: JSON.stringify(body),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok || data.ok === false) {
      vsSet(data.why || data.error || ("VoiceStudio answered " + r.status), true);
      return data;
    }
    return data;
  }

  if (vsStart) vsStart.addEventListener("click", async () => {
    vsSet("Starting VoiceStudio. The first start can take a few minutes while models load.", false);
    await vsPost({ action: "start", wait: false });
    setTimeout(vsRefresh, 1500);
  });
  if (vsStop) vsStop.addEventListener("click", async () => {
    await vsPost({ action: "stop" });
    vsRefresh();
  });
  if (vsSpeak) vsSpeak.addEventListener("click", async () => {
    const text = (vsText && vsText.value || "").trim();
    if (!text) { vsSet("Type something for VoiceStudio to say.", true); return; }
    vsSet("Speaking…", false);
    if (vsEngine && vsEngine.value || vsVoice && vsVoice.value) {
      await vsPost({ action: "voice", engine: vsEngine ? vsEngine.value : "", voice: vsVoice ? vsVoice.value : "" });
    }
    const spoken = await vsPost({
      action: "speak",
      text: text,
      engine: vsEngine ? vsEngine.value : "",
      voice: vsVoice ? vsVoice.value : "",
    });
    if (spoken && spoken.ok) {
      vsSet("Clip written. " + (spoken.bytes || 0) + " bytes.", false);
      vsPlay(spoken.card || {});
      if (vsClip && spoken.path) vsClip.textContent = spoken.path;
    }
    vsRefresh();
  });
  if (vsStatus) {
    vsRefresh();
    setInterval(vsRefresh, 15000);
  }

  /* Photo to mesh. The server owns the GPU job. This block only posts a workspace
     path and shows the GLB the server says it wrote. */
  const meshStatus = document.getElementById("mesh-status");
  const meshHint = document.getElementById("mesh-hint");
  const meshGo = document.getElementById("mesh-go");
  const meshFile = document.getElementById("mesh-file");
  const meshView = document.getElementById("mesh-view");
  const meshDownload = document.getElementById("mesh-download");
  const meshLighter = document.getElementById("mesh-lighter");
  let meshName = "";
  function meshSet(text, bad) {
    if (!meshStatus) return;
    meshStatus.textContent = text;
    meshStatus.className = bad ? "hint warn" : "hint";
  }
  function meshShow(j) {
    if (!j || !j.url || !meshView) return;
    meshName = j.name || "";
    const src = j.url + "?t=" + Date.now();
    meshView.setAttribute("src", src);
    if (meshDownload) {
      meshDownload.hidden = false;
      meshDownload.href = src;
      meshDownload.setAttribute("download", meshName || "mesh.glb");
    }
    if (meshLighter) meshLighter.disabled = false;
    const faces = j.faces ? " · " + j.faces + " faces" : "";
    meshSet("mesh ready" + faces + (j.bytes ? " · " + j.bytes + " bytes" : ""), false);
  }
  function meshRefresh() {
    fetch("/api/mesh", { headers: headers(), cache: "no-store" })
      .then((r) => r.json())
      .then((j) => {
        if (meshHint && j && j.hint) meshHint.textContent = j.hint;
        if (j && j.ready) meshSet("mesh · TripoSR is on this machine. Pick a photo.", false);
        else meshSet("mesh · " + ((j && j.hint) || "the local builder is not installed"), true);
      })
      .catch(() => meshSet("mesh · the desk did not answer", true));
  }
  if (meshGo && meshFile) {
    meshGo.onclick = () => {
      const f = meshFile.files && meshFile.files[0];
      if (!f) { meshSet("pick a photo first", true); return; }
      meshGo.disabled = true;
      meshSet("saving the photo on this desk…", false);
      fetch("/api/upload", {
        method: "POST",
        headers: {
          "Content-Type": "application/octet-stream",
          "X-Lygo-Filename": encodeURIComponent(f.name),
          "X-LYGO-LLM-Token": token()
        },
        body: f
      })
        .then((r) => r.json())
        .then((up) => {
          if (!up || !up.path) throw new Error((up && up.error) || "upload failed");
          meshSet("building the mesh on the GPU. The first one also fetches the weights.", false);
          const resEl = document.getElementById("mesh-res");
          const cutEl = document.getElementById("mesh-cut");
          const fitEl = document.getElementById("mesh-fit");
          return fetch("/api/mesh", {
            method: "POST",
            headers: headers(),
            body: JSON.stringify({
              action: "generate",
              path: up.path,
              resolution: resEl ? Number(resEl.value) : 192,
              remove_bg: !(cutEl && !cutEl.checked),
              foreground: fitEl ? Number(fitEl.value) : 0.85
            })
          });
        })
        .then((r) => r.json())
        .then((j) => {
          if (!j || !j.ok) {
            meshSet("mesh failed — " + ((j && (j.detail || j.error)) || "no result"), true);
            return;
          }
          meshShow(j);
        })
        .catch((err) => meshSet("mesh failed — " + (err && err.message ? err.message : "request"), true))
        .then(() => { meshGo.disabled = false; });
    };
    meshRefresh();
  }
  if (meshLighter) {
    meshLighter.onclick = () => {
      if (!meshName) return;
      meshLighter.disabled = true;
      meshSet("making a lighter copy…", false);
      fetch("/api/mesh", {
        method: "POST",
        headers: headers(),
        body: JSON.stringify({ action: "decimate", name: meshName, ratio: 0.45 })
      })
        .then((r) => r.json())
        .then((j) => {
          if (!j || !j.ok) {
            meshSet("lighter copy failed — " + ((j && (j.error || j.detail)) || "no result"), true);
            meshLighter.disabled = false;
            return;
          }
          meshShow(j);
        })
        .catch(() => { meshSet("lighter copy failed", true); meshLighter.disabled = false; });
    };
  }
})();

/* ---- the sticky dock must not hide the tail of the page ----
   The dock floats at the bottom of the viewport (position: sticky), so without
   reserved space the last rows of the document can never be scrolled clear of it.
   Reserve exactly its height, and keep it exact when its rows wrap. */
(function fitDockSpace() {
  function apply() {
    const dock = document.querySelector(".media-dock");
    if (!dock) return;
    const h = Math.ceil(dock.getBoundingClientRect().height);
    document.body.style.paddingBottom = (h + 8) + "px";
  }
  function wire() {
    apply();
    const dock = document.querySelector(".media-dock");
    if (dock && window.ResizeObserver) new ResizeObserver(apply).observe(dock);
    window.addEventListener("resize", apply);
    /* the dock gains rows (title, hint) after the radio starts - re-measure */
    setTimeout(apply, 1200);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();
})();

async function loadKeys() {
  const body = document.querySelector("#keys-strip .keys-body");
  if (!body) return;
  try {
    const r = await fetch("/api/keys", { cache: "no-store" });
    const d = await r.json();
    const chips = (d.providers || []).map(p => {
      const dot = p.ok ? "🟢" : (p.configured ? "🔴" : "⚪");
      const ms = p.ok ? ` ${p.ms}ms` : (p.status ? ` ${p.status}` : "");
      const star = p.active ? "*" : "";
      return `<span class="key-chip" title="${(p.detail || "").replace(/"/g, "&quot;")}">${dot} ${p.provider}${star}${ms}</span>`;
    });
    const e = d.engine || {};
    const eng = e.brain ? `<span class="key-chip">engine ${e.brain}${e.selected ? " · " + e.selected : ""}</span>` : "";
    body.innerHTML = chips.join(" ") + eng;
  } catch (e) { body.textContent = "keys unavailable"; }
}
document.addEventListener("DOMContentLoaded", () => { loadKeys(); setInterval(loadKeys, 120000); });
