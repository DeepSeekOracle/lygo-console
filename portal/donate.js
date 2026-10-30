(() => {
  const PAYPAL = "https://www.paypal.com/paypalme/ExcavationPro";
  const PATREON = "https://www.patreon.com/Excavationpro";
  const EVERY_MS = 15 * 60 * 1000;
  let opened = false;
  let timer = null;

  function el(id) {
    return document.getElementById(id);
  }

  function show() {
    const layer = el("donateLayer");
    if (!layer) return;
    opened = false;
    const close = el("donateClose");
    if (close) {
      close.disabled = true;
      close.textContent = "Open a donate page to continue";
    }
    layer.hidden = false;
    layer.setAttribute("aria-hidden", "false");
  }

  function hide() {
    const layer = el("donateLayer");
    if (!layer) return;
    layer.hidden = true;
    layer.setAttribute("aria-hidden", "true");
    opened = false;
    schedule();
  }

  function markOpened() {
    opened = true;
    const close = el("donateClose");
    if (close) {
      close.disabled = false;
      close.textContent = "Thanks — continue";
    }
  }

  function schedule() {
    if (timer) clearTimeout(timer);
    timer = setTimeout(show, EVERY_MS);
  }

  function boot() {
    const layer = el("donateLayer");
    if (!layer) return;
    const pay = el("donatePaypal");
    const pat = el("donatePatreon");
    const close = el("donateClose");
    // Both donate targets live on the public web while this kit ships offline-first, and the only
    // console that loads this file is the online web portal (web_portal/index.html). So the links
    // are labelled online-only, and with no network they say so instead of opening a dead tab -
    // which also stops the layer from being a dialog the user cannot close while offline.
    function offline() {
      return typeof navigator !== "undefined" && navigator.onLine === false;
    }
    function openLink(url) {
      if (offline()) {
        opened = true;
        if (close) {
          close.disabled = false;
          close.textContent = "Donate pages need internet — close to continue";
        }
        return;
      }
      window.open(url, "_blank", "noopener");
      markOpened();
    }
    if (pay) {
      pay.title = "online only · opens the public web";
      pay.onclick = function (e) { e.preventDefault(); openLink(PAYPAL); };
    }
    if (pat) {
      pat.title = "online only · opens the public web";
      pat.onclick = function (e) { e.preventDefault(); openLink(PATREON); };
    }
    if (close) {
      close.onclick = function () {
        if (!opened) return;
        hide();
      };
    }
    layer.addEventListener("click", function (e) {
      if (e.target === layer) e.stopPropagation();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !layer.hidden && !opened) e.preventDefault();
    });
    schedule();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
