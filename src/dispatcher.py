"""Dispatcher: worker registry, scan, chat, boot/restart for Hermes.

Never starts a second llama-server if 11441 is already open. Network scan is
opt-in (POST scan_net), localhost probe is cheap.
"""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LINKS = ROOT / "data" / "swarm" / "links.json"
KNOWN_PORTS = (9641, 11441, 18900, 8790, 8796, 9119, 8080, 8081, 11434, 1234, 8000, 11442)
DESKTOP_ENGINE_PORT = 8080
DESKTOP_DASH_PORT = 9119
# Desktop Hermes llama-server on 8080 uses this Bearer. Do not log it.
DESKTOP_ENGINE_KEY = "local"

def _tcp(host: str, port: int, timeout: float = 0.35) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        return sock.connect_ex((host, port)) == 0
    except OSError:
        return False
    finally:
        sock.close()


def _get(url: str, timeout: float = 0.7, headers: dict[str, str] | None = None) -> tuple[int, str]:
    t0 = time.time()
    try:
        req = urllib.request.Request(url, method="GET", headers=headers or {})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(4000).decode("utf-8", "replace")
            return resp.status, body
    except urllib.error.HTTPError as exc:
        return exc.code, (exc.read(400) or b"").decode("utf-8", "replace")
    except Exception:
        return 0, ""
    finally:
        elapsed = time.time() - t0
        _get.last_ms = int(elapsed * 1000)  # type: ignore[attr-defined]


_get.last_ms = 0  # type: ignore[attr-defined]


