/* Per-module fold controls for the console module strip. */
(function () {
  var KEY = "lygo.module.folds.v1";
  var folded = {};
  try { folded = JSON.parse(localStorage.getItem(KEY) || "{}") || {}; } catch (_) { folded = {}; }
  function save() { try { localStorage.setItem(KEY, JSON.stringify(folded)); } catch (_) {} }

  function makeButton(title, onToggle) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "llm-fold";
    b.title = "Collapse or expand " + title;
    b.setAttribute("aria-label", "Collapse or expand " + title);
    b.addEventListener("click", function (event) {
      event.stopPropagation();
      onToggle(b);
    });
    return b;
  }

  function paintButton(button, isFolded) {
    button.textContent = isFolded ? "+" : "−";
    button.setAttribute("aria-expanded", String(!isFolded));
  }

  function setPane(pane, title, isFolded) {
    pane.classList.toggle("is-folded", isFolded);
    folded[title] = isFolded;
    if (!isFolded) delete folded[title];
    save();
  }

  function decoratePane(pane) {
    if (!pane || pane.dataset.foldReady === "1") return;
    var head = pane.querySelector(".llm-head");
    var titleEl = pane.querySelector(".llm-title");
    if (!head || !titleEl) return;
    var title = titleEl.textContent.trim() || "Module";
    pane.dataset.foldReady = "1";
    var isFolded = !!folded[title];
    var button = makeButton(title, function (b) {
      isFolded = !isFolded;
      setPane(pane, title, isFolded);
      paintButton(b, isFolded);
    });
    paintButton(button, isFolded);
    head.appendChild(button);
    if (isFolded) pane.classList.add("is-folded");
  }

  function decorateDock() {
    var dock = document.getElementById("modules-dock");
    if (!dock || dock.dataset.foldReady === "1") return;
    var head = dock.querySelector(".modules-head");
    if (!head) return;
    var title = "Modules";
    dock.dataset.foldReady = "1";
    var isFolded = !!folded[title];
    var button = makeButton(title, function (b) {
      isFolded = !isFolded;
      dock.classList.toggle("is-folded", isFolded);
      folded[title] = isFolded;
      if (!isFolded) delete folded[title];
      save();
      paintButton(b, isFolded);
    });
    paintButton(button, isFolded);
    head.appendChild(button);
    if (isFolded) dock.classList.add("is-folded");
  }

  function decorateVoiceStudio() {
    var panel = document.getElementById("voicestudio");
    if (!panel || panel.dataset.foldReady === "1") return;
    var head = panel.querySelector(".modules-head");
    if (!head) return;
    var title = "VoiceStudio";
    panel.dataset.foldReady = "1";
    var isFolded = !!folded[title];
    var button = makeButton(title, function (b) {
      isFolded = !isFolded;
      panel.classList.toggle("is-folded", isFolded);
      folded[title] = isFolded;
      if (!isFolded) delete folded[title];
      save();
      paintButton(b, isFolded);
    });
    paintButton(button, isFolded);
    head.appendChild(button);
    if (isFolded) panel.classList.add("is-folded");
  }

  function decorateAll() {
    decorateDock();
    document.querySelectorAll("#module-panes .llm-pane").forEach(decoratePane);
    decorateVoiceStudio();
  }

  var host = document.getElementById("module-panes");
  if (host) {
    var pending = false;
    var observer = new MutationObserver(function () {
      if (pending) return;
      pending = true;
      requestAnimationFrame(function () { pending = false; decorateAll(); });
    });
    observer.observe(host, { childList: true, subtree: true });
  }
  document.addEventListener("DOMContentLoaded", decorateAll);
  if (document.readyState !== "loading") decorateAll();
  setTimeout(decorateAll, 1200);
})();
