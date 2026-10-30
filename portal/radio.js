(() => {
  // The only network dependencies in this console live here: the two public dock links and the
  // playlist JSONs they stream from. Everything else is offline-first, so when the network is
  // gone the radio goes quiet and says why instead of pretending to load.
  const HUB = "https://asiancoastline.com/listen.html";
  const TV = "https://chatagent.ca/sources/";
  const PLAYLISTS = [
    "https://asiancoastline.com/data/public_stream_playlist.json",
    "https://chatagent.ca/games/lattice-crypt/radio.json",
    "https://deepseekoracle.github.io/Excavationpro/data/public_stream_playlist.json",
  ];
  const $ = (id) => document.getElementById(id);
  const el = () => $("radioEl");
  // blocked: the browser refused autoplay, so the stream runs muted and the silence is not the
  // user's choice. Keeping that apart from `muted` (a deliberate press on Mute) is what stops
  // the console from either talking over the user or ignoring a real mute.
  const st = {
    tracks: [],
    i: 0,
    playing: false,
    muted: false,
    vol: 0.45,
    bag: [],
    wantPlay: true,
    blocked: false,
    loadFailed: false,
    online: true,
  };

  try {
    const s = JSON.parse(localStorage.getItem("lygo_console_radio") || "{}");
    if (typeof s.vol === "number") st.vol = Math.max(0, Math.min(1, s.vol));
  } catch (_) {}

  function save() {
    try { localStorage.setItem("lygo_console_radio", JSON.stringify({ vol: st.vol })); } catch (_) {}
  }

  function setHint(msg) {
    const h = $("radioHint");
    if (h) h.textContent = msg;
  }

  // True only while sound is actually coming out: unmuted, not paused, not ended.
  function audible() {
    const a = el();
    return !!a && !a.muted && !a.paused && !a.ended;
  }

  // The hint is the only explanation the page gives for a silent radio, so it is derived from
  // the live audio element on every paint instead of being written once and blanked on a race.
  // It stays up until playback is really audible.
  function hintFor() {
    const a = el();
    if (audible()) return "";
    if (!st.tracks.length) {
      return st.loadFailed
        ? "Radio needs internet · no playlist cached, so this console stays silent (everything else works offline)."
        : "Loading radio…";
    }
    if (st.blocked) return "Sound is held back by the browser until you press Play · press Play or move the volume slider in this dock to unmute.";
    if (st.muted) return "Muted · press Unmute in this dock to hear the radio.";
    if (a && a.paused) return "Paused · press Play to start the radio.";
    return "";
  }

  function ingest(data) {
    const raw = Array.isArray(data) ? data : (data && data.tracks) || [];
    const out = [];
    for (const t of raw) {
      const url = t && (t.stream_url || t.url);
      if (!url) continue;
      out.push({ title: t.title || t.name || "Untitled", url: url });
    }
    if (out.length) st.tracks = out;
  }

  async function loadPlaylists() {
    for (const url of PLAYLISTS) {
      try {
        const data = await fetch(url, { mode: "cors", cache: "no-store" }).then((r) => {
          if (!r.ok) throw new Error(String(r.status));
          return r.json();
        });
        ingest(data);
        if (st.tracks.length > 20) break;
      } catch (_) {}
    }
    st.loadFailed = !st.tracks.length;
    st.online = st.tracks.length > 0 || navigator.onLine !== false;
    markNet();
    refill();
  }

  function refill() {
    st.bag = st.tracks.map((_, i) => i);
    for (let i = st.bag.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      const t = st.bag[i];
      st.bag[i] = st.bag[j];
      st.bag[j] = t;
    }
  }

  // The two dock links leave this offline-first kit for the public web, so they are labelled
  // online-only and, when the console has no network, they explain themselves instead of
  // handing the user a dead page. The hrefs stay correct for when the link does work.
  function dockLink(id, url, online) {
    const a = $(id);
    if (!a) return;
    a.href = url;
    a.classList.toggle("offline", !online);
    a.title = online
      ? "online only · opens on the public web"
      : "online only · needs internet, and this console is offline";
    if (online) {
      a.removeAttribute("aria-disabled");
      a.onclick = null;
      return;
    }
    a.setAttribute("aria-disabled", "true");
    a.onclick = function (e) {
      e.preventDefault();
      setHint("That link is online-only (" + url.replace(/^https?:\/\//, "").split("/")[0] + ") · this console has no network right now.");
    };
  }

  function markNet() {
    document.body.dataset.net = st.online ? "online" : "offline";
    dockLink("radioListen", HUB, st.online);
    dockLink("radioTv", TV, st.online);
  }

  async function probe() {
    try {
      await fetch(HUB, { mode: "no-cors", cache: "no-store" });
      return true;
    } catch (_) {
      return false;
    }
  }

  function paint() {
    const title = $("radioTitle");
    const play = $("radioPlay");
    const mute = $("radioMute");
    if (title) {
      const t = st.tracks[st.i];
      title.textContent = t
        ? ((st.playing ? "▶ " : "❚❚ ") + t.title)
        : st.loadFailed
        ? "Radio — offline"
        : "Loading radio…";
    }
    if (play) play.textContent = st.playing && !st.blocked ? "Pause" : "Play";
    if (mute) mute.textContent = st.muted ? "Unmute" : "Mute";
    const pct = $("radioVolPct");
    if (pct) pct.textContent = Math.round(st.vol * 100) + "%";
    document.querySelectorAll("[data-radio-vol]").forEach(function (inp) {
      if (document.activeElement !== inp) inp.value = String(Math.round(st.vol * 100));
    });
    const a = el();
    if (a) {
      a.muted = st.muted;
      a.volume = st.vol;
    }
    setHint(hintFor());
  }

  function setVol(n) {
    st.vol = Math.max(0, Math.min(1, +n || 0));
    if (st.vol > 0) {
      // a deliberate move of the volume slider is an explicit request for sound
      st.muted = false;
      st.blocked = false;
    }
    save();
    paint();
  }

  function loadIndex(i) {
    if (!st.tracks.length) return;
    st.i = ((i % st.tracks.length) + st.tracks.length) % st.tracks.length;
    const a = el();
    const t = st.tracks[st.i];
    if (!a || !t) return;
    a.src = t.url;
    a.volume = st.vol;
    a.muted = st.muted;
    paint();
  }

  function next() {
    if (!st.bag.length) refill();
    const i = st.bag.pop();
    loadIndex(i == null ? 0 : i);
    if (st.playing || st.wantPlay) play();
  }

  function play() {
    st.wantPlay = true;
    const a = el();
    if (!a) return;
    if (!st.tracks.length) {
      paint();
      return;
    }
    if (!a.src) next();
    a.muted = st.muted;
    a.volume = st.vol;
    a.play()
      .then(function () {
        st.playing = true;
        st.blocked = false;
        paint();
      })
      .catch(function () {
        // Autoplay was refused: retrying muted keeps the stream warm, and st.blocked records
        // that the silence is the browser's doing so the hint can say so and offer the fix.
        st.blocked = true;
        a.muted = true;
        a.play()
          .then(function () {
            st.playing = true;
            st.muted = true;
            paint();
          })
          .catch(function () {
            paint();
          });
      });
  }

  function pauseKeep() {
    const a = el();
    if (a) a.pause();
    st.playing = false;
    st.wantPlay = false;
    paint();
  }

  // Sound is opt-in. Only a press on this dock's own controls (Play, Next, Mute, the volume
  // slider) may unmute - the old code listened for pointerdown on the whole document, so any
  // click anywhere on the page could start audio, and it blanked the explanation in the same
  // breath. There is deliberately no document-level listener any more.
  function unlock() {
    const a = el();
    st.muted = false;
    st.blocked = false;
    if (a) {
      a.muted = false;
      a.volume = st.vol;
    }
    if (st.wantPlay || st.playing || (a && a.src)) play();
    paint();
  }

  function boot() {
    const a = el();
    if (!a) return;
    loadPlaylists().then(function () {
      paint();
      play();
    });
    a.addEventListener("play", function () {
      st.playing = true;
      paint();
    });
    a.addEventListener("pause", function () {
      if (a.ended) return;
      st.playing = false;
      st.wantPlay = false;
      paint();
    });
    a.addEventListener("ended", function () {
      st.playing = true;
      next();
    });
    a.addEventListener("error", function () {
      if (st.playing || st.wantPlay) next();
    });
    const on = (id, fn) => {
      const n = $(id);
      if (n) n.onclick = fn;
    };
    on("radioPlay", function () {
      // the explicit play action: if the browser held the sound back, this is what unmutes it
      if (st.blocked) {
        unlock();
        return;
      }
      if (st.playing) pauseKeep();
      else play();
    });
    on("radioNext", function () {
      st.playing = true;
      if (st.blocked) {
        st.blocked = false;
        st.muted = false;
      }
      next();
    });
    on("radioMute", function () {
      st.muted = !st.muted;
      // an explicit press decides the state, so it also clears the browser-blocked flag
      st.blocked = false;
      const node = el();
      if (node) node.muted = st.muted;
      paint();
    });
    document.querySelectorAll("[data-radio-vol]").forEach(function (inp) {
      inp.addEventListener("input", function (e) {
        setVol(Number(e.target.value) / 100);
      });
    });
    // no document-level pointerdown/click listener: nothing but the controls above may unmute
    markNet();
    probe().then(function (ok) {
      st.online = ok || st.tracks.length > 0;
      markNet();
    });
    window.addEventListener("offline", function () {
      st.online = false;
      markNet();
      paint();
    });
    window.addEventListener("online", function () {
      st.online = true;
      markNet();
      if (!st.tracks.length) loadPlaylists().then(paint);
      else paint();
    });
    paint();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
