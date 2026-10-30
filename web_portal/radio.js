(() => {
  const HUB = "https://asiancoastline.com/listen.html";
  const TV = "https://chatagent.ca/sources/";
  const PLAYLISTS = [
    "https://asiancoastline.com/data/public_stream_playlist.json",
    "https://chatagent.ca/games/lattice-crypt/radio.json",
    "https://deepseekoracle.github.io/Excavationpro/data/public_stream_playlist.json",
  ];
  const $ = (id) => document.getElementById(id);
  const el = () => $("radioEl");
  const st = { tracks: [], i: 0, playing: false, muted: false, vol: 0.45, bag: [], wantPlay: true, unlocked: false };

  try {
    const s = JSON.parse(localStorage.getItem("lygo_console_radio") || "{}");
    if (typeof s.vol === "number") st.vol = Math.max(0, Math.min(1, s.vol));
  } catch (_) {}

  function save() {
    try { localStorage.setItem("lygo_console_radio", JSON.stringify({ vol: st.vol })); } catch (_) {}
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

  function paint() {
    const title = $("radioTitle");
    const play = $("radioPlay");
    const mute = $("radioMute");
    if (title) {
      const t = st.tracks[st.i];
      title.textContent = t ? ((st.playing ? "▶ " : "❚❚ ") + t.title) : "Loading radio…";
    }
    if (play) play.textContent = st.playing ? "Pause" : "Play";
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
  }

  function setVol(n) {
    st.vol = Math.max(0, Math.min(1, +n || 0));
    if (st.vol > 0) st.muted = false;
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
    if (!st.tracks.length) return;
    if (!a.src) next();
    a.muted = st.muted;
    a.volume = st.vol;
    a.play()
      .then(function () {
        st.playing = true;
        st.unlocked = true;
        paint();
      })
      .catch(function () {
        a.muted = true;
        a.play()
          .then(function () {
            st.playing = true;
            st.muted = true;
            paint();
            const hint = $("radioHint");
            if (hint) hint.textContent = "Click Play or anywhere once to unmute (browser autoplay).";
          })
          .catch(function () {
            const hint = $("radioHint");
            if (hint) hint.textContent = "Click Play to start radio.";
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

  function unlock() {
    if (st.unlocked && !st.muted) return;
    st.muted = false;
    st.unlocked = true;
    const a = el();
    if (a) a.muted = false;
    if (st.wantPlay || st.playing) play();
    const hint = $("radioHint");
    if (hint) hint.textContent = "";
    paint();
  }

  function boot() {
    const a = el();
    if (!a) return;
    loadPlaylists().then(function () {
      paint();
      play();
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
      if (st.playing) pauseKeep();
      else play();
    });
    on("radioNext", function () {
      st.playing = true;
      next();
    });
    on("radioMute", function () {
      st.muted = !st.muted;
      el().muted = st.muted;
      paint();
    });
    document.querySelectorAll("[data-radio-vol]").forEach(function (inp) {
      inp.addEventListener("input", function (e) {
        setVol(Number(e.target.value) / 100);
      });
    });
    document.addEventListener(
      "pointerdown",
      function () {
        unlock();
      },
      { once: true }
    );
    const listen = $("radioListen");
    if (listen) listen.href = HUB;
    const tv = $("radioTv");
    if (tv) tv.href = TV;
    paint();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