def _links() -> list[dict[str, Any]]:
    if not LINKS.is_file():
        return []
    try:
        rows = json.loads(LINKS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [r for r in rows if isinstance(r, dict)]


def _save_links(rows: list[dict[str, Any]]) -> None:
    LINKS.parent.mkdir(parents=True, exist_ok=True)
    LINKS.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")


def _classify(wid: str, port: int, host: str = "127.0.0.1") -> dict[str, Any]:
    up = _tcp(host, port)
    status = "offline"
    hint = "need boot"
    latency = None
    detail = ""
    if not up:
        return {"id": wid, "host": host, "port": port, "status": "offline", "hint": hint,
                "available": False, "latency_ms": None, "detail": "port closed"}
    latency = _get.last_ms
    if port == 11441:
        code, body = _get("http://%s:%s/v1/models" % (host, port), 0.8)
        latency = _get.last_ms
        if code in (200, 401):
            status, hint, detail = "connected", "live local GGUF", "OpenAI /v1" + (" (key)" if code == 401 else "/models")
            try:
                ids = [str(r.get("id")) for r in (json.loads(body).get("data") or []) if isinstance(r, dict) and r.get("id")]
                if ids:
                    detail = ids[0]
            except Exception:
                pass
        else:
            status, hint, detail = "need_restart", "port open, models failed", "HTTP " + str(code)
    elif port == 8082:
        code, body = _get("http://%s:%s/v1/models" % (host, port), 0.8)
        latency = _get.last_ms
        if code == 200:
            status, hint, detail = "connected", "NVIDIA API advisor", "NVIDIA bridge"
        else:
            status, hint, detail = "need_restart", "bridge port open, models failed", "HTTP " + str(code)
    elif port == 18900:
        code, _ = _get("http://%s:%s/health" % (host, port), 1.2)
        latency = _get.last_ms
        if code == 200:
            status, hint, detail = "connected", "API advisor live", "OFM health"
        else:
            status, hint, detail = "need_restart", "gateway port open but unhealthy", "HTTP " + str(code)
    elif port == 9641:
        # Never HTTP-GET this console from inside a request on 9641 — that deadlocks the handler.
        if host in {"127.0.0.1", "localhost", "::1"}:
            status, hint, detail = "connected", "Hermes rails (this console)", "in-process"
        else:
            code, body = _get("http://%s:%s/api/health" % (host, port), 0.8)
            latency = _get.last_ms
            if code == 200:
                status, hint, detail = "connected", "remote console", "health"
            else:
                status, hint, detail = "need_restart", "console port open, health failed", "HTTP " + str(code)
    elif port == 8790:
        code, _ = _get("http://%s:%s/health" % (host, port), 1.0)
        latency = _get.last_ms
        status = "connected" if code == 200 else "standby"
        hint = "Manager portal"
        detail = "portal"
    elif port == 8796:
        status, hint, detail = "connected" if up else "offline", "Forge UI", "do not kill this port"
    elif port == DESKTOP_DASH_PORT:
        status, hint, detail = "connected" if up else "offline", "desktop Hermes dashboard", "leave running"
    elif port == DESKTOP_ENGINE_PORT:
        return _desktop_hermes_live(host)
    else:
        code, body = _get("http://%s:%s/v1/models" % (host, port), 0.9)
        latency = _get.last_ms
        if code in (200, 401):
            status, hint, detail = "available", "OpenAI-compatible LLM", "discovered" + (" (key)" if code == 401 else "")
        else:
            status, hint, detail = "standby", "port open", "unknown service"
    return {
        "id": wid, "host": host, "port": port, "status": status, "hint": hint,
        "available": status in {"connected", "standby", "available"},
        "latency_ms": latency, "detail": detail,
    }


def _desktop_model(host: str = "127.0.0.1") -> str:
    code, body = _get(
        "http://%s:%s/v1/models" % (host, DESKTOP_ENGINE_PORT),
        0.8,
        headers={"Authorization": "Bearer " + DESKTOP_ENGINE_KEY},
    )
    if code not in (200, 401):
        return ""
    try:
        payload = json.loads(body or "{}")
    except json.JSONDecodeError:
        return ""
    for row in payload.get("data") or []:
        if isinstance(row, dict) and row.get("id"):
            return str(row["id"])
    for row in payload.get("models") or []:
        if isinstance(row, dict) and (row.get("id") or row.get("name") or row.get("model")):
            return str(row.get("id") or row.get("name") or row.get("model"))
    return ""


def _desktop_hermes_live(host: str = "127.0.0.1") -> dict[str, Any]:
    """Desktop Hermes is the 8080 llama-server, the 9119 dashboard, or both.

    The Electron dashboard on 9119 is often closed while the local engine on 8080
    stays up. Dispatcher used to watch 9119 only, so a live desktop session
    painted offline.
    """
    eng = _tcp(host, DESKTOP_ENGINE_PORT)
    dash = _tcp(host, DESKTOP_DASH_PORT)
    if eng:
        code, _ = _get("http://%s:%s/health" % (host, DESKTOP_ENGINE_PORT), 0.8)
        model = _desktop_model(host) if code == 200 else ""
        if code == 200:
            hint = "desktop Hermes local engine"
            if dash:
                hint = "desktop Hermes engine + dashboard"
            return {
                "id": "desktop-hermes", "host": host, "port": DESKTOP_ENGINE_PORT,
                "status": "connected", "hint": hint, "available": True,
                "latency_ms": getattr(_get, "last_ms", None), "detail": model or "8080 /health",
            }
        return {
            "id": "desktop-hermes", "host": host, "port": DESKTOP_ENGINE_PORT,
            "status": "need_restart", "hint": "8080 open, health failed", "available": False,
            "latency_ms": getattr(_get, "last_ms", None), "detail": "HTTP " + str(code),
        }
    if dash:
        return {
            "id": "desktop-hermes", "host": host, "port": DESKTOP_DASH_PORT,
            "status": "connected", "hint": "desktop Hermes dashboard", "available": True,
            "latency_ms": None, "detail": "9119",
        }
    return {
        "id": "desktop-hermes", "host": host, "port": DESKTOP_ENGINE_PORT,
        "status": "offline", "hint": "desktop Hermes not on 8080 or 9119",
        "available": False, "latency_ms": None, "detail": "ports closed",
    }


def registry() -> list[dict[str, Any]]:
    eport = 11441
    emodel = ""
    try:
        import swarm as _swarm

        eng = _swarm.live_engine()
        if int(eng.get("port") or 0):
            eport = int(eng["port"])
        emodel = str(eng.get("model") or "")
    except Exception:
        pass
    local_title = "Local GGUF helper" + ((" · " + emodel) if emodel else "")
    rows = [
        {"id": "hermes", "title": "Hermes brain", "kind": "rails", "host": "127.0.0.1", "port": 9641,
         "capabilities": "chat, limbs, skills, swarm"},
        {"id": "local", "title": local_title, "kind": "gguf", "host": "127.0.0.1", "port": eport,
         "capabilities": "idle helper when Hermes is on API — any booted GGUF, any engine port"},
        {"id": "advisor", "title": "API advisor", "kind": "api", "host": "127.0.0.1", "port": 8082,
         "capabilities": "NVIDIA Lightning second opinion"},
        {"id": "portal", "title": "Manager portal", "kind": "tool", "host": "127.0.0.1", "port": 8790,
         "capabilities": "stream agent chat"},
        {"id": "a9lyra", "title": "A9LYRA (stream)", "kind": "tool", "host": "127.0.0.1", "port": 8790,
         "capabilities": "stream persona, local GGUF fallback"},
        {"id": "lightfather", "title": "Lightfather (stream)", "kind": "tool", "host": "127.0.0.1", "port": 8790,
         "capabilities": "stream persona, local GGUF fallback"},
        {"id": "forge", "title": "Project Forge", "kind": "tool", "host": "127.0.0.1", "port": 8796,
         "capabilities": "asset jobs — do not kill"},
        {"id": "desktop-hermes", "title": "Desktop Hermes", "kind": "tool", "host": "127.0.0.1", "port": DESKTOP_ENGINE_PORT,
         "capabilities": "separate desktop engine on 8080 (dashboard 9119 if open) — do not stop"},
    ]
    seen = {(r["host"], r["port"]) for r in rows}
    seen.add(("127.0.0.1", DESKTOP_DASH_PORT))
    for link in _links():
        key = (str(link.get("host") or "127.0.0.1"), int(link.get("port") or 0))
        if key in seen or not key[1]:
            continue
        seen.add(key)
        rows.append({
            "id": str(link.get("id") or ("link-%s-%s" % key)),
            "title": str(link.get("title") or "linked LLM"),
            "kind": str(link.get("kind") or "api"),
            "host": key[0], "port": key[1],
            "capabilities": str(link.get("capabilities") or "linked"),
        })
    out = []
    for row in rows:
        if str(row["id"]) == "desktop-hermes":
            live = _desktop_hermes_live(str(row["host"]))
        else:
            live = _classify(str(row["id"]), int(row["port"]), str(row["host"]))
        live.update({k: row[k] for k in ("title", "kind", "capabilities")})
        out.append(live)
    return out


def scan_local() -> list[dict[str, Any]]:
    found = []
    known = {(w["host"], w["port"]) for w in registry()}
    for port in KNOWN_PORTS:
        if ("127.0.0.1", port) in known:
            continue
        if not _tcp("127.0.0.1", port):
            continue
        found.append(_classify("scan-%s" % port, port, "127.0.0.1"))
        found[-1]["title"] = "local:%s" % port
        found[-1]["kind"] = "discovered"
        found[-1]["capabilities"] = "auto-search"
    return found


def _local_ipv4() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def scan_net() -> list[dict[str, Any]]:
    """Probe a small private /24 slice. Opt-in only. Never scans the public internet."""
    ip = _local_ipv4()
    parts = ip.split(".")
    if len(parts) != 4 or parts[0] not in {"10", "192", "172"}:
        return []
    prefix = ".".join(parts[:3])
    me = int(parts[3])
    found = []
    ports = (11441, 18900, 11434, 8080)
    for last in range(max(1, me - 8), min(254, me + 9)):
        host = "%s.%s" % (prefix, last)
        if host == ip:
            continue
        for port in ports:
            if not _tcp(host, port, 0.12):
                continue
            row = _classify("net-%s-%s" % (host.replace(".", "-"), port), port, host)
            row["title"] = "%s:%s" % (host, port)
            row["kind"] = "network"
            row["capabilities"] = "LAN OpenAI-shaped"
            found.append(row)
    return found


def pane() -> dict[str, Any]:
    workers = registry()
    live = sum(1 for w in workers if w["status"] == "connected")
    lights = [
        {"state": "ok" if live else "amber", "label": "WORKERS", "text": "%s connected" % live},
        {"state": "ok" if any(w["id"] == "local" and w["status"] == "connected" for w in workers) else "off",
         "label": "QWENPAW", "text": "11441"},
        {"state": "ok" if any(w["id"] == "desktop-hermes" and w["status"] == "connected" for w in workers) else "off",
         "label": "DESKTOP", "text": "8080"},
        {"state": "ok" if any(w["id"] == "advisor" and w["status"] == "connected" for w in workers) else "off",
         "label": "API", "text": "8082"},
    ]
    groups = [{"title": "Dispatcher workers", "rows": [
        {"k": w["title"], "v": w["status"], "note": w["hint"], "dot":
         "ok" if w["status"] == "connected" else ("amber" if w["status"] in {"standby", "available", "need_restart"} else "off")}
        for w in workers
    ]}]
    try:
        import tasking

        tasks = tasking.fold()[:8]
        if tasks:
            groups.append({"title": "Task queue", "rows": [
                {"k": str(t.get("id") or ""), "v": str(t.get("state") or ""),
                 "note": str(t.get("target") or t.get("note") or ""), "dot": "ok" if t.get("state") == "done" else "amber"}
                for t in tasks
            ]})
    except Exception:
        pass
    return {
        "ok": True,
        "state": "green" if live else "amber",
        "state_text": "%s workers connected" % live,
        "lights": lights,
        "groups": groups,
        "workers": workers,
        "missing": [],
    }


def boot(worker_id: str) -> dict[str, Any]:
    wid = str(worker_id or "").strip()
    if wid in {"qwenpaw", "helper", "engine", "local"}:
        try:
            import swarm as _swarm
            if _swarm.engine_alive():
                return {"ok": True, "status": "connected", "note": "local GGUF already listening — not starting a second llama-server"}
        except Exception:
            if _tcp("127.0.0.1", 11441):
                return {"ok": True, "status": "connected", "note": "local GGUF already listening — not starting a second llama-server"}
        try:
            import server as srv
            mid = str(srv.STATE.get("selected") or "QwenPaw Heretic")
            srv.boot_async(mid)
            return {"ok": True, "status": "booting", "note": "asked the console to boot " + mid}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": type(exc).__name__, "hint": "press Boot LLM in the console"}
    if wid in {"advisor", "ofm"}:
        if _tcp("127.0.0.1", 8082):
            return {"ok": True, "status": "connected", "note": "NVIDIA bridge already live"}
        try:
            import subprocess
            subprocess.Popen(
                ["C:\\Python313\\pythonw.exe", r"<LYGO_DIR>\lygo-local-bridge\lygo_nvidia_bridge.py"],
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
            for _ in range(6):
                time.sleep(0.5)
                if _tcp("127.0.0.1", 8082):
                    return {"ok": True, "status": "connected", "note": "NVIDIA bridge started"}
            return {"ok": True, "status": "booting", "note": "NVIDIA bridge starting"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": type(exc).__name__}
    if wid == "forge":
        if _tcp("127.0.0.1", 8796):
            return {"ok": True, "status": "connected", "note": "Forge already live on 8796"}
        forge_bat = Path(r"<FORGE_DIR>\LYGO Forge.bat")
        if not forge_bat.is_file():
            return {"ok": False, "error": "forge_launcher_missing", "hint": str(forge_bat)}
        try:
            import subprocess
            subprocess.Popen(
                ["cmd.exe", "/c", "start", "", str(forge_bat)],
                cwd=str(forge_bat.parent),
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
            time.sleep(2)
            return {"ok": True, "status": "connected" if _tcp("127.0.0.1", 8796) else "booting",
                    "note": "Forge window launched"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": type(exc).__name__}
    if wid in {"desktop-hermes"}:
        return {"ok": False, "error": "protected", "hint": "desktop Hermes is not auto-booted from Dispatcher"}
    return {"ok": False, "error": "unknown_worker", "id": wid}


def restart(worker_id: str) -> dict[str, Any]:
    wid = str(worker_id or "").strip()
    if wid in {"qwenpaw", "helper", "engine", "hermes", "forge", "desktop-hermes"}:
        return {"ok": False, "error": "protected", "hint": "will not kill the engine, Forge, or desktop Hermes"}
    if wid in {"advisor", "ofm"}:
        return boot("advisor")
    return {"ok": False, "error": "unknown_worker"}


_STREAM_PERSONA = {
    "a9lyra": "You are A9LYRA, a linked stream-PC agent answering through the LYGO Manager. Short, checkable, no secrets.",
    "lightfather": "You are Lightfather, a linked stream-PC agent answering through the LYGO Manager. Short, checkable, no secrets.",
}


def _stream_chat(persona: str, prompt: str) -> dict[str, Any] | None:
    body = json.dumps({"persona": persona, "message": prompt, "history": []}).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:8790/api/agents/chat",
        data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return None
    if payload.get("ok") and payload.get("reply"):
        return {"ok": True, "reply": str(payload["reply"]), "backend": "stream-pc", "agent": persona}
    return None


def chat(worker_id: str, prompt: str) -> dict[str, Any]:
    import swarm
    wid = str(worker_id or "helper").strip().lower()
    prompt = str(prompt or "").strip()
    if not prompt:
        return {"ok": False, "error": "empty_prompt"}
    if wid in {"a9lyra", "lightfather", "stream", "portal"}:
        persona = "lightfather" if "light" in wid else "a9lyra"
        got = _stream_chat(persona, prompt)
        if got:
            return got
        eng = swarm.live_engine()
        if not eng.get("alive"):
            return {"ok": False, "error": "engine_not_booted", "hint": "stream PC did not answer in 5s and the local GGUF is idle/off"}
        reply = swarm._complete(str(eng["url"]), str(eng.get("model") or "lygo-local"),
                                _STREAM_PERSONA[persona], prompt)
        reply["agent"] = persona
        reply["backend"] = "local_fallback"
        return reply
    if wid in {"qwenpaw", "helper", "local", "engine"}:
        return swarm.ask("helper", prompt)
    if wid in {"advisor", "ofm"}:
        return swarm.advise(prompt)
    if wid in {"forge"}:
        return swarm.dispatch("forge", prompt, note="dispatcher")
    if wid in {"hermes"}:
        return {"ok": False, "error": "use_main_chat", "hint": "Use the main console chat for Hermes"}
    if wid in {"desktop-hermes", "desktop"}:
        if not _tcp("127.0.0.1", DESKTOP_ENGINE_PORT):
            return {"ok": False, "error": "desktop_hermes_offline", "hint": "desktop engine 8080 is closed"}
        model = _desktop_model() or "lygo-local"
        reply = swarm._complete(
            "http://127.0.0.1:%s/v1/chat/completions" % DESKTOP_ENGINE_PORT,
            model,
            "You are the desktop Hermes local engine linked from the LYGO Manager Dispatcher. Short, checkable answers. Do not print secrets.",
            prompt,
            key=DESKTOP_ENGINE_KEY,
        )
        reply["agent"] = "desktop-hermes"
        reply["backend"] = "desktop-8080"
        reply["model"] = model
        return reply
    return swarm.ask("helper", prompt)


def link(host: str, port: int, title: str = "") -> dict[str, Any]:
    host = str(host or "").strip() or "127.0.0.1"
    port = int(port or 0)
    if port <= 0:
        return {"ok": False, "error": "need_port"}
    rows = _links()
    wid = "link-%s-%s" % (host.replace(".", "-"), port)
    rows = [r for r in rows if not (r.get("host") == host and int(r.get("port") or 0) == port)]
    rows.append({"id": wid, "host": host, "port": port, "title": title or ("%s:%s" % (host, port)), "kind": "api"})
    _save_links(rows)
    return {"ok": True, "id": wid, "status": _classify(wid, port, host)["status"]}


def handle(action: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = args if isinstance(args, dict) else {}
    action = str(action or "status").strip().lower()
    if action in ("status", "list", "pane"):
        return pane()
    if action in ("scan", "scan_local"):
        return {"ok": True, "workers": registry() + scan_local()}
    if action in ("scan_net", "network"):
        return {"ok": True, "workers": scan_net()}
    if action == "boot":
        return boot(str(args.get("worker") or args.get("id") or ""))
    if action == "restart":
        return restart(str(args.get("worker") or args.get("id") or ""))
    if action in ("chat", "talk", "ask"):
        return chat(str(args.get("worker") or args.get("agent") or "helper"),
                    str(args.get("prompt") or args.get("q") or ""))
    if action == "link":
        return link(str(args.get("host") or "127.0.0.1"), int(args.get("port") or 0), str(args.get("title") or ""))
    if action == "task":
        import swarm
        return swarm.dispatch(str(args.get("worker") or "helper"), str(args.get("prompt") or ""), "dispatcher")
    return {"ok": False, "error": "unknown_action", "got": action}
