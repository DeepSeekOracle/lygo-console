/* Native Manager uploaders inside the 1.7 console.
   Posts to the local Agent Manager API; no iframe dependency. */
(function () {
  var API = "http://127.0.0.1:8790";
  var statusEl = document.getElementById("lygo-up-status");
  var signalInput = document.getElementById("lygo-up-signal-file");
  var musicInput = document.getElementById("lygo-up-music-file");
  var photoInput = document.getElementById("lygo-up-photo-file");

  function status(text, kind) {
    if (!statusEl) return;
    statusEl.textContent = text || "ready";
    statusEl.className = "lygo-up-status" + (kind ? " " + kind : "");
  }

  async function json(url, opts) {
    var r = await fetch(API + url, opts);
    var data = {};
    try { data = await r.json(); } catch (_) {}
    if (!r.ok) throw new Error(data.error || ("HTTP " + r.status));
    return data;
  }

  async function poll(id, label) {
    for (var i = 0; i < 180; i++) {
      await new Promise(function (resolve) { setTimeout(resolve, 1000); });
      try {
        var job = await json("/api/jobs/" + encodeURIComponent(id));
        status(label + " · " + (job.status || "working"), job.status === "error" ? "err" : "");
        if (!["queued", "running", "uploading"].includes(job.status)) {
          status(label + " · " + (job.status || "done") + (job.url ? " · " + job.url : ""), job.status === "error" ? "err" : "ok");
          return job;
        }
      } catch (error) {
        status(label + " · " + error.message, "err");
        return null;
      }
    }
    status(label + " · still running; open Manager for log", "");
    return null;
  }

  async function uploadSignal(file) {
    if (!file) return;
    if (!/\.zip$/i.test(file.name)) { status("Signal needs a ZIP", "err"); return; }
    status("Signal · uploading " + file.name);
    var r = await fetch(API + "/api/upload", {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream", "X-Filename": encodeURIComponent(file.name) },
      body: file
    });
    var job = await r.json().catch(function () { return {}; });
    if (!r.ok) throw new Error(job.error || ("HTTP " + r.status));
    status("Signal · queued " + file.name);
    await poll(job.id, "Signal");
  }

  async function uploadBatch(kind, files) {
    var list = Array.prototype.slice.call(files || []);
    if (!list.length) return;
    var start = await json("/api/" + kind + "/start", { method: "POST" });
    for (var i = 0; i < list.length; i++) {
      status((kind === "music" ? "Music" : "Photos") + " · uploading " + (i + 1) + "/" + list.length);
      var rel = list[i].webkitRelativePath || list[i].name;
      var r = await fetch(API + "/api/" + kind + "/file?job=" + encodeURIComponent(start.id), {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream", "X-Relpath": encodeURIComponent(rel) },
        body: list[i]
      });
      if (!r.ok) {
        var fail = await r.json().catch(function () { return {}; });
        throw new Error(fail.error || ("upload HTTP " + r.status));
      }
    }
    status((kind === "music" ? "Music" : "Photos") + " · queueing import");
    var payload = kind === "music"
      ? { id: start.id, album: "", artist: "Excavationpro" }
      : { id: start.id, title: "", tags: "PHOTO" };
    var job = await json("/api/" + kind + "/finalize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    await poll(start.id, kind === "music" ? "Music" : "Photos");
  }

  document.querySelectorAll("[data-up]").forEach(function (button) {
    button.addEventListener("click", function () {
      var kind = button.getAttribute("data-up");
      if (kind === "signal") signalInput.click();
      else if (kind === "music") musicInput.click();
      else if (kind === "photo") photoInput.click();
    });
  });

  signalInput.addEventListener("change", function () {
    var file = signalInput.files[0];
    signalInput.value = "";
    uploadSignal(file).catch(function (error) { status("Signal · " + error.message, "err"); });
  });

  musicInput.addEventListener("change", function () {
    var files = musicInput.files;
    musicInput.value = "";
    uploadBatch("music", files).catch(function (error) { status("Music · " + error.message, "err"); });
  });

  photoInput.addEventListener("change", function () {
    var files = photoInput.files;
    photoInput.value = "";
    uploadBatch("photo", files).catch(function (error) { status("Photos · " + error.message, "err"); });
  });
})();
