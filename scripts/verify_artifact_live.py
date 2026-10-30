#!/usr/bin/env python3
"""Does a picture the turn produced get named in the answer?

Row 76, measured live on 2026-09-21: the operator asked the API brain for a picture and the answer was
a promise - "I'll render it now - first run loads the checkpoint, so give it a minute." The limb did
produce a real PNG (23.6s, 957,430 bytes); the answer never said where it was. This asks the same thing
and insists the reply names a file that is really on disk.

Restores the console's config through atexit, so a crash on any step cannot leave it on a half-set
provider.
"""

from __future__ import annotations

import atexit
import json
import os
import re
import sys
import time
import urllib.request

ARGV = list(sys.argv)
PORT = 9641
if "--port" in ARGV:
    try:
        PORT = int(ARGV[ARGV.index("--port") + 1])
    except (IndexError, ValueError):
        pass
BASE = "http://127.0.0.1:%d" % PORT


def call(path: str, obj: dict | None = None, timeout: int = 300):
    data = json.dumps(obj).encode("utf-8") if obj is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode("utf-8", "replace"))


def _restore() -> None:
    """Leave the kit exactly as it was found, whatever happened."""
    try:
        call("/api/cloud", {"provider": "deepseek", "model": "deepseek-chat", "enabled": False}, timeout=30)
        call("/api/brain", {"mode": "local"}, timeout=30)
        print("restored: provider=deepseek model=deepseek-chat enabled=False brain=local")
    except Exception as exc:
        print("restore failed: %r" % (exc,))


atexit.register(_restore)


def main() -> int:
    code = None
    for _ in range(40):
        try:
            code, _j = call("/api/health", timeout=8)
            if code == 200:
                break
        except Exception:
            time.sleep(2)
    print("health:", code)
    if code != 200:
        print("VERDICT: FAIL - the console is not answering on %s" % BASE)
        return 1

    before = call("/api/cloud")[1].get("provider")
    print("cloud before:", before)
    call("/api/brain", {"mode": "api"})
    call("/api/cloud", {"provider": "deepseek", "model": "deepseek-chat", "enabled": True})

    ask = ("Use your image_generate limb to make one small picture - 256x256 is plenty - and then tell "
           "me you have done it. Do not describe the picture, just make it.")
    t0 = time.time()
    try:
        st, j = call("/api/chat", {"messages": [{"role": "user", "content": ask}], "stream": False},
                     timeout=900)
    except Exception as exc:
        print("turn failed: %r" % (exc,))
        print("VERDICT: FAIL - the turn did not complete")
        return 1
    secs = round(time.time() - t0, 1)
    text = str(j.get("text") or "")
    print("turn: %s %ss brain=%s" % (st, secs, j.get("brain")))
    print("answer: %r" % (text[:500],))
    traces = j.get("traces") or []
    made = []
    for t in traces:
        res = t.get("result") if isinstance(t, dict) else None
        if isinstance(res, dict) and res.get("ok") and str(res.get("path") or "").lower().endswith(
                (".png", ".jpg", ".jpeg", ".webp")):
            made.append(str(res["path"]))
    print("limb wrote:", made or "(nothing seen in the reply's traces)")

    # Judge the paths the LIMB reported. Splitting the answer on whitespace cannot work here: this
    # kit lives under "<EDRIVE>\...", so a path with a space in it parses as two fragments and a
    # produced, correctly-named picture reads as missing (measured 2026-09-21).
    named_ok = bool(made) and all(p in text for p in made)
    disk_ok = bool(made) and all(os.path.isfile(p) for p in made)
    ok = named_ok and disk_ok
    print("named in the answer:", named_ok, [(p, p in text) for p in made])
    print("present on disk:", disk_ok, [(p, os.path.isfile(p)) for p in made])
    print("VERDICT: %s" % ("PASS - the answer names the picture it produced" if ok
                           else "FAIL - a produced picture was not named in the answer"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
