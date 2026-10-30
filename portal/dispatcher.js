/* Dispatcher worker switcher — talks to /api/dispatcher on the Manager console. */
(function () {
  var API = "";
  var workers = [];
  var current = "qwenpaw";
  var logs = {};

  function $(id) { return document.getElementById(id); }
  function bubble(text, kind) {
    var log = $("disp-log");
    if (!log) return;
    var p = document.createElement("p");
    p.className = kind || "";
    p.textContent = text;
    log.appendChild(p);
    log.scrollTop = log.scrollHeight;
    logs[current] = logs[current] || [];
    logs[current].push({ kind: kind, text: text });
  }

  function paintSelect() {
    var sel = $("disp-worker");
    if (!sel) return;
    var keep = sel.value || current;
    sel.innerHTML = "";
    workers.forEach(function (w) {
      var o = document.createElement("option");
      o.value = w.id;
      o.textContent = w.title + " · " + w.status;
      sel.appendChild(o);
    });
    if ([].some.call(sel.options, function (o) { return o.value === keep; })) sel.value = keep;
    current = sel.value || current;
    var w = workers.filter(function (x) { return x.id === current; })[0];
    $("disp-worker-state").textContent = w ? (w.status + " · " + (w.hint || "") + (w.latency_ms != null ? " · " + w.latency_ms + "ms" : "")) : "—";
  }

  function paintFeed() {
    var feed = $("disp-feed");
    if (!feed) return;
    feed.innerHTML = "";
    workers.forEach(function (w) {
      var card = document.createElement("button");
      card.type = "button";
      card.className = "lygo-disp-card" + (w.id === current ? " on" : "");
      card.innerHTML = "<b></b><span class='lygo-disp-dot'></span><small></small>";
      card.querySelector("b").textContent = w.title;
      var dot = card.querySelector(".lygo-disp-dot");
      dot.textContent = w.status;
      dot.className = "lygo-disp-dot " + (w.status || "offline");
      card.querySelector("small").textContent = (w.host || "127.0.0.1") + ":" + w.port + " · " + (w.hint || w.kind || "");
      card.addEventListener("click", function () {
        current = w.id;
        var sel = $("disp-worker");
        if (sel) sel.value = w.id;
        paintSelect();
        paintFeed();
      });
      feed.appendChild(card);
    });
  }

  async function post(action, extra) {
    extra = extra || {};
    extra.action = action;
    extra.worker = extra.worker || current;
    var ctrl = new AbortController();
    var t = setTimeout(function () { ctrl.abort(); }, 70000);
    var r = await fetch("/api/dispatcher", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: ctrl.signal,
      body: JSON.stringify(extra)
    });
    clearTimeout(t);
    return r.json();
  }

  async function refresh() {
    try {
      var r = await fetch("/api/dispatcher", { cache: "no-store" });
      var d = await r.json();
      workers = d.workers || [];
      var live = workers.filter(function (w) { return w.status === "connected"; }).length;
      $("disp-status").textContent = live + "/" + workers.length + " connected · pick a worker to talk";
      paintSelect();
      paintFeed();
    } catch (e) {
      $("disp-status").textContent = "dispatcher unavailable";
    }
  }

  $("disp-refresh") && $("disp-refresh").addEventListener("click", refresh);
  $("disp-scan") && $("disp-scan").addEventListener("click", async function () {
    $("disp-status").textContent = "searching this PC…";
    var d = await post("scan");
    workers = d.workers || workers;
    paintSelect(); paintFeed();
    $("disp-status").textContent = (workers.length) + " workers after local search";
  });
  $("disp-scan-net") && $("disp-scan-net").addEventListener("click", async function () {
    $("disp-status").textContent = "searching LAN (small private slice)…";
    var d = await post("scan_net");
    var extra = d.workers || [];
    $("disp-status").textContent = extra.length ? (extra.length + " LAN endpoints") : "no LAN LLMs in the nearby slice";
    extra.forEach(function (w) { workers.push(w); });
    paintSelect(); paintFeed();
  });
  $("disp-worker") && $("disp-worker").addEventListener("change", function () {
    current = $("disp-worker").value;
    paintFeed();
    paintSelect();
  });
  $("disp-forge-boot") && $("disp-forge-boot").addEventListener("click", async function () {
    bubble("booting Project Forge…", "note");
    var d = await post("boot", { worker: "forge" });
    bubble(d.note || d.hint || d.error || JSON.stringify(d), d.ok ? "note" : "err");
    refresh();
  });
  $("disp-boot") && $("disp-boot").addEventListener("click", async function () {
    bubble("boot " + current + "…", "note");
    var d = await post("boot");
    bubble(d.note || d.hint || d.error || JSON.stringify(d), d.ok ? "note" : "err");
    refresh();
  });
  $("disp-restart") && $("disp-restart").addEventListener("click", async function () {
    bubble("restart " + current + "…", "note");
    var d = await post("restart");
    bubble(d.hint || d.note || d.error || JSON.stringify(d), d.ok ? "note" : "err");
    refresh();
  });
  $("disp-task") && $("disp-task").addEventListener("click", async function () {
    var prompt = ($("disp-input").value || "").trim() || "status";
    var d = await post("task", { prompt: prompt });
    bubble("queued " + (d.id || "") + " " + (d.note || ""), d.ok ? "note" : "err");
  });
  $("disp-form") && $("disp-form").addEventListener("submit", async function (ev) {
    ev.preventDefault();
    var prompt = ($("disp-input").value || "").trim();
    if (!prompt) return;
    $("disp-input").value = "";
    bubble(prompt, "user");
    bubble("talking to " + current + "…", "note");
    try {
      var d = await post("chat", { prompt: prompt });
      $("disp-log").querySelectorAll("p.note").forEach(function (p) { p.remove(); });
      if (!d.ok) bubble(d.hint || d.error || "no answer", "err");
      else bubble(d.reply || d.note || JSON.stringify(d), "bot");
    } catch (e) {
      bubble(String(e.message || e), "err");
    }
  });

  refresh();
  setInterval(refresh, 8000);
})();
