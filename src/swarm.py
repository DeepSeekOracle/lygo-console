"""Bot swarm: specialist helpers Hermes can dispatch.

QwenPaw on 11441 is the local worker ONLY if that engine is already listening.
This module never starts llama-server. The API advisor uses the NVIDIA bridge on 8082 when alive.
Jobs go through the existing task queue and cron keeper.
"""
from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "data" / "swarm" / "agents"
LOG = ROOT / "save" / "swarm" / "runs.jsonl"
ADVISOR_URL = "http://127.0.0.1:8082/v1/chat/completions"
ADVISOR_MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"


def _engine_ports() -> list[int]:
    ports: list[int] = []
    for raw in (os.environ.get("LYGO_LLAMA_PORT"),):
        try:
            ports.append(int(raw or 0))
        except (TypeError, ValueError):
            pass
    try:
        from paths import LLAMA_PORT

        ports.append(int(LLAMA_PORT))
    except Exception:
        ports.append(11441)
    try:
        import server as srv

        ports.append(int(srv.brain_port()))
    except Exception:
        pass
    out: list[int] = []
    for p in ports:
        if p > 0 and p not in out:
            out.append(p)
    return out or [11441]


def live_engine() -> dict[str, Any]:
    """Whatever GGUF is already listening for the console. Never starts llama-server."""
    for port in _engine_ports():
        if not _port_up(port):
            continue
        url = "http://127.0.0.1:%s/v1/models" % port
        headers = {}
        try:
            headers["Authorization"] = "Bearer " + _engine_key()
        except Exception:
            pass
        req = urllib.request.Request(url, method="GET", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=0.8) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
        except Exception:
            selected = ""
            try:
                import server as srv
                selected = str(srv.STATE.get("selected") or "")
            except Exception:
                selected = ""
            return {
                "alive": True,
                "port": port,
                "model": selected,
                "url": "http://127.0.0.1:%s/v1/chat/completions" % port,
                "note": "port open, model list unread",
            }
        ids = []
        for row in payload.get("data") or []:
            if isinstance(row, dict) and row.get("id"):
                ids.append(str(row["id"]))
        model = ids[0] if ids else ""
        return {
            "alive": True,
            "port": port,
            "model": model,
            "models": ids,
            "url": "http://127.0.0.1:%s/v1/chat/completions" % port,
        }
    return {"alive": False, "port": 0, "model": "", "url": "", "models": []}

DEFAULT_AGENTS: dict[str, dict[str, Any]] = {
    "helper": {
        "id": "helper",
        "title": "Local GGUF helper",
        "backend": "local",
        "role": "specialist on whatever GGUF is already idle on the LYGO engine",
        "system": "You are the LYGO Manager helper on the local engine. Use the booted GGUF, whatever its name. Short, checkable answers. You advise Hermes. You do not push, deploy, or print secrets.",
    },
    "advisor": {
        "id": "advisor",
        "title": "API advisor",
        "backend": "api",
        "role": "second opinion from the free API gateway",
        "system": "You are the API advisor for Hermes on the LYGO Manager. Critique and tighten. Do not claim to be Hermes. Do not print secrets.",
    },
    "forge": {
        "id": "forge",
        "title": "Forge dispatcher",
        "backend": "local",
        "role": "queues Forge-shaped jobs onto the console task list",
        "system": "You plan one Forge job as JSON: title, target, steps. Hermes approves. Do not start Unity or spend GPU.",
    },
    "stream": {
        "id": "stream",
        "title": "Stream liaison",
        "backend": "local",
        "role": "drafts stream-PC bot notes like the Manager stream chat modules",
        "system": "You draft a short note for A9LYRA or Lightfather on the stream PC. No tokens. No live Discord.",
    },
}


def _port_up(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.4)
    try:
        return sock.connect_ex(("127.0.0.1", port)) == 0
    except OSError:
        return False
    finally:
        sock.close()


def engine_alive() -> bool:
    return bool(live_engine().get("alive"))


def advisor_alive() -> bool:
    return _port_up(8082)


def ensure_agents() -> list[dict[str, Any]]:
    AGENTS.mkdir(parents=True, exist_ok=True)
    for spec in DEFAULT_AGENTS.values():
        path = AGENTS / (spec["id"] + ".json")
        if not path.is_file():
            path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    rows = []
    for path in sorted(AGENTS.glob("*.json")):
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(obj, dict) and obj.get("id"):
            rows.append(obj)
    return rows


def _agent(aid: str) -> dict[str, Any] | None:
    aid = str(aid or "").strip()
    for row in ensure_agents():
        if str(row.get("id")) == aid:
            return row
    return DEFAULT_AGENTS.get(aid)


def _append(row: dict[str, Any]) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _engine_key() -> str:
    try:
        from auth import ensure_llama_key

        return ensure_llama_key() or "lygo-local"
    except Exception:
        return "lygo-local"


