/* The Archive band - the operator's Wayback Machine search, from the console.
 *
 * Δ9Φ963-LYGO-ARCHIVE-BAND-v1
 *
 * WHAT THIS IS
 *   A band under the chat box that asks the archive module (/api/webarchive) to look a page or a site
 *   up in the Internet Archive, lists the captures the index returned, reads an ARCHIVED copy of a
 *   page into the band, and works a watch list for link rot.
 *
 * WHAT IT DELIBERATELY DOES NOT DO
 *   * It never builds an archive URL by hand and never parses one: the server decides which door to
 *     ask and validates the target (no loopback, no private hosts, no fabricated domains). This file
 *     sends a target and paints the answer.
 *   * It never inserts archived page text as markup. An archived page is somebody else's content:
 *     it arrives as a string and goes into a text node, so a page cannot script this console.
 *   * It never hides a failure behind "no results". A search that could not run comes back with the
 *     server's own `why` and the per-door errors, and the band prints them.
 *   * It never writes a field without telling the field: a value set from a result dispatches an
 *     `input` event, the same event a keystroke would send.
 *   * It never opens a modal dialog for a destructive action: clearing the log is undoable, so the
 *     band offers Undo instead of a box a keyboard user may not be able to dismiss.
 *
 * COST, SAID OUT LOUD
 *   The CDX index takes 7-40 s on a good day. The band says it is asking, and then prints the
 *   seconds the answer cost, because a slow truth beats a fast guess.
 */
