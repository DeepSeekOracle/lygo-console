/* One sound source at a time: radio, Song Studio takes, and VoiceStudio clips
   must never play over each other. The Song Studio only paused its own sibling
   audios, so the footer radio could keep running underneath it. */
(function () {
  function pauseOthers(active) {
    document.querySelectorAll("audio").forEach(function (audio) {
      if (audio !== active && !audio.paused) audio.pause();
    });
    // Keep the radio dock honest when a song (or a voice clip) took over.
    var radio = document.getElementById("radioEl");
    var play = document.getElementById("radioPlay");
    if (radio && radio !== active && radio.paused && play) play.textContent = "Play";
  }

  document.addEventListener("play", function (event) {
    var el = event.target;
    if (!el || el.tagName !== "AUDIO") return;
    pauseOthers(el);
  }, true);
})();
