"""Isolated Hermes brain for the LYGO Manager console.

This is not the desktop Hermes. The profile lives under this kit's ``data/hermes-engine``
and the process is started with that ``HERMES_HOME`` already set. The desktop profile,
its keys, and port 9119 are never read and never stopped.

The console chat window is the mouth. Hermes calls the LYGO engine already booted on
11441 (the model the picker selected). Console limbs are the ``lygo_limb`` tool.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

KIT_ROOT = Path(__file__).resolve().parents[1]
HOME_DIRNAME = "hermes-engine"
ENGINE_KEY_ENV = "LYGO_ENGINE_API_KEY"
# Hermes Agent refuses a window under 65536 tokens. The console engine often
# serves 32768; this pin lets Hermes start. The engine's own overflow error
# remains the real ceiling.
HERMES_CONTEXT_FLOOR = 65536
# Desktop Hermes has no turn clock. This oneshot used to kill a live process
# and the chat reported the kill. There is no such guard now. A pulse every
# 15s keeps the page attached. The process is stopped only when the caller
# says the client has gone.
PROMPT_CAP = 3500
ONESHOT_LEAD = (
    "You are HERMES, the standalone brain. The LYGO Manager console is your skin and harness. "
    "Answer Justin in this chat. The model is the backend he selected; local GGUF is the fallback. "
    "lygo_turn, lygo_limb, lygo_roster, and lygo_harness are loaded. "
    "Call lygo_harness to see the wrapper. Call lygo_turn for the full message. "
    "Call lygo_roster for skills and limbs. Call lygo_limb to run a console actuator. "
    "Use the terminal for git, gh, and hf. Ask before any push, upload, or deploy. "
    "Do not say those tools are inactive. Do not invent tool results. Operator message: "
)

# Child environment drops these prefixes so a desktop key cannot ride along.
_SECRET_PREFIXES = (
    "OPENAI",
    "ANTHROPIC",
    "GEMINI",
    "GROQ",
    "XAI_",
    "OPENROUTER",
    "DEEPSEEK",
    "TOGETHER",
    "HUGGING",
    "HF_",
    "NOUS_",
    "CLAUDE",
    "AZURE_OPENAI",
    "MISTRAL",
    "COHERE",
    "HERMES",
)


def desktop_homes() -> list[Path]:
    """The user's Hermes profiles. This brain must not write either of them."""
    homes: list[Path] = []
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        homes.append(Path(local) / "hermes")
    homes.append(Path.home() / ".hermes")
    return homes


def is_isolated(path: Path, homes: list[Path] | None = None) -> bool:
    """True when ``path`` is not inside a desktop Hermes profile."""
    try:
        resolved = path.resolve()
    except OSError:
        return False
    for home in homes if homes is not None else desktop_homes():
        try:
            root = home.resolve()
        except OSError:
            continue
        if resolved == root:
            return False
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        else:
            return False
    return True


def home_dir() -> Path:
    """The console-owned profile. Never the desktop Hermes home."""
    path = KIT_ROOT / "data" / HOME_DIRNAME
    if not is_isolated(path):
        raise RuntimeError("hermes home collides with the desktop Hermes profile")
    return path


def find_binary() -> str:
    """The installed ``hermes`` launcher, or '' when this machine has none.

    The launcher binary may live under the desktop install. That is the program.
    The profile is ``HERMES_HOME``, which this module always overrides.
    """
    pinned = os.environ.get("LYGO_HERMES_BIN", "").strip()
    if pinned:
        return pinned
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        cmd = Path(local) / "hermes" / "bin" / "hermes.cmd"
        if cmd.is_file():
            return str(cmd)
    found = shutil.which("hermes") or shutil.which("hermes.cmd")
    return found or ""


def wants_turn(has_image: bool, *, mock: bool = False) -> bool:
    """Whether this local text turn should be answered by the isolated Hermes.

    Image turns stay on the console's own vision path. The test suite stays on
    the console's own engine path. ``LYGO_HERMES_BRAIN=0`` turns the hook off.
    """
    if has_image or mock:
        return False
    flag = os.environ.get("LYGO_HERMES_BRAIN", "1").strip().lower()
    if flag in {"0", "off", "false", "no"}:
        return False
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    return True


