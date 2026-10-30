/* Native Stream PC agent chat + data-module fold, inside the 1.8.0 console. */
(function () {
  var API = "";
  var histories = { a9lyra: [], lightfather: [] };

  function bubble(log, text, kind) {
    var p = document.createElement("p");
    p.className = kind || "";
    p.textContent = text;
    log.appendChild(p);
    log.scrollTop = log.scrollHeight;
  }

  function setState(id, text) {
    var el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  async function refreshAgentState() {
    try {
      var ctrl = new AbortController();
      var t = setTimeout(function () { ctrl.abort(); }, 4000);
      var r = await fetch("/api/dispatcher", { cache: "no-store", signal: ctrl.signal });
      clearTimeout(t);
      var d = await r.json();
      var workers = d.workers || [];
      var portal = workers.filter(function (w) { return w.id === "portal"; })[0] || {};
      var local = workers.filter(function (w) { return w.id === "local"; })[0] || {};
      var linked = portal.status === "connected";
      setState("stream-a9-state", linked ? "linked" : (local.status === "connected" ? "local fallback" : "offline"));
      setState("stream-lf-state", linked ? "linked" : (local.status === "connected" ? "local fallback" : "offline"));
      setState("lygo-stream-status", linked
        ? "portal 8790 connected · stream chat with 5s then local GGUF"
        : (local.status === "connected" ? "stream PC slow/off · A9LYRA and Lightfather use local GGUF" : "no stream portal and no local engine"));
    } catch (e) {
      setState("lygo-stream-status", "dispatcher status unavailable");
    }
  }

  document.querySelectorAll("[data-stream-form]").forEach(function (form) {
    var persona = form.getAttribute("data-stream-form");
    var log = document.getElementById(persona === "a9lyra" ? "stream-a9-log" : "stream-lf-log");
    var textarea = form.querySelector("textarea");
    var button = form.querySelector("button");
    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      var message = (textarea.value || "").trim();
      if (!message || !log) return;
      textarea.value = "";
      bubble(log, message, "user");
      button.disabled = true;
      bubble(log, "talking to " + persona + "…", "note");
      try {
        var ctrl = new AbortController();
        var t = setTimeout(function () { ctrl.abort(); }, 70000);
        var r = await fetch("/api/dispatcher", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          signal: ctrl.signal,
          body: JSON.stringify({ action: "chat", worker: persona, prompt: message }),
        });
        clearTimeout(t);
        var d = await r.json().catch(function () { return {}; });
        if (!r.ok || d.ok === false) throw new Error(d.hint || d.error || ("HTTP " + r.status));
        log.querySelectorAll("p.note").forEach(function (p) { p.remove(); });
        bubble(log, d.reply || "(empty reply)", "bot");
        histories[persona].push({ role: "user", content: message });
        histories[persona].push({ role: "assistant", content: d.reply || "" });
        setState(persona === "a9lyra" ? "stream-a9-state" : "stream-lf-state", d.provider || "linked");
      } catch (error) {
        log.querySelectorAll("p.note").forEach(function (p) { p.remove(); });
        bubble(log, "no answer: " + (error && error.message ? error.message : error), "err");
        setState(persona === "a9lyra" ? "stream-a9-state" : "stream-lf-state", "error");
      } finally {
        button.disabled = false;
        textarea.focus();
      }
    });
  });

  refreshAgentState();

  var fold = document.getElementById("lygo-datafold-toggle");
  if (fold) {
    fold.addEventListener("click", function () {
      var folded = document.body.classList.toggle("lygo-data-folded");
      fold.textContent = folded ? "Open data modules ↓" : "Fold data modules ↑";
      if (!folded) document.getElementById("lygo-datafold-bar").scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }
})();
