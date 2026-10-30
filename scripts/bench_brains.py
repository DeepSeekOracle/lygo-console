#!/usr/bin/env python3
"""Test campaign: the LOCAL brain, the API brain, switching between them, and images both ways.

Runs against a LIVE console on --port (default 9651) over its own HTTP routes - the same paths the
portal uses - and writes every result to workspace/memory/test-campaign-<date>.jsonl plus a table on
stdout. Nothing is fixed from in here: this measures and records, the operator reviews the log.

    python scripts/bench_brains.py --port 9651

It restores the console to the house default (provider deepseek, cloud brain off) when it finishes,
including when a step fails.
"""

from __future__ import annotations

import argparse
import atexit
import json
import subprocess
import sys
import time
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT / "src"))
OUT = KIT / "workspace" / "memory"

REPS_DIRECT = 2
REPS_LOCAL = 3
PORT = 9651


def curl(path: str, body: dict | None = None, timeout: int = 300) -> tuple[str, float]:
    url = path if path.startswith("http") else f"http://127.0.0.1:{PORT}{path}"
    cmd = ["curl", "-s", "-m", str(timeout), url]
    if body is not None:
        cmd = ["curl", "-s", "-m", str(timeout), "-X", "POST", url,
               "-H", "Content-Type: application/json", "--data-binary", json.dumps(body)]
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout.strip(), time.time() - t0


def jget(path: str, timeout: int = 60) -> dict:
    txt, _ = curl(path, timeout=timeout)
    try:
        got = json.loads(txt)
        return got if isinstance(got, dict) else {"_": got}
    except Exception:
        return {"_raw": txt[:300]}