def _yaml_str(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _loopback_up(base: str) -> bool:
    """True when a 127.0.0.1 URL's port is listening. Used so a dead API does not hang a turn."""
    from urllib.parse import urlparse

    parsed = urlparse(str(base or ""))
    host = parsed.hostname or ""
    if host not in {"127.0.0.1", "localhost", "::1"}:
        return True
    port = int(parsed.port or (443 if parsed.scheme == "https" else 80))
    try:
        sock = __import__("socket").socket(__import__("socket").AF_INET, __import__("socket").SOCK_STREAM)
        sock.settimeout(0.35)
        try:
            return sock.connect_ex(("127.0.0.1", port)) == 0
        finally:
            sock.close()
    except OSError:
        return False


API_SHOT_TIMEOUT_S = 25.0


def _loopback_base(port: int, suffix: str) -> str:
    try:
        number = int(port)
    except (TypeError, ValueError):
        number = 0
    if number <= 0 or number > 65535:
        number = 11441 if suffix.endswith("/v1") else 9641
    return f"http://127.0.0.1:{number}{suffix}"


def _one_line(text: str, limit: int = PROMPT_CAP) -> str:
    cleaned = str(text or "").replace("%", " percent ").replace("\r", " ").replace("\n", " ")
    cleaned = "".join(ch for ch in cleaned if ch >= " " or ch == "\t")
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > limit:
        return cleaned[:limit] + " ..."
    return cleaned


def _skills_yaml(_model: str = "") -> list[str]:
    """external_dirs for the isolated profile. Empty when this machine has none."""
    del _model
    try:
        from roster import hermes_external_dirs

        dirs = hermes_external_dirs()
    except ImportError:
        dirs = []
    if not dirs:
        return ["skills:", "  external_dirs: []"]
    lines = ["skills:", "  external_dirs:"]
    for path in dirs:
        lines.append("    - " + _yaml_str(path))
    return lines


def _config_text(model: str, engine_base: str, api_key: str = "") -> str:
    key_line = (f"  api_key: {_yaml_str(api_key)}" if api_key
                else f"  key_env: {ENGINE_KEY_ENV}")
    return "\n".join([
        "# Isolated Manager brain. Not the desktop Hermes profile.",
        "model:",
        "  provider: custom",
        f"  default: {_yaml_str(model or 'lygo-local')}",
        f"  base_url: {_yaml_str(engine_base)}",
        key_line,
        "  streaming: false",
        f"  context_length: {HERMES_CONTEXT_FLOOR}",
        "agent:",
        "  disabled_toolsets:",
        "    - browser",
        "    - image_gen",
        "    - video",
        "    - video_gen",
        "    - computer_use",
        "    - code_execution",
        "    - delegation",
        "    - cronjob",
        "    - discord",
        "    - discord_admin",
        "    - x_search",
        "    - connections",
        "approvals:",
        "  mode: manual",
        "memory:",
        "  memory_enabled: true",
        "  user_profile_enabled: true",
        # User plugins are opt-in. A missing plugins.enabled list loads none of them.
        "plugins:",
        "  enabled:",
        "    - lygo-console-limb",
        # Plugin tools are deferred whenever tool_search is on, so a small model
        # never sees lygo_limb. Off keeps the three console tools in the schema.
        "tools:",
        "  tool_search:",
        "    enabled: off",
        "  connectors:",
        "    enabled: false",
        *_skills_yaml(model),
        "",
    ])


def _plugin_source() -> str:
    return '''"""Console limb for the isolated Manager Hermes. Loopback only."""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from urllib.parse import urlencode, urlparse

_MAX = 12000


def _home() -> Path:
    return Path(os.environ.get("HERMES_HOME", "")).expanduser()


def _engine() -> dict:
    path = _home() / "engine.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _loopback(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return host in {"127.0.0.1", "localhost", "::1"}


def register(ctx):
    def lygo_turn(params, **kwargs):
        del params, kwargs
        path = _home() / "turn.txt"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            return json.dumps({"ok": False, "error": "turn_unreadable", "detail": type(exc).__name__})
        return json.dumps({"ok": True, "text": text[:_MAX]})

    def lygo_limb(params, **kwargs):
        del kwargs
        params = params if isinstance(params, dict) else {}
        name = str(params.get("name") or "").strip()
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        if not name:
            return json.dumps({"ok": False, "error": "name_required"})
        spec = _engine()
        console = str(spec.get("console") or "")
        if not _loopback(console):
            return json.dumps({"ok": False, "error": "console_not_loopback"})
        body = json.dumps({"name": name, "arguments": arguments}).encode("utf-8")
        req = urllib.request.Request(
            console.rstrip("/") + "/api/limb",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read(_MAX + 1)
        except Exception as exc:  # noqa: BLE001 - the tool must return the failure
            return json.dumps({"ok": False, "error": "limb_failed", "detail": type(exc).__name__})
        text = raw[:_MAX].decode("utf-8", "replace")
        return text

    ctx.register_tool(
        name="lygo_turn",
        toolset="lygo_console",
        schema={
            "name": "lygo_turn",
            "description": "Read the operator message in the LYGO Manager console chat. Call this first.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
        handler=lygo_turn,
    )
    ctx.register_tool(
        name="lygo_limb",
        toolset="lygo_console",
        schema={
            "name": "lygo_limb",
            "description": "Run one LYGO console harness limb (extended skills, actuators, audio, video, notes, world). name skill_list or skill_read (arguments.slug) for a skill. Other names are console limbs such as image_generate, music_generate, notepad_list, kernel_status.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Limb name, such as image_generate or world_pulse."},
                    "arguments": {"type": "object", "description": "Arguments that limb accepts."},
                },
                "required": ["name"],
            },
        },
        handler=lygo_limb,
    )

    def lygo_roster(params, **kwargs):
        del kwargs
        params = params if isinstance(params, dict) else {}
        q = str(params.get("q") or "").strip()
        spec = _engine()
        console = str(spec.get("console") or "")
        if not _loopback(console):
            return json.dumps({"ok": False, "error": "console_not_loopback"})
        url = console.rstrip("/") + "/api/roster"
        if q:
            url += "?" + urlencode({"q": q})
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read(_MAX + 1)
        except Exception as exc:  # noqa: BLE001 - the tool must return the failure
            return json.dumps({"ok": False, "error": "roster_failed", "detail": type(exc).__name__})
        return raw[:_MAX].decode("utf-8", "replace")

    ctx.register_tool(
        name="lygo_roster",
        toolset="lygo_console",
        schema={
            "name": "lygo_roster",
            "description": "List Manager skills, console limbs, and Unity harness skills. q filters by name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "q": {"type": "string", "description": "Optional filter, such as unity or skill_read."},
                },
                "required": [],
            },
        },
        handler=lygo_roster,
    )

    def lygo_harness(params, **kwargs):
        del params, kwargs
        spec = _engine()
        console = str(spec.get("console") or "")
        if not _loopback(console):
            return json.dumps({"ok": False, "error": "console_not_loopback"})
        out = {"ok": True, "brain": "hermes", "skin": "lygo-manager-console", "role": "rails"}
        for path, key in (("/api/health", "health"), ("/api/roster", "roster")):
            req = urllib.request.Request(console.rstrip("/") + path, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    raw = resp.read(_MAX + 1)
                data = json.loads(raw[:_MAX].decode("utf-8", "replace"))
            except Exception as exc:  # noqa: BLE001
                out[key] = {"error": type(exc).__name__}
                continue
            if key == "health" and isinstance(data, dict):
                cloud = data.get("cloud") if isinstance(data.get("cloud"), dict) else {}
                hz = data.get("hermes") if isinstance(data.get("hermes"), dict) else {}
                api_on = str(cloud.get("mode") or "") == "api" or str(hz.get("backend") or "") == "api"
                answering = str(hz.get("model") or cloud.get("model") or "") if api_on else str(data.get("selected") or "")
                out["health"] = {
                    "brain": data.get("brain"),
                    "selected": data.get("selected"),
                    "answering": answering,
                    "answering_backend": "api" if api_on else "local",
                    "hermes": data.get("hermes"),
                    "cloud": {
                        "mode": cloud.get("mode"),
                        "model": cloud.get("model"),
                        "provider": cloud.get("provider"),
                    },
                    "limbs": len(data.get("tools") or []),
                }
            elif key == "roster" and isinstance(data, dict):
                out["roster"] = {
                    "state": data.get("state"),
                    "skills": data.get("skills"),
                    "unity": data.get("unity"),
                    "tools": data.get("tools"),
                }
            else:
                out[key] = data
        return json.dumps(out)

    ctx.register_tool(
        name="lygo_harness",
        toolset="lygo_console",
        schema={
            "name": "lygo_harness",
            "description": "Read the LYGO Manager console wrapper around this Hermes brain: UI, selected model, API/local backend, limb and skill counts. Call when the operator asks what the console can do.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
        handler=lygo_harness,
    )
'''


def _soul() -> str:
    return (
        "You are HERMES, the standalone brain, LYGO architect, and the rails and the runner.\n"
        "The LYGO Manager Agent Console is the over-arching skin and harness around you.\n"
        "Justin Helmer is the operator. This home is isolated. It is not the desktop Hermes profile.\n"
        "Core LLM is the model the console selected: local GGUF on 11441, or the API on 18900. Local is the fallback.\n"
        "When lygo_harness health.answering_backend is api, the MAIN answering model is health.answering (cloud.model on 18900). health.selected is the idle local GGUF fallback, not the MAIN model.\n"
        "You orchestrate: memory (SOUL, USER, MEMORY), Hermes skills, Hermes limbs, and text I/O.\n"
        "The console harness extends you: extra skills, extra limbs, audio, video, UI, feedback, context.\n"
        "lygo_turn, lygo_limb, lygo_roster, and lygo_harness are loaded.\n"
        "Call lygo_harness to see the wrapper. Call lygo_roster for skills and limbs. Call lygo_limb to run a console actuator.\n"
        "The bot swarm is yours to manage: lygo_limb name swarm_status, swarm_ask, swarm_advise, swarm_dispatch, swarm_pulse, dispatcher.\n"
        "The local helper is whichever GGUF is already idle on the LYGO engine (any port, any model). If you are on the API, that idle engine is the worker. Never start a second llama-server.\n"
        "Git, GitHub, and Hugging Face run through the terminal: git, gh, and hf.\n"
        "Read lygo-lattice-manager, github-operator, and huggingface-operator before those commands.\n"
        "Ask Justin before git push, force-push, hf upload, deploy, ClawHub publish, or a social post.\n"
        "Never print a token, an API key, or gh auth token. Never invent a tool result.\n"
        "Browser, image generation, video generation, computer use, code execution, delegation, "
        "cron, Discord, and app connectors stay off. The terminal is the admin shell.\n"
    )


def _user_profile() -> str:
    return (
        "Justin Helmer. Public name Excavationpro. Lattice name Lightfather.\n"
        "GitHub and Hugging Face account: DeepSeekOracle.\n"
        "He is the steward of this Manager console. Speak as the architect: short, direct, evidence first.\n"
        "He approves every push, upload, deploy, and public post. Show the command and wait.\n"
        "Do not print secrets. Do not reset git history. Do not force-push.\n"
    )


def _memory() -> str:
    return (
        "Dual layer: HERMES is the brain. LYGO Manager Console is the skin and harness.\n"
        "Live Manager kit: <CONSOLE_DIR>\n"
        "Console http://127.0.0.1:9641/  Portal http://127.0.0.1:8790/  Engine http://127.0.0.1:11441/v1\n"
        "Free API gateway http://127.0.0.1:18900/v1 when the operator boots an Our Free Model bat.\n"
        "Launcher: Desktop\\LYGO Agent Manager.bat. A second click does not replace a live 9641.\n"
        "<EDRIVE>\\lygo-protocol-stack\\lygo_llm_console is not this console.\n"
        "GitHub CLI: gh, account DeepSeekOracle. Preflight: gh auth status. Never gh auth token.\n"
        "Hugging Face CLI: hf, account DeepSeekOracle. Preflight: hf auth whoami. Never print the token file.\n"
        "Lattice repo: https://github.com/DeepSeekOracle/lygo-protocol-stack\n"
        "HF dataset: https://huggingface.co/datasets/DeepSeekOracle/lygo-protocol-stack\n"
        "Skill hubs: https://excavationpro.ca/lygoskillhub.html and https://chatagent.ca/lygoskillhub.html\n"
        "ClawHub publisher deepseekoracle. Leave lygo-project-forge unpublished until Justin says it is public.\n"
        "Models live on <MODELS>. Boot id is QwenPaw Heretic. Do not start a second llama-server.\n"
        "Shared task bus: <DRIVE_D>\LYGO_SHARED. Star Chart writes need Justin's explicit yes.\n"
        "Support link, only when he asks: https://www.paypal.com/paypalme/ExcavationPro\n"
    )


def prepare(model: str = "lygo-local", engine_port: int = 11441, console_port: int = 9641,
            root: Path | None = None, engine_base: str = "", api_key: str = "") -> dict[str, Any]:
    """Write the isolated home. No process is started."""
    root = root or home_dir()
    if not is_isolated(root):
        raise RuntimeError("refusing to write a Hermes home inside the desktop profile")
    engine_base = str(engine_base or "").strip() or _loopback_base(engine_port, "/v1")
    console = _loopback_base(console_port, "")
    root.mkdir(parents=True, exist_ok=True)
    plugin = root / "plugins" / "lygo-console-limb"
    plugin.mkdir(parents=True, exist_ok=True)
    try:
        from roster import link_local_skills

        link_local_skills(root)
    except ImportError:
        pass
    (root / "config.yaml").write_text(_config_text(model, engine_base, api_key), encoding="utf-8")
    (root / "SOUL.md").write_text(_soul(), encoding="utf-8")
    (root / "USER.md").write_text(_user_profile(), encoding="utf-8")
    (root / "MEMORY.md").write_text(_memory(), encoding="utf-8")
    (root / ".env").write_text("", encoding="utf-8")
    (root / "engine.json").write_text(json.dumps({
        "console": console,
        "engine": engine_base,
        "model": model or "lygo-local",
        "isolated": True,
        "role": "hermes-brain",
        "skin": "lygo-manager-console",
    }, indent=2) + "\n", encoding="utf-8")
    (plugin / "plugin.yaml").write_text(
        "name: lygo-console-limb\nversion: \"1.0\"\n"
        "kind: standalone\n"
        "description: LYGO Manager console limbs for the isolated Hermes brain\n",
        encoding="utf-8",
    )
    (plugin / "__init__.py").write_text(_plugin_source(), encoding="utf-8")
    (root / "README.txt").write_text(
        "Isolated Hermes profile for the LYGO Manager console.\n"
        "Not the desktop Hermes. Do not point HERMES_HOME at %LOCALAPPDATA%\\hermes.\n",
        encoding="utf-8",
    )
    return {
        "ok": True,
        "home": str(root),
        "isolated": True,
        "engine": engine_base,
        "console": console,
        "model": model or "lygo-local",
        "binary": find_binary(),
        "line": "Manager brain is Hermes inside the LYGO engine",
    }


def sync_harness(*, model: str = "lygo-local", engine_port: int = 11441,
                 console_port: int = 9641, api_route: dict[str, Any] | None = None,
                 root: Path | None = None) -> dict[str, Any]:
    """Point the isolated Hermes rails at the console's current backend.

    API route wins when it has a base and a model. Local GGUF stays the fallback
    on the next turn if that backend dies.
    """
    route = api_route if isinstance(api_route, dict) else {}
    base = str(route.get("base") or "").strip()
    api_model = str(route.get("model") or "").strip()
    if base.startswith(("http://", "https://")) and api_model:
        return prepare(model=api_model, engine_port=engine_port, console_port=console_port,
                       root=root, engine_base=base)
    return prepare(model=model or "lygo-local", engine_port=engine_port,
                   console_port=console_port, root=root, engine_base="")


def status() -> dict[str, Any]:
    """Cheap read. Does not create the home and does not start a process."""
    try:
        root = home_dir()
    except RuntimeError as exc:
        return {"ok": False, "isolated": False, "ready": False, "reason": str(exc)}
    cfg = root / "config.yaml"
    ready = cfg.is_file() and (root / "plugins" / "lygo-console-limb" / "__init__.py").is_file()
    model = ""
    engine = ""
    if cfg.is_file():
        for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().startswith("default:"):
                model = line.split(":", 1)[1].strip().strip('"')
            if line.strip().startswith("base_url:"):
                engine = line.split(":", 1)[1].strip().strip('"')
    backend = "local"
    if "18900" in engine or (engine and "11441" not in engine and engine.startswith("http")):
        backend = "api"
    return {
        "ok": True,
        "ready": ready,
        "isolated": is_isolated(root),
        "home": root.name,
        "binary": bool(find_binary()),
        "model": model,
        "engine": engine,
        "backend": backend,
        "runner": True,
        "turn_timeout_s": None,
        "desktop": False,
    }


def child_env(home: Path, api_key: str, base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for the Hermes process. Desktop keys are not copied."""
    source = base if base is not None else dict(os.environ)
    env: dict[str, str] = {}
    for key, value in source.items():
        upper = key.upper()
        if upper.startswith(_SECRET_PREFIXES):
            continue
        env[key] = value
    env["HERMES_HOME"] = str(home)
    env[ENGINE_KEY_ENV] = api_key or "lygo-local"
    env["HERMES_ACCEPT_HOOKS"] = "1"
    return env


def _argv(binary: str, model: str, prompt: str) -> list[str]:
    return [
        binary,
        "-z", prompt,
        "-m", model or "lygo-local",
        "--provider", "custom",
    ]


def _redact(text: str, secret: str) -> str:
    if secret and len(secret) >= 8 and secret in text:
        return text.replace(secret, "[redacted]")
    return text


# A .cmd launcher goes through cmd.exe, and cmd splits "QwenPaw Heretic" into a
# second word. Hermes then says that word is not a command. Call the same
# Python the launcher names, with the argument list left intact.
_DIRECT_BOOT = (
    "import sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "del sys.argv[1]\n"
    "import hermes_bootstrap\n"
    "from hermes_cli.main import main\n"
    "sys.argv[0] = 'hermes'\n"
    "raise SystemExit(main())\n"
)


def _launcher_python(binary: str) -> tuple[str, str]:
    """``(python.exe, agent root)`` named by a hermes.cmd, or empty when unknown."""
    path = Path(binary)
    if not path.is_file():
        return "", ""
    root = path.resolve().parent.parent / "hermes-agent"
    if not (root / "hermes_cli" / "main.py").is_file():
        return "", ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", ""
    match = re.search(r'"([^"]+?python\.exe)"', text, re.IGNORECASE)
    if not match:
        return "", ""
    python = match.group(1)
    if not Path(python).is_file():
        return "", ""
    return python, str(root)


def launch_argv(argv: list[str]) -> list[str]:
    """The process argv. A Windows ``.cmd`` is replaced by its Python entry."""
    if not argv:
        return list(argv)
    if os.name == "nt" and str(argv[0]).lower().endswith((".cmd", ".bat")):
        python, agent = _launcher_python(argv[0])
        if python and agent:
            return [python, "-I", "-c", _DIRECT_BOOT, agent, *argv[1:]]
    return list(argv)


def _spawn(argv: list[str], env: dict[str, str], cwd: Path) -> subprocess.Popen[bytes]:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(
        launch_argv(argv),
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=flags,
    )


def _kill(proc: subprocess.Popen[bytes]) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        return
    proc.kill()


def _default_runner(argv: list[str], env: dict[str, str], cwd: Path,
                    on_wait: Callable[[], Any] | None, timeout: float | None) -> dict[str, Any]:
    """Wait until Hermes exits. ``timeout`` is ignored. ``on_wait`` returning
    False means the client left, and that is the only reason this kills the process."""
    del timeout
    proc = _spawn(argv, env, cwd)
    out = b""
    err = b""
    while True:
        try:
            out, err = proc.communicate(timeout=15.0)
            break
        except subprocess.TimeoutExpired:
            if on_wait and on_wait() is False:
                _kill(proc)
                return {"code": 130, "stdout": "", "stderr": "hermes stopped"}
    return {
        "code": int(proc.returncode or 0),
        "stdout": (out or b"").decode("utf-8", "replace"),
        "stderr": (err or b"").decode("utf-8", "replace"),
    }


def _history(messages: list[dict[str, Any]] | None) -> str:
    lines: list[str] = []
    for message in (messages or [])[-6:]:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        content = message.get("content")
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(str(part.get("text") or ""))
                elif isinstance(part, str):
                    parts.append(part)
            content = " ".join(parts)
        if not isinstance(content, str) or not content.strip():
            continue
        flat = _one_line(content, 400)
        if "Run `hermes --help`" in flat:
            continue
        lines.append(role + ": " + flat)
    return " | ".join(lines)[-1200:]


def _oneshot(*, user_text: str, messages: list[dict[str, Any]] | None, model: str,
             engine_port: int, console_port: int, api_key: str,
             on_wait: Callable[[], Any] | None, runner: Callable[..., dict[str, Any]] | None,
             root: Path, timeout: float | None, engine_base: str, binary: str) -> dict[str, Any]:
    info = prepare(model=model, engine_port=engine_port, console_port=console_port,
                   root=root, engine_base=engine_base, api_key=api_key)
    history = _history(messages)
    full = (user_text or "").strip()
    if history:
        full = history + "\n\n" + full
    (root / "turn.txt").write_text(full, encoding="utf-8")
    prompt = ONESHOT_LEAD + _one_line(user_text or "")
    argv = _argv(binary or "hermes", model, prompt)
    forbidden = {"--stop", "serve", "--tui"}
    if any(part in forbidden for part in argv):
        return {"handled": False, "reason": "refused_argv", "text": "", "info": info, "argv": argv}
    env = child_env(root, api_key)
    run = runner or _default_runner
    result = run(argv, env, root, on_wait, timeout)
    secret = api_key or ""
    text = _redact(str(result.get("stdout") or ""), secret).strip()
    err = _redact(str(result.get("stderr") or ""), secret).strip()
    code = int(result.get("code") or 0)
    if not text:
        lines = [ln.strip() for ln in err.splitlines() if ln.strip()]
        text = "\n".join(lines[-6:]) if lines else ""
    return {
        "handled": True,
        "text": text,
        "code": code,
        "stderr": err,
        "home": info["home"],
        "isolated": True,
        "model": model,
        "engine": info.get("engine") or "",
        "traces": [],
        "argv": argv,
    }


def _api_failed(result: dict[str, Any]) -> bool:
    blob = (str(result.get("stderr") or "") + " " + str(result.get("text") or "")).lower()
    if int(result.get("code") or 0) not in (0,):
        return True
    if not str(result.get("text") or "").strip():
        return True
    return any(mark in blob for mark in ("rate_limit", "429", "502", "free usage limit", "timed out"))


def answer_turn(*, user_text: str, messages: list[dict[str, Any]] | None = None,
                model: str = "lygo-local", engine_port: int = 11441, console_port: int = 9641,
                api_key: str = "", on_wait: Callable[[], Any] | None = None,
                runner: Callable[..., dict[str, Any]] | None = None,
                root: Path | None = None, timeout: float | None = None,
                api_route: dict[str, Any] | None = None) -> dict[str, Any]:
    """One console turn. API first when a configured route is given; local GGUF if that dies."""
    binary = find_binary()
    if runner is None and not binary:
        return {"handled": False, "reason": "binary_missing", "text": ""}
    root = root or home_dir()
    route = api_route if isinstance(api_route, dict) else {}
    api_base = str(route.get("base") or "").strip()
    api_model = str(route.get("model") or "").strip()
    api_secret = str(route.get("key") or "")
    if api_base.startswith(("http://", "https://")) and api_model:
        first = _oneshot(
            user_text=user_text, messages=messages, model=api_model,
            engine_port=engine_port, console_port=console_port, api_key=api_secret or api_key,
            on_wait=on_wait, runner=runner, root=root,
            timeout=timeout if timeout is not None else API_SHOT_TIMEOUT_S,
            engine_base=api_base, binary=binary or "hermes",
        )
        if first.get("handled") and not _api_failed(first):
            first["via"] = "api"
            if not first.get("text"):
                first["text"] = "Hermes returned no text for this turn."
            return first
    local = _oneshot(
        user_text=user_text, messages=messages, model=model,
        engine_port=engine_port, console_port=console_port, api_key=api_key,
        on_wait=on_wait, runner=runner, root=root, timeout=timeout,
        engine_base="", binary=binary or "hermes",
    )
    local["via"] = "local"
    if not local.get("text"):
        local["text"] = "Hermes returned no text for this turn."
    return local
