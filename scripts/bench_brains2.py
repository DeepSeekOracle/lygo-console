#!/usr/bin/env python3
"""Finish the campaign: each provider through the console with its own model, Gemini vision, API-draws.

Part two exists because part one died in its own harness (a base64 image in a Windows command line,
WinError 206), and because part one sent every provider the *previous* provider's model, so all four
console turns came back 404. Here the model is set explicitly alongside the provider - which is a
setting, not a code change - so the question "does provider X answer through the console?" is asked
cleanly. The 404s remain logged as the defect they exposed.

    python scripts/bench_brains2.py --port 9651
"""

from __future__ import annotations

import base64
import atexit
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT / "src"))
OUT = KIT / "workspace" / "memory"
PORT = 9651


def http(path: str, body: dict | None = None, timeout: int = 900) -> tuple[dict, float]:
    url = f"http://127.0.0.1:{PORT}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
    except Exception as e:
        raw = json.dumps({"_error": repr(e)[:200]})
    try:
        got = json.loads(raw)
    except Exception:
        got = {"_raw": raw[:300]}
    return got, time.time() - t0


def log(kind: str, **f) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / f"test-campaign-{time.strftime('%Y-%m-%d')}.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind, **f}, ensure_ascii=False) + "\n")
    print(f"{time.strftime('%H:%M:%S')} {kind:22} " +
          "  ".join(f"{k}={v}" for k, v in f.items() if k not in ("answer", "detail")), flush=True)


def main() -> int:
    global PORT
    if "--port" in sys.argv:
        PORT = int(sys.argv[sys.argv.index("--port") + 1])
    import cloud_api

    print("== part two: providers through the console, with the provider's own model ==", flush=True)
    try:
        for entry in cloud_api.chain_for():
            prov = entry["provider"]
            model = cloud_api.PROVIDERS[prov]["model"]
            http("/api/cloud", {"provider": prov, "model": model}, timeout=60)
            http("/api/brain", {"mode": "api"}, timeout=60)
            state, _ = http("/api/cloud", timeout=60)
            got, secs = http("/api/chat", {"stream": False, "messages": [
                {"role": "user", "content": f"[BENCH] reply with exactly: {prov.upper()} CONSOLE OK"}]})
            log("console_provider_own_model", provider=prov, model_set=state.get("model"),
                seconds=round(secs, 2), brain=got.get("brain"), chars=len(str(got.get("text") or "")),
                answer=str(got.get("text") or "")[:90], error=str(got.get("error") or "")[:80])
        http("/api/cloud", {"provider": "deepseek", "model": "deepseek-chat"}, timeout=60)

        # --- pictures: newest generated file ---
        pngs = sorted((KIT / "workspace" / "images").glob("*.png"), key=lambda p: p.stat().st_mtime)
        made = str(pngs[-1]) if pngs else ""
        log("picked_image", path=made, bytes=Path(made).stat().st_size if made else 0)

        if made:
            # (a) our own engine reads it - recorded again here so both readers sit side by side
            seen, secs = http("/api/limb", {"name": "image_see",
                                            "arguments": {"path": made, "prompt": "one sentence please"}}, timeout=900)
            log("image_see_local", seconds=round(secs, 1), ok=bool(seen.get("ok")),
                text=str(seen.get("text") or "")[:220], error=str(seen.get("error") or "")[:120])

            # (b) the API brain reads it. The body goes in the request, never on a command line.
            http("/api/cloud", {"provider": "gemini", "model": cloud_api.PROVIDERS["gemini"]["model"]}, timeout=60)
            http("/api/brain", {"mode": "api"}, timeout=60)
            b64 = base64.b64encode(Path(made).read_bytes()).decode()
            got, secs = http("/api/chat", {"stream": False, "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text": "What is in this image? One sentence."}]}]}, timeout=600)
            log("image_see_api", seconds=round(secs, 1), brain=got.get("brain"),
                ok=bool(str(got.get("text") or "").strip()), chars=len(str(got.get("text") or "")),
                text=str(got.get("text") or "")[:220], error=str(got.get("error") or "")[:120])

            # (c) the API brain asks for a picture: does it reach the image limb and return a file?
            http("/api/cloud", {"provider": "deepseek", "model": "deepseek-chat"}, timeout=60)
            before = {p.name for p in (KIT / "workspace" / "images").glob("*.png")}
            got, secs = http("/api/chat", {"stream": False, "messages": [
                {"role": "user", "content": "Draw a small picture of a blue crystal and tell me where you "
                                            "saved it."}]}, timeout=1200)
            after = {p.name for p in (KIT / "workspace" / "images").glob("*.png")}
            log("api_asks_for_image", seconds=round(secs, 1), brain=got.get("brain"),
                chars=len(str(got.get("text") or "")), new_files=sorted(after - before),
                answer=str(got.get("text") or "")[:200], error=str(got.get("error") or "")[:120])
    finally:
        http("/api/cloud", {"provider": "deepseek", "model": "deepseek-chat"}, timeout=60)
        http("/api/brain", {"mode": "local"}, timeout=60)
        end, _ = http("/api/cloud", timeout=60)
        log("restored", provider=end.get("provider"), model=end.get("model"), enabled=end.get("enabled"),
            keys=end.get("keys_wired"))
    print("\nlog:", OUT / f"test-campaign-{time.strftime('%Y-%m-%d')}.jsonl")
    return 0


def _restore():
    """House default even if this run dies part-way (stdlib only: it may die before its own helpers)."""
    import json as _j
    import sys as _s
    import urllib.request as _u

    port = 9651
    if "--port" in _s.argv:
        try:
            port = int(_s.argv[_s.argv.index("--port") + 1])
        except (IndexError, ValueError):
            pass
    base = "http://127.0.0.1:%d" % port
    for path, body in (("/api/cloud", {"provider": "deepseek", "model": "deepseek-chat",
                                       "enabled": False}),
                       ("/api/brain", {"mode": "local"})):
        try:
            _u.urlopen(_u.Request(base + path, data=_j.dumps(body).encode(),
                                  headers={"Content-Type": "application/json"}), timeout=20).read()
        except Exception:
            pass


atexit.register(_restore)


if __name__ == "__main__":
    raise SystemExit(main())