def log(kind: str, **fields) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    row = {"t": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind, **fields}
    with (OUT / f"test-campaign-{time.strftime('%Y-%m-%d')}.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    bits = "  ".join(f"{k}={v}" for k, v in fields.items() if k not in ("answer", "detail"))
    print(f"{time.strftime('%H:%M:%S')} {kind:22} {bits}", flush=True)


def turn(prompt: str, use_api: bool | None = None, label: str = "", timeout: int = 420) -> dict:
    body: dict = {"messages": [{"role": "user", "content": prompt}], "stream": False}
    if use_api is not None:
        body["use_api"] = use_api
    txt, secs = curl("/api/chat", body, timeout=timeout)
    try:
        got = json.loads(txt)
    except Exception:
        got = {"_raw": txt[:200]}
    answer = str(got.get("text") or "")
    log("turn", label=label, asked=prompt[:44], use_api=use_api, seconds=round(secs, 2),
        brain=got.get("brain"), chars=len(answer), answer=answer[:160],
        error=got.get("error") or "")
    return {"seconds": secs, **got}


def _restore():
    """Put the kit back on the house default even if this campaign dies mid-way.

    Measured 2026-09-21: this script crashed at its image step (WinError 206 - a base64 image on
    a Windows command line) and, because the restore sat at the end of the happy path, the console
    was left configured with provider=groq and a model that does not exist. The next run started
    from that broken config. A harness that can break what it measures is a defect.
    """
    try:
        curl("/api/cloud", {"provider": "deepseek", "model": "deepseek-chat", "enabled": False})
        curl("/api/brain", {"mode": "local"})
    except Exception as exc:
        print("restore failed: %r" % (exc,), flush=True)


def main() -> int:
    global PORT
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9651)
    args = ap.parse_args()
    PORT = args.port

    print("=" * 78)
    print("LYGO console test campaign - local vs api, switching, images")
    print("=" * 78, flush=True)

    health = jget("/api/health")
    hw = (health.get("hardware") or {}).get("gpus") or [{}]
    log("system", brain=health.get("brain"), model=health.get("selected"),
        free_mib=hw[0].get("free_mib"), total_mib=hw[0].get("total_mib"))

    try:
        # ---------------------------------------------------------------- providers, direct
        import cloud_api
        st = cloud_api.public_status()
        log("cloud_state", enabled=st.get("enabled"), provider=st.get("provider"),
            model=st.get("model"), keys=st.get("keys_wired"), chain=st.get("chain"))
        # Each provider is asked straight at its own endpoint with its own wired key, so this measures
        # the vendor and the key rather than the console's chain.
        import urllib.error
        import urllib.request
        for entry in cloud_api.chain_for():
            prov = entry["provider"]
            payload = {"model": entry["model"], "max_tokens": 64, "stream": False,
                       "messages": [{"role": "user", "content": f"[BENCH] reply with exactly: {prov.upper()} OK"}]}
            payload.update(entry.get("body") or {})
            headers = {"Content-Type": "application/json", "Authorization": f"Bearer {entry['key']}"}
            headers.update(entry.get("extra") or {})
            for rep in range(REPS_DIRECT):
                t0 = time.time()
                code, answer, err = 0, "", ""
                try:
                    req = urllib.request.Request(entry["url"], data=json.dumps(payload).encode(),
                                                 headers=headers, method="POST")
                    with urllib.request.urlopen(req, timeout=120) as resp:
                        code = resp.status
                        body = json.loads(resp.read().decode("utf-8", "replace"))
                    answer = str(((body.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
                    if not answer.strip():
                        rc = ((body.get("choices") or [{}])[0].get("message") or {}).get("reasoning_content")
                        err = "empty content" + (" (reasoning only)" if rc else "")
                except urllib.error.HTTPError as exc:
                    code = exc.code
                    err = exc.read().decode("utf-8", "replace")[:140]
                except Exception as exc:
                    err = repr(exc)[:140]
                log("api_direct", provider=prov, rep=rep + 1, code=code, seconds=round(time.time() - t0, 2),
                    chars=len(answer), answer=answer.strip()[:80], error=err)

        # ---------------------------------------------------------------- the local brain
        for rep, prompt in enumerate([
            "[BENCH] reply with exactly: LOCAL OK",
            "[BENCH] in one short sentence, what is 17 times 3?",
            "[BENCH] list three words that rhyme with 'stack', comma separated",
        ]):
            turn(prompt, use_api=False, label=f"local rep{rep + 1}")

        # ---------------------------------------------------------------- each provider through the console
        for entry in cloud_api.chain_for():
            prov = entry["provider"]
            save = jget("/api/cloud")
            txt, _ = curl("/api/cloud", {"provider": prov}, timeout=60)
            curl("/api/brain", {"mode": "api"}, timeout=60)
            before = jget("/api/cloud")
            turn(f"[BENCH via console] reply with exactly: {prov.upper()} CONSOLE OK",
                 label=f"api console {prov}", timeout=420)
            after = jget("/api/cloud")
            log("console_provider", provider=prov, set_to=before.get("provider"),
                model=before.get("model"), answered_by=after.get("last_provider"),
                chain_tried=after.get("chain_tried"))
        curl("/api/cloud", {"provider": "deepseek"}, timeout=60)

        # ---------------------------------------------------------------- switching back and forth
        for i in range(3):
            curl("/api/brain", {"mode": "local"}, timeout=60)
            r1 = turn(f"[BENCH] switch cycle {i + 1}: reply with exactly LOCAL SIDE", use_api=False,
                      label=f"cycle {i + 1} local")
            curl("/api/brain", {"mode": "api"}, timeout=60)
            r2 = turn(f"[BENCH] switch cycle {i + 1}: reply with exactly API SIDE", use_api=None,
                      label=f"cycle {i + 1} api")
            state_local = jget("/api/cloud")
            log("cycle", n=i + 1, local_brain=r1.get("brain"), api_brain=r2.get("brain"),
                api_enabled=state_local.get("enabled"), answered_by=state_local.get("last_provider"))
        curl("/api/brain", {"mode": "local"}, timeout=60)

        # ---------------------------------------------------------------- a refusal must hand the turn back
        cfg = KIT / "config" / "api.json"
        backup = cfg.read_text(encoding="utf-8")
        try:
            bad = json.loads(backup)
            bad["provider"] = "groq"
            bad["model"] = "this-model-does-not-exist-lygo"
            cfg.write_text(json.dumps(bad, indent=2), encoding="utf-8")
            curl("/api/brain", {"mode": "api"}, timeout=60)
            r = turn("[BENCH] failover: reply with exactly FAILOVER OK", label="api forced to fail")
            log("failover", brain=r.get("brain"), ok=bool(r.get("text")),
                answer=str(r.get("text") or "")[:80])
        finally:
            cfg.write_text(backup, encoding="utf-8")
            curl("/api/brain", {"mode": "local"}, timeout=60)

        # ---------------------------------------------------------------- images
        tools = jget("/api/tools", timeout=60)
        schema = None
        for t in (tools.get("tools") or []):
            nm = (t.get("function") or {}).get("name") or t.get("name")
            if nm == "image_generate":
                schema = t
                break
        log("image_schema", found=bool(schema), detail=json.dumps(schema)[:400] if schema else "")

        t0 = time.time()
        img, _ = curl("/api/limb", {"name": "image_generate",
                                    "arguments": {"prompt": "a small lygo crystal, flat icon, plain background",
                                                  "width": 256, "height": 256, "steps": 8}}, timeout=900)
        try:
            got = json.loads(img)
        except Exception:
            got = {"_raw": img[:300]}
        log("image_generate", seconds=round(time.time() - t0, 1), ok=bool(got.get("ok")),
            path=str(got.get("path") or got.get("file") or "")[:120],
            recipe=got.get("recipe"), route=got.get("route"), error=str(got.get("error") or "")[:150],
            detail=json.dumps(got)[:300])

        named, _ = curl("/api/limb", {"name": "image_generate", "arguments": {
            "prompt": "a small lygo crystal, flat icon, plain background", "width": 256, "height": 256,
            "model": "qwen-image-2.1"}}, timeout=1200)
        try:
            ng = json.loads(named)
        except Exception:
            ng = {"_raw": named[:300]}
        log("image_generate_named", ok=bool(ng.get("ok")), model="qwen-image-2.1",
            path=str(ng.get("path") or ng.get("file") or "")[:120], error=str(ng.get("error") or "")[:150],
            detail=json.dumps(ng)[:240])

        made = str(got.get("path") or got.get("file") or "")
        if not made:
            cands = sorted((KIT / "workspace" / "images").glob("*.png"), key=lambda p: p.stat().st_mtime)
            made = str(cands[-1]) if cands else ""
        if made and Path(made).is_file():
            t0 = time.time()
            seen, _ = curl("/api/limb", {"name": "image_see", "arguments": {
                "path": made, "prompt": "describe this picture in one sentence"}}, timeout=900)
            try:
                sv = json.loads(seen)
            except Exception:
                sv = {"_raw": seen[:300]}
            log("image_see_local", seconds=round(time.time() - t0, 1), ok=bool(sv.get("ok")),
                text=str(sv.get("text") or sv.get("description") or "")[:200],
                error=str(sv.get("error") or "")[:150])

            import base64
            b64 = base64.b64encode(Path(made).read_bytes()).decode()
            curl("/api/cloud", {"provider": "gemini"}, timeout=60)
            curl("/api/brain", {"mode": "api"}, timeout=60)
            t0 = time.time()
            txt, secs = curl("/api/chat", {"stream": False, "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text": "What is in this image? One sentence."}]}]}, timeout=420)
            try:
                gv = json.loads(txt)
            except Exception:
                gv = {"_raw": txt[:300]}
            log("image_see_api", seconds=round(secs, 1), brain=gv.get("brain"),
                ok=bool(gv.get("text")), text=str(gv.get("text") or "")[:200],
                error=str(gv.get("error") or "")[:150])
        else:
            log("image_see_local", ok=False, error="no generated file to read")

        # ---------------------------------------------------------------- a picture through the API brain
        curl("/api/brain", {"mode": "api"}, timeout=60)
        turn("[BENCH] draw me a tiny picture of a blue crystal and save it", label="api asks for an image",
             timeout=900)
    finally:
        try:
            curl("/api/cloud", {"provider": "deepseek"}, timeout=60)
            curl("/api/brain", {"mode": "local"}, timeout=60)
            end = jget("/api/cloud")
            log("restored", provider=end.get("provider"), enabled=end.get("enabled"),
                model=end.get("model"), keys=end.get("keys_wired"))
        except Exception as exc:
            log("restore_failed", error=repr(exc)[:200])

    st = jget("/api/status") if False else jget("/api/health")
    log("done", brain=st.get("brain"), model=st.get("selected"))
    print("\nlog:", OUT / f"test-campaign-{time.strftime('%Y-%m-%d')}.jsonl")
    return 0


if __name__ == "__main__":
    # A campaign that dies mid-way must not leave the console pointing at a half-set provider: this
    # script crashed at its image step on 2026-09-21 and, with the restore at the end of main(), left
    # the kit on groq with a model that does not exist.
    atexit.register(_restore)
    raise SystemExit(main())
