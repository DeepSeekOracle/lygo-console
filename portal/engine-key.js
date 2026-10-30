/* Engine API key module: create, rotate, revoke one key for the console's /v1 API. */
(function () {
  var statusEl = document.getElementById("key-status");
  var prefixEl = document.getElementById("key-prefix");
  var createBtn = document.getElementById("key-create");
  var rotateBtn = document.getElementById("key-rotate");
  var revokeBtn = document.getElementById("key-revoke");
  var reveal = document.getElementById("key-reveal");
  var secretEl = document.getElementById("key-secret");
  var copyBtn = document.getElementById("key-copy");
  var note = document.getElementById("key-note");

  function token() {
    try { return sessionStorage.getItem("lygo_token") || window.LYGO_TOKEN || ""; } catch (_) { return window.LYGO_TOKEN || ""; }
  }

  function say(text, bad) {
    if (!note) return;
    note.textContent = text || "";
    note.className = "lygo-key-note" + (bad ? " err" : "");
  }

  async function call(method, body) {
    var r = await fetch("/api/engine-key", {
      method: method,
      headers: { "Content-Type": "application/json", "X-LYGO-LLM-Token": token() },
      body: body ? JSON.stringify(body) : undefined,
      cache: "no-store",
    });
    var d = await r.json().catch(function () { return {}; });
    if (!r.ok || d.ok === false) throw new Error(d.error || ("HTTP " + r.status));
    return d;
  }

  function paint(d) {
    var configured = !!(d && d.configured);
    if (statusEl) statusEl.textContent = configured ? "active · made " + (d.created_at || "—") : "not created";
    if (prefixEl) prefixEl.textContent = configured ? (d.prefix || "lygo_sk_…") : "not created";
    if (createBtn) createBtn.hidden = configured;
    if (rotateBtn) rotateBtn.hidden = !configured;
    if (revokeBtn) revokeBtn.hidden = !configured;
    if (configured) say("Key is active. The secret is not stored in this page; rotate if it was lost.");
  }

  async function refresh() {
    try {
      paint(await call("GET"));
    } catch (error) {
      if (statusEl) statusEl.textContent = "unavailable";
      say("Could not read the key store: " + error.message, true);
    }
  }

  function showSecret(secret) {
    if (!secret || !reveal || !secretEl) return;
    secretEl.value = secret;
    reveal.hidden = false;
    secretEl.focus();
    secretEl.select();
  }

  async function action(kind) {
    if (kind === "rotate" && !confirm("Rotate the engine API key? The old key stops working immediately.")) return;
    if (kind === "revoke" && !confirm("Revoke the engine API key? External API clients will be refused.")) return;
    var button = kind === "create" ? createBtn : kind === "rotate" ? rotateBtn : revokeBtn;
    if (button) { button.disabled = true; button.dataset.was = button.textContent; button.textContent = "working…"; }
    try {
      var d = await call("POST", { action: kind, label: "LYGO Manager Console" });
      paint(d);
      if (d.secret) {
        showSecret(d.secret);
        say(kind === "create" ? "API key created. Copy it now — it is shown once." : "Key rotated. Copy the new key now — the old key is dead.");
      } else if (kind === "revoke") {
        if (reveal) reveal.hidden = true;
        if (secretEl) secretEl.value = "";
        say("API key revoked. /v1 now refuses unauthenticated clients.");
      }
    } catch (error) {
      say("Key action failed: " + error.message, true);
    } finally {
      if (button) { button.disabled = false; button.textContent = button.dataset.was || button.textContent; }
    }
  }

  async function copy() {
    if (!secretEl || !secretEl.value) return;
    try {
      await navigator.clipboard.writeText(secretEl.value);
      say("Copied to clipboard.");
    } catch (_) {
      secretEl.select();
      try { document.execCommand("copy"); say("Copied to clipboard."); }
      catch (error) { say("Select the key and copy it manually.", true); }
    }
  }

  if (createBtn) createBtn.addEventListener("click", function () { action("create"); });
  if (rotateBtn) rotateBtn.addEventListener("click", function () { action("rotate"); });
  if (revokeBtn) revokeBtn.addEventListener("click", function () { action("revoke"); });
  if (copyBtn) copyBtn.addEventListener("click", copy);
  refresh();
})();
