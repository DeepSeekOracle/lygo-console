#!/usr/bin/env python3
"""Put the console's own two agents to work, and measure whether they know their environment.

The kit is meant to be developed *with* its own agents, so this asks each brain the same questions and
then hands each one a real task. The answers are printed verbatim - the point is to see what they
actually say, not to grade them into a pass.
"""

import atexit
import json
import sys
import time
import urllib.error
import urllib.request

PORT = 9651
BASE = f"http://127.0.0.1:{PORT}"


def call(path, body=None, timeout=420):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST" if data else "GET")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace"), round(time.time() - t0, 2)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), round(time.time() - t0, 2)


def ask(text, timeout=420):
    st, raw, secs = call("/api/chat", {"messages": [{"role": "user", "content": text}], "stream": False}, timeout)
    try:
        data = json.loads(raw)
    except Exception:
        return st, "", secs, {}
    msg = str(data.get("text") or "")
    return st, (msg or "").strip(), secs, data


def brain(mode):
    call("/api/brain", {"mode": mode})
    time.sleep(1)


SELF = ("In three short lines, describe your own working environment: which console you are running "
        "inside, which port it serves, which engine and model you are, and two things you can actually "
        "do (limbs included). Do not guess - if you are unsure of something, say so.")

TASK_LOCAL = ("Use your limbs to inspect the project you are running from. Report: (a) the exact result "
              "your limb returned, (b) what you can and cannot see from inside. Be literal.")

TASK_API = ("Here is a changed line from the console you are part of:\n"
            "    payload = cloud_api.local_payload_from(payload, str(local_model))\n"
            "It sits where the API failed and the local engine takes over. In two sentences: what does "
            "this line prevent, and what could it break?")

print("=" * 78)
print("PART A - the local agent on itself")
print("=" * 78)
brain("local")
st, msg, secs, data = ask(SELF, timeout=300)
print(f"[{st} {secs}s brain={data.get('brain')} model={data.get('model')}]")
print(msg or "(no text)")

print()
print("=" * 78)
print("PART B - the API agent on itself")
print("=" * 78)
brain("api")
st, msg, secs, data = ask(SELF, timeout=300)
print(f"[{st} {secs}s brain={data.get('brain')} model={data.get('model')} chain={data.get('chain_tried')}]")
print(msg or "(no text)")

print()
print("=" * 78)
print("PART C - a real task for the local agent (limbs)")
print("=" * 78)
brain("local")
st, msg, secs, data = ask(TASK_LOCAL, timeout=420)
print(f"[{st} {secs}s brain={data.get('brain')} tools={data.get('tool_rounds')}]")
print(msg or "(no text)")

print()
print("=" * 78)
print("PART D - a real review task for the API agent")
print("=" * 78)
brain("api")
st, msg, secs, data = ask(TASK_API, timeout=300)
print(f"[{st} {secs}s brain={data.get('brain')} chain={data.get('chain_tried')}]")
print(msg or "(no text)")

def _restore():
    """House default, run even if a probe above dies part-way."""
    call("/api/cloud", {"provider": "deepseek", "model": "deepseek-chat", "enabled": False})
    call("/api/brain", {"mode": "local"})


atexit.register(_restore)
sys.exit(0)
