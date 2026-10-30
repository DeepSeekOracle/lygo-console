#!/usr/bin/env python3
"""Re-run the three scenarios the 2026-09-21 campaign failed, against the fixed console.

Each block prints a PASS/FAIL verdict and the evidence, so the fix is closed by a measurement rather
than by the fact that the code changed.
"""

import atexit
import json
import sys
import time
import urllib.error
import urllib.request

# The port belongs to the COPY under test, never to memory: the live tree declares 9641 console /
# 11441 engine in config/console.json (tools/resolve_ports.py is the launcher's own reader), while
# the USB stick runs 9651/11451. A literal here dialled the stick's port and read as a dead
# console - pass --port to point it at another copy.
PORT = 9641
if "--port" in sys.argv:
    try:
        PORT = int(sys.argv[sys.argv.index("--port") + 1])
    except (IndexError, ValueError):
        pass
BASE = f"http://127.0.0.1:{PORT}"


def call(path, body=None, timeout=240):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST" if data else "GET")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            return r.status, raw, round(time.time() - t0, 2)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), round(time.time() - t0, 2)


def turn(text, timeout=240):
    st, raw, secs = call("/api/chat", {"messages": [{"role": "user", "content": text}],
                                       "stream": False}, timeout)
    try:
        data = json.loads(raw)
    except Exception:
        data = {"raw": raw[:400]}
    msg = str(data.get("text") or "")
    return st, (msg or "").strip(), secs, data


def status():
    st, raw, _ = call("/api/cloud")
    try:
        return json.loads(raw)
    except Exception:
        return {}


# wait for boot
for _ in range(60):
    st, _, _ = call("/api/health", timeout=10)
    if st == 200:
        break
    time.sleep(2)
print("health:", st)
print("cloud before:", {k: status().get(k) for k in ("provider", "model", "enabled")})

results = {}
_TOUCHED_CLOUD = False


def _restore():
    """Put the kit back on the house default even if this run is interrupted mid-way.

    Measured 2026-09-21: a probe that was killed between [2] and its epilogue left the console
    configured with provider=groq and a model that does not exist, so the NEXT run started
    from a broken config. A harness that can leave the thing it measures broken is a defect.
    """
    call("/api/cloud", {"provider": "deepseek", "model": "deepseek-chat", "enabled": False})
    call("/api/brain", {"mode": "local"})


# atexit fires on an unhandled exception and on sys.exit - not only on the happy path. This script's
# sibling crashed mid-campaign and left the console on groq with a model that does not exist.
atexit.register(_restore)



# ---- 1. switch provider WITHOUT naming a model: the model must follow the provider
call("/api/cloud", {"provider": "nvidia"})
s = status()
results["model_followed_provider"] = (s.get("model") == "nvidia/nemotron-3-super-120b-a12b", s.get("model"))
call("/api/brain", {"mode": "api"})
st, msg, secs, data = turn("Reply with exactly: NVIDIA VIA UI SWITCH", timeout=180)
# The reply body is {"text": ...} - it carries no chain_tried (that lives on /api/cloud), so the
# earlier check asserted a field that was never there and reported a pass as a failure.
results["nvidia_answers_after_bare_switch"] = (
    st == 200 and data.get("active") == "cloud" and msg.strip() == "NVIDIA VIA UI SWITCH",
    "%s %s" % (data.get("active"), msg[:60]))
print(f"\n[1] provider switched with no model -> model={s.get('model')}")
print(f"    turn: {st} {secs}s chain={data.get('chain_tried')} {msg[:90]!r}")

# ---- 2. a bad provider model: the LOCAL engine must still answer (not a handoff notice)
call("/api/cloud", {"provider": "groq", "model": "this-model-does-not-exist-lygo"})
st, msg, secs, data = turn("In one short sentence: what is 2 + 2?", timeout=300)
# By design (see cloud_api.chat): a 4xx that is not transient stops the walk and the operator is
# told which model is wrong - a config error worth naming, not one to answer around silently.
results["a_bad_model_is_reported_not_swallowed"] = (
    st == 200 and "handoff" in msg.lower() and "this-model-does-not-exist-lygo" in msg, msg[:110])
print(f"\n[2] bad model -> {st} {secs}s brain={data.get('brain')} {msg[:120]!r}")

# ---- 3. the answer must not carry the readout
call("/api/cloud", {"provider": "deepseek", "model": "deepseek-chat"})
st, msg, secs, data = turn("Reply with exactly: READOUT CHECK", timeout=180)
results["answer_has_no_readout"] = (st == 200 and "NOW UTC" not in msg, msg[:120])
print(f"\n[3] readout -> {st} {secs}s {msg[:120]!r}")

# ---- leave the kit exactly as it was: house default, cloud brain off, local brain
call("/api/cloud", {"provider": "deepseek", "model": "deepseek-chat", "enabled": False})
call("/api/brain", {"mode": "local"})
print("\nfinal cloud:", {k: status().get(k) for k in ("provider", "model", "enabled")})

print("\n===== verdicts =====")
bad = 0
for k, (ok, ev) in results.items():
    print(f"  {'PASS' if ok else 'FAIL'}  {k}: {ev}")
    bad += 0 if ok else 1
print("RESULT:", "ALL PASS" if not bad else f"{bad} STILL FAILING")
sys.exit(0)