(function archiveBand() {
  "use strict";

  var TOKEN_KEY = "lygo-token";

  function token() {
    try {
      return sessionStorage.getItem(TOKEN_KEY) || window.LYGO_TOKEN || "";
    } catch (e) {
      return window.LYGO_TOKEN || "";
    }
  }

  function headers() {
    return {
      "Content-Type": "application/json",
      "X-LYGO-LLM-Token": token(),
      "Cache-Control": "no-store"
    };
  }

  var doc = typeof document !== "undefined" ? document : null;
  if (!doc || !doc.getElementById) return;

  function $(id) {
    return doc.getElementById(id);
  }

  var band = $("wb-band"),
    target = $("wb-target"),
    word = $("wb-word"),
    asof = $("wb-asof"),
    scope = $("wb-scope"),
    go = $("wb-go"),
    watchBtn = $("wb-watch"),
    checkBtn = $("wb-check"),
    forgetBtn = $("wb-forget"),
    undoBtn = $("wb-undo"),
    status = $("wb-status"),
    answer = $("wb-answer"),
    closest = $("wb-closest"),
    results = $("wb-results"),
    readwrap = $("wb-readwrap"),
    readhead = $("wb-readtitle"),
    readbody = $("wb-read"),
    readclose = $("wb-readclose"),
    watchbox = $("wb-watched");

  /* A band that is not on this page is a no-op: the console serves one document, and a module the
     host refused simply has no elements here. */
  if (!band || !target || !go || !status || !answer || !results) return;

  var busy = false;
  var lastRows = [];

  function say(text, warn) {
    status.textContent = String(text);
    if (status.classList && status.classList.toggle) status.classList.toggle("warn", !!warn);
  }

  function clear(node) {
    if (!node) return;
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function setValue(input, value) {
    if (!input) return;
    input.value = value;
    /* A field written from a result is told, exactly as a keystroke would tell it. */
    try {
      input.dispatchEvent(new Event("input", { bubbles: true }));
    } catch (e) {
      /* older engines: the value is still set, only the event is missing */
    }
  }

  function fetchJSON(url, opts) {
    return fetch(url, opts || { headers: headers(), cache: "no-store" }).then(function (r) {
      return r.json().then(function (j) {
        if (j && typeof j === "object") j.__status = r.status;
        return j;
      });
    });
  }

  function post(body) {
    return fetchJSON("/api/webarchive", {
      method: "POST",
      headers: headers(),
      cache: "no-store",
      body: JSON.stringify(body || {})
    }).catch(function (e) {
      return {
        ok: false,
        error: "network",
        why: "the console did not answer: " + String((e && e.message) || e),
        hint: "if the console window is closed the server is gone too - Boot server brings it back"
      };
    });
  }

  /* One place that turns an answer into words. Every failure path uses it, so no branch can print a
     code without its reason. */
  function why(j, fallback) {
    var bits = [];
    if (!j || typeof j !== "object") return fallback || "no answer";
    if (j.error) bits.push(String(j.error));
    if (j.why) bits.push(String(j.why));
    if (j.hint) bits.push(String(j.hint));
    if (j.did_you_mean) bits.push("try " + j.did_you_mean);
    if (j.__status && j.__status >= 400 && !bits.length) bits.push("HTTP " + j.__status);
    return bits.length ? bits.join(" — ") : (fallback || "no reason was given");
  }

  function doorErrors(j) {
    var e = j && j.errors;
    if (!e || !e.length) return "";
    return " (" + e.join("; ") + ")";
  }

  function scoped() {
    return (scope && scope.value) || "site";
  }

  function row(r) {
    r = r || {};
    var li = doc.createElement("li");
    var when = r.date || r.timestamp || "";
    var url = r.original || r.url || "";
    var link = doc.createElement("a");
    link.href = r.snapshot_url || ("https://web.archive.org/web/" + (r.timestamp || "") + "/" + url);
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = url + (when ? "  ·  " + when : "");
    li.appendChild(link);

    var meta = doc.createElement("span");
    meta.className = "wb-meta";
    meta.textContent = " " + (r.statuscode ? "HTTP " + r.statuscode : "") +
      (r.mimetype ? " · " + r.mimetype : "") +
      (r.length ? " · " + r.length + " B" : "");
    li.appendChild(meta);

    var read = doc.createElement("button");
    read.type = "button";
    read.className = "mini";
    read.textContent = "Read";
    read.addEventListener("click", function () {
      readCopy(url, r.timestamp || "");
    });
    li.appendChild(read);

    var pick = doc.createElement("button");
    pick.type = "button";
    pick.className = "mini";
    pick.textContent = "As of";
    pick.addEventListener("click", function () {
      setValue(asof, r.timestamp || "");
      say("as of " + (r.timestamp || "") + " - search again to ask the archive for that date", false);
    });
    li.appendChild(pick);

    var keep = doc.createElement("button");
    keep.type = "button";
    keep.className = "mini";
    keep.textContent = "Watch";
    keep.addEventListener("click", function () {
      watch(url, "");
    });
    li.appendChild(keep);
    return li;
  }

  function paint(j) {
    var near = j.closest || {};
    var snap = near.snapshot || {};
    var bits = [];
    if (snap.url) {
      bits.push("closest capture " + (snap.timestamp || "") + (near.drift ? " (" + near.drift + ")" : ""));
    } else if (near.archived === false) {
      bits.push("the archive has no capture of this URL at all");
    }
    if (near.via) bits.push("via " + near.via);
    bits.push(j.count + " row(s)");
    bits.push((j.seconds || 0) + " s");
    if (j.found_nothing) bits.push("no archived rows for that ask - a finding, not a failure");
    closest.textContent = bits.join(" · ");

    lastRows = j.rows || [];
    clear(results);
    lastRows.forEach(function (r) {
      results.appendChild(row(r));
    });
    if (!lastRows.length) {
      var empty = doc.createElement("li");
      empty.className = "hint";
      empty.textContent = j.found_nothing
        ? "the index returned nothing for that ask"
        : "the answer carried no rows";
      results.appendChild(empty);
    }
    answer.hidden = false;
    if (j.saved && j.saved.saved) {
      say("search kept in the log · " + (j.count || 0) + " row(s) in " + (j.seconds || 0) + " s", false);
    } else {
      say("search ran but was not kept: " + why((j.saved || {}).why ? { why: j.saved.why } : {}, "the log was not written"), true);
    }
  }

  function search() {
    var t = String(target.value || "").trim();
    if (!t) {
      say("give a page or a site first - e.g. chatagent.ca, or https://chatagent.ca/portal/", true);
      return;
    }
    if (busy) return;
    busy = true;
    go.disabled = true;
    say("asking the archive - the index can take up to a minute...", false);
    clear(results);
    closest.textContent = "";
    answer.hidden = false;
    post({
      action: "search",
      target: t,
      word: String((word && word.value) || "").trim(),
      as_of: String((asof && asof.value) || "").trim(),
      scope: scoped(),
      limit: 25
    }).then(function (j) {
      busy = false;
      go.disabled = false;
      if (!j || j.ok !== true) {
        say("the archive did not answer: " + why(j, "no answer") + doorErrors(j), true);
        if (j && j.asked && j.asked.length) {
          var per = j.asked
            .map(function (a) {
              return a.door + (a.ok ? " ok " + (a.seconds || 0) + "s" : " " + a.error);
            })
            .join("; ");
          closest.textContent = "doors asked: " + per;
        }
        return;
      }
      paint(j);
    });
  }

  function readCopy(url, ts) {
    if (!url) {
      say("nothing to read - search first, or put a page in the box", true);
      return;
    }
    say("reading the archived copy...", false);
    post({ action: "read", target: url, timestamp: ts || "" }).then(function (j) {
      if (!j || j.ok !== true) {
        say("no archived copy could be read: " + why(j, "the archive reported nothing"), true);
        return;
      }
      if (j.archived === false) {
        say("the archive has no capture at " + (url || "") + " - " + (j.why || "nothing to read"), true);
        return;
      }
      readwrap.hidden = false;
      readhead.textContent = "archived copy · " + (j.served_timestamp || "") + " · " + (j.bytes || 0) + " bytes" +
        (j.truncated ? " · cut at the limit (the snapshot link has the whole page)" : "");
      /* SOMEBODY ELSE'S PAGE ARRIVES AS TEXT. Never as markup. */
      readbody.textContent = j.text || "(the archived copy was empty)";
      say("read the copy served for " + (j.served_timestamp || "?") + " in " + (j.seconds || 0) + " s", false);
    });
  }

  function watch(url, note) {
    var t = url || String(target.value || "").trim();
    if (!t) {
      say("put a URL in the box first, then watch it", true);
      return;
    }
    say("checking the live web and the archive...", false);
    post({ action: "watch", target: t, note: note || "" }).then(function (j) {
      if (!j || j.ok !== true) {
        say("could not watch that URL: " + why(j, "no answer"), true);
        return;
      }
      var e = j.entry || {};
      say("watching " + (e.url || t) + " - " + findingWords(e), e.finding === "dead");
      paintWatched(j.card);
    });
  }

  function findingWords(e) {
    e = e || {};
    switch (e.finding) {
      case "alive":
        return "it answers on the live web (HTTP " + e.live_status + ")";
      case "dead-but-archived":
        return "it is DEAD and the archive holds a copy from " +
          ((e.archived || {}).timestamp || "an earlier date");
      case "dead":
        return "it is dead and no archived copy was found";
      default:
        return "not checked yet: " + (e.why || e.live_error || "the live ask did not answer");
    }
  }

  function paintWatched(card) {
    if (!watchbox || !card) return;
    clear(watchbox);
    var rows = [];
    (card.groups || []).forEach(function (g) {
      if ((g.title || "").indexOf("Watched") === 0) rows = g.rows || [];
    });
    if (!rows.length) {
      var none = doc.createElement("li");
      none.className = "hint";
      none.textContent = "nothing watched";
      watchbox.appendChild(none);
      return;
    }
    rows.forEach(function (r) {
      var li = doc.createElement("li");
      li.textContent = r.k + " — " + r.v + (r.note ? " (" + r.note + ")" : "");
      watchbox.appendChild(li);
    });
  }

  function check() {
    say("re-checking every watched URL...", false);
    post({ action: "check" }).then(function (j) {
      if (!j || j.ok !== true) {
        say("the watch list could not be checked: " + why(j, "no answer"), true);
        return;
      }
      var rot = j.rot || 0;
      say("checked " + (j.checked || 0) + " watched URL(s): " + rot + " dead but archived" +
        (j.refused && j.refused.length ? " · " + j.refused.length + " unreadable" : ""), rot > 0);
      paintWatched(j.card);
    });
  }

  function forget() {
    post({ action: "forget" }).then(function (j) {
      if (!j || j.ok !== true) {
        say("the log could not be cleared: " + why(j, "no answer"), true);
        return;
      }
      if (undoBtn) undoBtn.hidden = !j.can_restore;
      say("cleared " + (j.cleared || 0) + " search(es) from the log" +
        (j.can_restore ? " - Undo puts them back" : ""), false);
      paintWatched(j.card);
    });
  }

  function undo() {
    post({ action: "restore" }).then(function (j) {
      if (!j || j.ok !== true) {
        say("nothing to restore: " + why(j, "no answer"), true);
        return;
      }
      if (undoBtn) undoBtn.hidden = true;
      say("put " + (j.restored || 0) + " search(es) back in the log", false);
      paintWatched(j.card);
    });
  }

  function init() {
    go.addEventListener("click", search);
    if (target) {
      target.addEventListener("keydown", function (ev) {
        if (ev && ev.key === "Enter") search();
      });
    }
    if (watchBtn) {
      watchBtn.addEventListener("click", function () {
        watch("", "");
      });
    }
    if (checkBtn) checkBtn.addEventListener("click", check);
    if (forgetBtn) forgetBtn.addEventListener("click", forget);
    if (undoBtn) undoBtn.addEventListener("click", undo);
    if (readclose) {
      readclose.addEventListener("click", function () {
        if (readwrap) readwrap.hidden = true;
      });
    }

    /* The archive's own read-out first: a search that cannot run should say so before it is asked,
       not after the operator has waited a minute for it. */
    fetchJSON("/api/webarchiveinfo").then(function (j) {
      var card = (j && j.card) || {};
      if (!card.doors) {
        say("the archive read-out did not answer: " + why(j, "no doors were reported"), true);
        return;
      }
      if (!card.doors_up) {
        say("the Internet Archive is not answering from this machine" +
          (card.state_text ? " - " + card.state_text : "") +
          (card.missing && card.missing.length ? " (" + card.missing[0] + ")" : ""), true);
        return;
      }
      fetchJSON("/api/webarchive").then(function (s) {
        var kept = (s && s.queries ? s.queries.length : 0);
        var watching = (s && s.watched ? s.watched.length : 0);
        say(card.doors_up + "/" + card.doors + " archive doors answered · " +
          kept + " search(es) kept · " + watching + " watched" +
          (card.cached ? " · read-out cached" : ""), false);
        if (s && s.card) paintWatched(s.card);
      });
    });
  }

  if (doc.readyState === "loading" && doc.addEventListener) {
    doc.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
