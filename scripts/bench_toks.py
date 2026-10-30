"""tok/s benchmark for this console - read-only, re-runnable, prints the config with the numbers.

Run it while the console is up (LYGO_LLM_CONSOLE.bat). Nothing here starts, stops or reconfigures
anything: it asks the running console and the running engine what they are, then times them.

    python scripts/bench_toks.py                # default ports from config
    LYGO_CONSOLE_PORT=9741 python scripts/bench_toks.py

What it measures, and why each one is a separate row:

  * gen        - 3 short completions through the console's OpenAI proxy. Steady-state generation.
  * prefill    - one realistic long prompt (never padded: the console's own P0 gate refuses a
                 synthetic 12k+ payload, and rightly so). Prompt-processing speed.
  * at depth   - generation from the end of that long prompt, i.e. what a real agent turn feels like.
  * turn       - one full /api/chat turn with tools on: the wall clock the operator actually waits,
                 plus what the engine did inside that turn (`perf`).
  * no tools   - the same turn with "tools": false, which used to answer HTTP 500.

Two different sources, on purpose:

  * /v1/chat/completions is a straight proxy, so the response carries llama.cpp's own `timings`.
  * /api/chat is the gated agent turn (system prompt, tool schema, the P0 gate). It reports the
    engine's own numbers back under `perf`, which is what this console added in the 2026-09-18
    performance pass - a turn now says how fast it ran without anyone re-benchmarking it.

Numbers come from llama.cpp's own `timings` block, not from wall-clock arithmetic, so a slow
client cannot make the engine look slow. Every figure is reported with the config it belongs to.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

CONSOLE = f"http://127.0.0.1:{os.environ.get('LYGO_CONSOLE_PORT') or 9641}"
ENGINE_PORT = os.environ.get("LYGO_LLAMA_PORT") or 11441
ENGINE = f"http://127.0.0.1:{ENGINE_PORT}"

PROMPT = "In two sentences, explain what a KV cache does in a llama.cpp inference server."
LONG_PROMPT = (
    "Here is an extract from the LYGO protocol stack whitepaper. Read it, then answer the "
    "question at the end.\n\n"
    "The console is one folder. Nothing in it is bound to a drive letter, so the same folder runs "
    "from a USB stick, from a second drive, or beside another copy on this machine. The launcher "
    "reads config/console.json, then overlays config/local.json if it exists, then lets an "
    "exported LYGO_*_PORT variable beat both. The engine is llama-server, shipped in the kit, with "
    "backend overlays that are proven on this host before they are used: a candidate build that "
    "fails its own self-test never becomes layers. The agent portal talks to the console over "
    "loopback only, and a bind to any other address is refused unless the operator starts it with "
    "an explicit consent flag, so a public bind is never a silent side effect. Every turn writes a "
    "receipt, the P0 gate quarantines a payload that looks like an attempt to lift the steward's "
    "own policy text back out of the system prompt, and the brain can hand a turn to the cloud "
    "brain when the operator asks for it and the API boost is armed.\n\n"
    "Question: reply with only the number 391, nothing else."
)
DEPTH_ASK = "With that extract in mind, what is 17 * 23? Reply with the number only."


def post(url: str, payload: dict, timeout: float = 900.0) -> tuple[int, bytes]:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # noqa: BLE001 - a benchmark must report, not crash
        return 0, str(e).encode()


def get(url: str, timeout: float = 20.0) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # noqa: BLE001
        return 0, str(e).encode()


def timings(body: bytes) -> dict:
    try:
        t = (json.loads(body.decode("utf-8")) or {}).get("timings") or {}
    except Exception:  # noqa: BLE001
        return {}
    if not t.get("predicted_n") and not t.get("prompt_n"):
        return {}
    return {
        "gen_n": int(t.get("predicted_n") or 0),
        "gen_tok_s": round(float(t.get("predicted_per_second") or 0), 1),
        "prompt_n": int(t.get("prompt_n") or 0),
        "prompt_tok_s": round(float(t.get("prompt_per_second") or 0), 1),
    }


def gen(prompt: str, max_tokens: int) -> dict:
    """Through the OpenAI proxy: the engine's own `timings` come back verbatim."""
    payload = {"messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens, "stream": False}
    t0 = time.time()
    code, body = post(f"{CONSOLE}/v1/chat/completions", payload)
    wall = round(time.time() - t0, 2)
    if code != 200:
        return {"fail": f"HTTP {code}", "detail": body[:200].decode("utf-8", "replace"), "wall_s": wall}
    out = timings(body)
    out["wall_s"] = wall
    return out


def turn(prompt: str, max_tokens: int, *, tools: bool | None = None) -> dict:
    """A real gated agent turn. Answers under `perf` (added 2026-09-18)."""
    payload = {"messages": [{"role": "user", "content": prompt}], "stream": False, "max_tokens": max_tokens}
    if tools is not None:
        payload["tools"] = tools
    t0 = time.time()
    code, body = post(f"{CONSOLE}/api/chat", payload)
    wall = round(time.time() - t0, 2)
    if code != 200:
        return {"fail": f"HTTP {code}", "detail": body[:200].decode("utf-8", "replace"), "wall_s": wall}
    out: dict = {}
    try:
        doc = json.loads(body.decode("utf-8"))
        out = dict(doc.get("perf") or {})
        out["text_chars"] = len(str(doc.get("text") or ""))
    except Exception:  # noqa: BLE001
        out = {}
    out["wall_s"] = wall
    return out


def config() -> dict:
    code, body = get(f"{CONSOLE}/api/health")
    if code != 200:
        return {"health": f"HTTP {code}"}
    h = json.loads(body.decode("utf-8"))
    p = h.get("perf") or {}
    return {
        "console": h.get("build"),
        "brain": h.get("selected"),
        "brain_ready": h.get("brain"),
        "backend_layer": (h.get("backend_layer") or {}).get("active"),
        "mode": p.get("mode"),
        "ngl": p.get("ngl"),
        "threads": p.get("threads"),
        "kv_mib": p.get("kv_mib"),
        "effective": p.get("effective"),
        "ctx": (p.get("effective") or {}).get("ctx") or p.get("ctx"),
    }


def main() -> int:
    print(f"console {CONSOLE}  engine {ENGINE}")
    cfg = config()
    if cfg.get("health"):
        print(f"console is not answering ({cfg['health']}) - start LYGO_LLM_CONSOLE.bat first")
        return 1
    print(json.dumps({k: v for k, v in cfg.items() if v not in (None, "", {})}, indent=1))
    if cfg.get("brain_ready") != "ready":
        print(f"brain is '{cfg.get('brain_ready')}' - nothing to measure until it is ready")
        return 1

    record: dict = {"measured_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "config": cfg}

    gens = []
    for i in range(3):
        r = gen(PROMPT, 220)
        gens.append(r)
        print(f"gen    run {i + 1}: {r.get('gen_tok_s', '-')} tok/s ({r.get('gen_n')} tokens) {r.get('fail') or ''}")
    good = [g for g in gens if g.get("gen_tok_s")]
    if good:
        record["gen"] = {
            "runs": [g["gen_tok_s"] for g in good],
            "mean_tok_s": round(sum(g["gen_tok_s"] for g in good) / len(good), 1),
        }
        print(f"gen    mean: {record['gen']['mean_tok_s']} tok/s over {len(good)} runs")

    depth = gen(LONG_PROMPT + "\n\n" + DEPTH_ASK, 64)
    record["at_depth"] = depth
    print(f"depth  gen {depth.get('gen_tok_s', '-')} tok/s | prefill {depth.get('prompt_tok_s', '-')} tok/s "
          f"({depth.get('prompt_n')} prompt tokens)")

    t = turn("Reply with exactly the word: ready", 32, tools=True)
    record["turn"] = t
    print(f"turn   /api/chat tools=true: wall {t.get('wall_s', '-')}s, gen {t.get('gen_tok_s', '-')} tok/s, "
          f"prompt {t.get('prompt_tokens', '-')} tokens, {t.get('engine_calls', '-')} engine call(s)")

    nt = turn("Reply with exactly the word: ready", 32, tools=False)
    record["turn_without_tools"] = nt
    print(f"turn   /api/chat tools=false: HTTP {'200' if not nt.get('fail') else nt['fail']}"
          f" (this answered 500 handler_failed before the tuning pass)")

    dest = ROOT / "save" / "logs" / f"bench_toks_{time.strftime('%Y%m%d_%H%M%S')}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(record, indent=1), encoding="utf-8")
    print(f"\nrecord -> {dest}")
    failed = [k for k, v in record.items() if isinstance(v, dict) and v.get("fail")]
    if failed:
        print(f"cases that failed: {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