def _complete(url: str, model: str, system: str, prompt: str, key: str = "") -> dict[str, Any]:
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system[:2000]},
            {"role": "user", "content": str(prompt)[:6000]},
        ],
        "temperature": 0.4,
        "max_tokens": 400,
        "stream": False,
    }).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if not key and "127.0.0.1:11441" in url:
        key = _engine_key()
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": "http_" + str(exc.code)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__}
    choices = payload.get("choices") or []
    text = (((choices[0] or {}).get("message") or {}).get("content") if choices else "") or ""
    if not str(text).strip():
        return {"ok": False, "error": "empty_reply"}
    return {"ok": True, "reply": str(text).strip()}


def _ofm_key() -> str:
    path = Path(r"<STACK_DIR>\ofm-home\settings.json")
    if not path.is_file():
        return "lygo-local"
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("forwardKey") or "lygo-local")
    except (OSError, json.JSONDecodeError):
        return "lygo-local"


def status() -> dict[str, Any]:
    agents = ensure_agents()
    eng = live_engine()
    return {
        "ok": True,
        "engine_alive": bool(eng.get("alive")),
        "advisor_alive": advisor_alive(),
        "engine": eng.get("url") or "",
        "engine_port": eng.get("port") or 0,
        "engine_model": eng.get("model") or "",
        "advisor": "http://127.0.0.1:8082/v1",
        "agents": [{"id": a.get("id"), "title": a.get("title"), "backend": a.get("backend"), "role": a.get("role")} for a in agents],
        "note": "Local helper uses whichever GGUF is already idle on the LYGO engine (any port, any model). If Hermes is on the API, that idle engine is the worker. This swarm never starts llama-server.",
    }


def ask(agent_id: str, prompt: str) -> dict[str, Any]:
    agent = _agent(agent_id) or DEFAULT_AGENTS["helper"]
    backend = str(agent.get("backend") or "local")
    if backend == "api":
        return advise(prompt, agent=agent)
    eng = live_engine()
    if not eng.get("alive"):
        return {
            "ok": False,
            "error": "engine_not_booted",
            "hint": "No local GGUF is listening. Boot the Manager engine first. Swarm will not start a second llama-server.",
            "agent": agent.get("id"),
        }
    model = str(eng.get("model") or "lygo-local")
    got = _complete(str(eng["url"]), model, str(agent.get("system") or ""), prompt)
    got["agent"] = agent.get("id")
    got["backend"] = "local"
    _append({"t": time.time(), "agent": agent.get("id"), "backend": "local", "ok": got.get("ok")})
    return got


def advise(prompt: str, agent: dict[str, Any] | None = None) -> dict[str, Any]:
    agent = agent or DEFAULT_AGENTS["advisor"]
    if advisor_alive():
        got = _complete(ADVISOR_URL, ADVISOR_MODEL, str(agent.get("system") or ""), prompt, key="")
        if got.get("ok"):
            got["agent"] = agent.get("id")
            got["backend"] = "api"
            _append({"t": time.time(), "agent": agent.get("id"), "backend": "api", "ok": True})
            return got
    eng = live_engine()
    if eng.get("alive"):
        got = _complete(str(eng["url"]), str(eng.get("model") or "lygo-local"), str(agent.get("system") or ""), prompt)
        got["agent"] = agent.get("id")
        got["backend"] = "local_fallback"
        _append({"t": time.time(), "agent": agent.get("id"), "backend": "local_fallback", "ok": got.get("ok")})
        return got
    return {"ok": False, "error": "no_backend", "hint": "NVIDIA bridge 8082 and local 11441 are both down"}


def dispatch(agent_id: str, prompt: str, note: str = "") -> dict[str, Any]:
    import tasking

    return tasking.add("swarm_ask", {"agent": agent_id or "helper", "prompt": prompt},
                       note=note or "swarm", origin="hermes")


def pulse() -> dict[str, Any]:
    st = status()
    LOG.parent.mkdir(parents=True, exist_ok=True)
    (LOG.parent / "last.json").write_text(json.dumps(st, indent=2), encoding="utf-8")
    return {"ok": True, "engine_alive": st["engine_alive"], "advisor_alive": st["advisor_alive"],
            "agents": len(st["agents"])}


def handle(action: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = args if isinstance(args, dict) else {}
    action = str(action or "status").strip().lower()
    if action in ("status", "list"):
        return status()
    if action in ("ask", "helper"):
        return ask(str(args.get("agent") or "helper"), str(args.get("prompt") or args.get("q") or ""))
    if action in ("advise", "advisor"):
        return advise(str(args.get("prompt") or args.get("q") or ""))
    if action in ("dispatch", "queue"):
        return dispatch(str(args.get("agent") or "helper"), str(args.get("prompt") or ""), str(args.get("note") or ""))
    if action == "pulse":
        return pulse()
    return {"ok": False, "error": "unknown_action", "got": action}
