"""The free brain: run the console's OWN turns on OpenCode's free model pool.

Δ9Φ963-LYGO-FREEBRAIN-v1

WHY THIS EXISTS
    One place chooses the console's brain: `src/cloud_api.py` - provider + url + model + key +
    enabled. When the chain is non-empty the console's turns go over HTTP to that url instead of
    to the local llama-server. The free pool cannot be reached that way directly: the same
    inference endpoint called from our own code answers 403 `FreeTierError` - "OpenCode's free
    tier can only be used from within OpenCode". This file closes that gap with a LOCAL ADAPTER:
    an OpenAI-shaped endpoint running INSIDE the console process that answers each request by
    driving the OpenCode CLI, which is the one client the free tier accepts.

WHAT IT IS
    * `serve()`  - an in-process HTTP shim on loopback: POST /v1/chat/completions, GET /v1/models,
      GET /health. It answers in the shape the console already parses
      (`choices[0].message.content`), so no console code learns anything new.
    * `on()` / `off()` - the switch. `on` points cloud_api at the shim; `off` puts back exactly
      what was there before, from the snapshot it took. The console core is untouched: this rides
      its existing `custom` provider row ("OpenAI-compatible URL").
    * A PLACEHOLDER key is saved with it. `cloud_api.chain_for` only offers a provider that holds a
      key, so a keyless row is dropped in silence and the switch would look broken with nothing
      anywhere saying why. The shim ignores the value; it is not a credential and never becomes one.

WHAT IT COSTS, SAID OUT LOUD
    Every turn SPAWNS THE CLI: an answer takes seconds rather than milliseconds, and a turn the
    local engine would have streamed arrives whole. The free tier is metered (community reports
    ~200 requests/day, per-model lockouts) and its limit is MASKED as an opaque server error -
    which this file passes through with its ref instead of translating into a diagnosis. Its
    per-model data terms also differ from the paid providers': this is a switch for work you would
    not mind a promotional endpoint seeing. Private operator context stays on the paid provider or
    the local engine, and the card says so next to the button.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

SIGNATURE = "Δ9Φ963-LYGO-FREEBRAIN-v1"
DEFAULT_PORT = 9655
#: Not a credential and never treated as one. `chain_for` needs a non-empty key or the row is
#: dropped; the shim accepts anything in the Authorization header and forwards nothing.
PLACEHOLDER_KEY = "free-brain-shim"
#: MEASURED 2026-10-16 from the shim's own ring: real console turns through this route took 20.3 s,
#: 22.3 s, 24.5 s - and 239.8 s, while a fifth was cut off at the ceiling (502 after 333.7 s, "the side
#: agent did not finish inside 240s"). A ceiling that a normal turn sits against is not a guard, it is a
#: coin flip on the operator's next question. Raised to cover the measured worst case with room: the page's
#: own window (portal/app.js) is the thing that must stay longer than THIS.
CLI_TIMEOUT_S = 600
#: MEASURED 2026-10-16 on this box. `opencode run` takes the prompt as an argv positional, and the
#: `opencode` on PATH is the npm .CMD wrapper, which re-parses its line through cmd.exe - so the
#: ceiling is cmd's ~8191, not Windows' 32767. A 2,164-character prompt answered (66.5 s); an
#: 18,168-character one came back exit 0 with NO answer in 0.06 s (a real console turn is 18k+).
ARGV_PROMPT_CHARS = 7000
#: Past the argv ceiling the prompt is handed over as a FILE and the argv carries one short sentence
#: naming it - measured working at 17,783 characters: exit 0 in 12.0 s, the CLI's own `read` tool, the
#: answer exactly as asked. Past this we refuse BY NAME instead of burning a request on a lost cause.
# 0 = no ceiling, kept as an idiom so an operator can disarm this with one number.
# KEPT, and NOT because doctrine: this is the point past which the CLI was MEASURED to answer with
# nothing - exit 0, no text, 0.06 s (an 18,168-character prompt did exactly that). The steward removed
# the DOCTRINE caps on 2026-10-16 ("REMOVE ALL THE CAPS") - the core-slot count, the schema character
# budget, the rule that kept tasking read-outs and side agents out of the on-box brain - and those are
# gone. This number is different in kind: underneath it the prompt still goes through as a FILE (measured
# good at 17,783 chars), and past it the honest move is to refuse BY NAME rather than hand back the
# silent empty answer that looks exactly like a clogged turn.
MAX_PROMPT_CHARS = 120000
MAX_TOKENS_NOTE = ("the free CLI answers in one shot; max_tokens is advisory here and is not "
                   "truncating anything - the answer comes back whole or not at all")

_LOCK = threading.Lock()
_SERVER: dict[str, Any] = {"httpd": None, "port": 0, "thread": None, "started": 0.0,
                           "answers": 0, "errors": 0, "last_model": "", "last_seconds": 0.0,
                           "last_error": {}, "requests": []}


def _note_request(model: str, prompt_chars: int, code: int, seconds: float, why: str = "",
                  via: str = "") -> None:
    """A small ring of what actually arrived, so "the adapter returned 502" is never a dead end."""
    row = {"at": time.strftime("%H:%M:%S"), "model": model, "prompt_chars": int(prompt_chars),
           "code": int(code), "seconds": round(seconds, 2), "via": via, "why": str(why)[:200]}
    _SERVER["requests"] = (_SERVER.get("requests") or [])[-7:] + [row]
    if code >= 400:
        _SERVER["last_error"] = row


# --------------------------------------------------------------------------------------
# the one place a free request is spent
# --------------------------------------------------------------------------------------
def _models() -> list[str]:
    """The roster, read through the side-agent harness so there is one source of truth."""
    try:
        import delegate_tools as dt
        return [str(x) for x in (dt.subagent_pool(probe=False).get("free") or [])]
    except Exception:  # noqa: BLE001 - an unreadable roster is an empty list, not a crash
        return []


def _answer_via_cli(prompt: str, model: str, timeout: int = CLI_TIMEOUT_S) -> dict[str, Any]:
    """One answer, from the CLI, read-only. The only function here that spends a free request."""
    import delegate_tools as dt
    return dt.subagent_run(task=prompt, engine=model, read_only=True, timeout=int(timeout))


def _prompt_from(messages: Any) -> str:
    """Flatten an OpenAI message list into the single prompt the CLI takes.

    The CLI is an agent, so the head tells it not to act like one: this is a chat turn, and a
    tool round here would answer a question nobody asked. `read_only=True` denies the tools as
    well - two controls, because the permission shape has been silently ignored once before.
    """
    parts: list[str] = []
    for m in messages if isinstance(messages, list) else []:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "user").lower()
        content = m.get("content")
        if isinstance(content, list):
            content = " ".join(str(p.get("text") or "") for p in content if isinstance(p, dict))
        text = str(content or "").strip()
        if not text:
            continue
        label = {"system": "SYSTEM", "assistant": "ASSISTANT", "tool": "TOOL"}.get(role, "USER")
        parts.append(f"{label}: {text}")
    if not parts:
        return ""
    # The head is housekeeping, not a greeting to reply to: measured live, a weak free model answered
    # "Ready. Ask your question." to a preamble that came across as a question of its own, and the
    # visitor's line went unanswered. So the head (a) forbids the acknowledgement, and (b) points at the
    # USER line as the one thing to do.
    # MEASURED 2026-10-16, the steward's test video. The old head - "do not use tools or read files" -
    # made a free model answer a render request with "this session has no `video_generate` limb wired;
    # available tools are only file search, web search, and read/grep" and render nothing, with zero
    # traces. The prohibition was about the CLI's OWN agent behaviour (the console drives the tool round
    # and read_only=True denies the harness tools) - but the model read it as "you have no tools", and its
    # harness agreed with that reading. So the head names what it means and says which tools ARE the turn's.
    head = ("Context above is background. Do not greet, do not acknowledge these instructions, and do not "
            "describe yourself. Do not use the CLI harness's own tools (file search, web search, read, "
            "grep): they are denied, and they are not the tools of this turn. The tools of this turn are "
            "the console limbs named in the context above - when the task needs work, NAME THE CALL as "
            "`limb_name(arg=value)` on a line of its own and the console will run it and hand you the "
            "result. Never answer that a tool does not exist because your harness lacks it, and never "
            "claim a result you did not see. The USER line is the task: begin with the answer itself.")
    return head + "\n\n" + "\n\n".join(parts) + "\n\nASSISTANT (answer the USER line):"


def _prompt_for_cli(prompt: str, model: str) -> tuple[str, str, str]:
    """(what the CLI is handed on its command line, the file it must read, how it travelled).

    A long prompt cannot go through argv at all (see ARGV_PROMPT_CHARS), so it is written to a file
    and the argv carries ONE sentence naming it. `read` is deliberately not one of the denied tools:
    that is the route the text takes in. Nothing is spent until the CLI actually runs.
    """
    if len(prompt) <= ARGV_PROMPT_CHARS:
        return prompt, "", "argv"
    try:
        from paths import WORKSPACE
        folder = WORKSPACE / "subagents" / ("freebrain-" + time.strftime("%Y%m%d-%H%M%S"))
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "prompt.txt"
        path.write_text(prompt, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - reported, never raised at the caller
        return prompt, "", f"file_failed:{type(exc).__name__}"
    task = (f"Read the file {path} completely, then follow the request at the END of it. "
            f"Reply with the answer only - no preamble, no commentary.")
    return task, str(path), "file"


def transport_now() -> str:
    """What the LAST request travelled by - the ring's own answer, so the reply cannot drift from it."""
    rows = _SERVER.get("requests") or []
    return str((rows[-1] if rows else {}).get("via") or "")


def _openai_reply(model: str, text: str, seconds: float) -> dict[str, Any]:
    return {
        "id": "lygo-freebrain-%d" % int(time.time() * 1000),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "lygo": {"source": "opencode-free-via-cli", "transport": transport_now(), "seconds": round(seconds, 1),
                 "note": "token counts are not reported by this route; the timing is real"},
    }


# --------------------------------------------------------------------------------------
# the shim
# --------------------------------------------------------------------------------------
#: Origins a browser page may call this shim from. The LYGO API Portal (chatagent.ca/portal) already
#: lists http://127.0.0.1:* in its own connect-src, so the local route works with no page change; what
#: it did NOT have was anything to talk to. Kept as an allowlist rather than "*": any page on the web
#: could otherwise spend an operator's metered free requests from their own machine.
ALLOWED_ORIGINS = (
    "https://chatagent.ca",
    "https://www.chatagent.ca",
    "https://justinhelmer.ca",
    "https://www.justinhelmer.ca",
)


def _origin_ok(origin: str) -> bool:
    o = str(origin or "").strip().rstrip("/")
    if not o:
        return False
    if o in ALLOWED_ORIGINS:
        return True
    # a page served from the operator's own machine (dev, a saved file, a local host)
    return o.startswith("http://127.0.0.1:") or o.startswith("http://localhost:")


class _Handler(BaseHTTPRequestHandler):
    server_version = "lygo-free-brain/" + SIGNATURE.split("-")[-1]

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003 - keep the console's stderr clean
        return

    def _send(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            for name, value in self._cors().items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)
        except Exception:  # noqa: BLE001 - a client that hung up is not our failure
            pass

    def _cors(self) -> dict[str, str]:
        """The headers that let a page call this. Absent for an origin we do not know."""
        from urllib.parse import urlsplit

        origin = ""
        try:
            origin = str(urlsplit(str(self.headers.get("Origin") or "")).geturl() or "")
        except Exception:  # noqa: BLE001
            origin = ""
        if not _origin_ok(origin):
            return {}
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Lygo-Filename",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Max-Age": "600",
            "Vary": "Origin",
        }

    def do_OPTIONS(self) -> None:  # noqa: N802 - a JSON POST is preflighted, so this must answer
        headers = self._cors()
        self.send_response(204 if headers else 403)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _path(self) -> str:
        return (self.path or "/").split("?", 1)[0].rstrip("/") or "/"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's own name
        path = self._path()
        if path in ("/v1/models", "/models"):
            self._send(200, {"object": "list",
                             "data": [{"id": m, "object": "model", "owned_by": "opencode"} for m in _models()]})
        elif path in ("/health", "/"):
            self._send(200, {"ok": True, "signature": SIGNATURE, "listening": _SERVER["port"],
                             "answers": _SERVER["answers"], "errors": _SERVER["errors"],
                             "last_model": _SERVER["last_model"],
                             "last_error": _SERVER.get("last_error") or {},
                             "requests": _SERVER.get("requests") or []})
        else:
            self._send(404, {"error": {"message": f"no such route: {self.path}",
                                       "type": "lygo_free_brain_404"}})

    def do_POST(self) -> None:  # noqa: N802
        if self._path() != "/v1/chat/completions":
            self._send(404, {"error": {"message": f"no such route: {self.path}",
                                       "type": "lygo_free_brain_404"}})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            body = json.loads(raw.decode("utf-8", "replace") or "{}")
        except Exception as exc:  # noqa: BLE001
            self._send(400, {"error": {"message": f"body is not JSON: {exc}",
                                       "type": "lygo_free_brain_400"}})
            return
        if not isinstance(body, dict):
            self._send(400, {"error": {"message": "body is not an object", "type": "lygo_free_brain_400"}})
            return
        model = str(body.get("model") or _SERVER.get("last_model") or "").strip()
        prompt = _prompt_from(body.get("messages"))
        if not prompt:
            self._send(400, {"error": {"message": "no user text in messages", "type": "lygo_free_brain_400"}})
            return
        if not model:
            self._send(400, {"error": {"message": "no model: the switch names one when it is turned on",
                                       "type": "lygo_free_brain_400"}})
            return
        if MAX_PROMPT_CHARS and len(prompt) > MAX_PROMPT_CHARS:
            why = ("prompt_too_long: %d characters of prompt; this route refuses past %d rather than "
                   "spend a free request on a turn that cannot fit. Nothing was sent."
                   % (len(prompt), MAX_PROMPT_CHARS))
            _SERVER["errors"] += 1
            _note_request(model, len(prompt), 502, 0.0, why, "refused")
            self._send(502, {"error": {"message": why, "type": "lygo_free_brain_prompt_too_long",
                                       "prompt_chars": len(prompt), "limit": MAX_PROMPT_CHARS,
                                       "engine": model}})
            return
        task, prompt_file, transport = _prompt_for_cli(prompt, model)
        if transport.startswith("file_failed"):
            why = ("prompt_file_failed: the prompt is over the %d-character argv ceiling and could not "
                   "be written to a file (%s). Nothing was sent." % (ARGV_PROMPT_CHARS, transport))
            _SERVER["errors"] += 1
            _note_request(model, len(prompt), 502, 0.0, why, transport)
            self._send(502, {"error": {"message": why, "type": "lygo_free_brain_prompt_file_failed",
                                       "engine": model}})
            return
        started = time.time()
        got = _answer_via_cli(task, model)
        seconds = time.time() - started
        _SERVER["last_model"] = model
        _SERVER["last_seconds"] = round(seconds, 1)
        answer = str(got.get("answer") or "").strip()
        if got.get("ok") and answer:
            _SERVER["answers"] += 1
            _note_request(model, len(prompt), 200, seconds, via=transport)
            self._send(200, _openai_reply(model, answer, seconds))
            return
        _SERVER["errors"] += 1
        # The CLI's own words, with its ref. The masked limit, a retired model id and a bad run
        # directory all arrive as the same sentence, so this never names one of them as the cause.
        bits = [str(got.get("error") or "no_answer"), str(got.get("hint") or "")]
        for e in (got.get("engine_error") or [])[:2]:
            if isinstance(e, dict) and (e.get("message") or e.get("name")):
                bits.append(f"{e.get('name') or 'error'}: {e.get('message') or ''}"
                            + (f" (ref {e.get('ref')})" if e.get("ref") else ""))
        _note_request(model, len(prompt), 502, seconds, " | ".join(b for b in bits if b), transport)
        self._send(502, {"error": {"message": " | ".join(b for b in bits if b),
                                   "type": "lygo_free_brain_engine_error",
                                   "engine": model, "seconds": round(seconds, 1)}})


def _url() -> str:
    return f"http://127.0.0.1:{int(_SERVER['port'] or 0)}"


def serve(port: int = DEFAULT_PORT) -> dict[str, Any]:
    """Start the shim (idempotent). Loopback only, daemon thread, dies with the console."""
    with _LOCK:
        if _SERVER.get("httpd") is not None:
            return {"ok": True, "already": True, "url": _url(), "port": _SERVER["port"],
                    "signature": SIGNATURE}
        why = ""
        # port=None means the house default; port=0 means "any free port" (tests use this).
        for candidate in (int(DEFAULT_PORT if port is None else port), 0):
            try:
                httpd = ThreadingHTTPServer(("127.0.0.1", candidate), _Handler)
            except OSError as exc:
                why = f"{type(exc).__name__}: {exc}"
                continue
            _SERVER["httpd"] = httpd
            _SERVER["port"] = int(httpd.server_address[1])
            _SERVER["started"] = time.time()
            thread = threading.Thread(target=httpd.serve_forever, name="lygo-free-brain", daemon=True)
            thread.start()
            _SERVER["thread"] = thread
            return {"ok": True, "url": _url(), "port": _SERVER["port"], "signature": SIGNATURE}
        return {"ok": False, "error": "bind_failed", "why": why}


def stop() -> dict[str, Any]:
    """Stop the shim. Safe to call when it is not running."""
    with _LOCK:
        httpd = _SERVER.get("httpd")
        if httpd is None:
            return {"ok": True, "already": True, "stopped": False}
        try:
            httpd.shutdown()
            httpd.server_close()
        except Exception as exc:  # noqa: BLE001 - report, never raise
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        _SERVER.update({"httpd": None, "thread": None, "port": 0})
        return {"ok": True, "stopped": True}


def listening() -> bool:
    return _SERVER.get("httpd") is not None


# --------------------------------------------------------------------------------------
# the switch
# --------------------------------------------------------------------------------------
def _load_state(state_path: Any) -> dict[str, Any]:
    try:
        data = json.loads(open(str(state_path), encoding="utf-8").read())
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 - no state file yet
        return {}


def _save_state(state_path: Any, payload: dict[str, Any]) -> str:
    try:
        from atomicio import atomic_write_text
        atomic_write_text(state_path, json.dumps(payload, indent=2))
        return ""
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"


def _cloud_snapshot() -> dict[str, Any]:
    """What the brain was before the switch: provider, model, url, enabled - never a key.

    Read from `config/api.json` directly because the operator's exact model must come back
    unchanged. Key material is deliberately NOT copied into our state file: the keys stay where
    they are, and turning the switch off only rewrites which provider is primary.
    """
    import cloud_api
    out: dict[str, Any] = {"provider": "", "model": "", "url": "", "enabled": False}
    try:
        raw = json.loads(open(str(cloud_api.API_PATH), encoding="utf-8").read())
        if isinstance(raw, dict):
            out["provider"] = str(raw.get("provider") or "")
            out["model"] = str(raw.get("model") or "")
            out["url"] = str(raw.get("url") or "")
            out["enabled"] = bool(raw.get("enabled"))
    except Exception:  # noqa: BLE001 - no config yet
        pass
    return out


def on(model: str, state_path: Any) -> dict[str, Any]:
    """Point the console's brain at the free pool. Returns a receipt, never raises."""
    import cloud_api
    model = str(model or "").strip()
    if not model:
        return {"ok": False, "error": "no_model",
                "hint": "name a free engine (subagent_pool lists them), e.g. opencode/glm-5.3-flash-free"}
    started = serve()
    if not started.get("ok"):
        return {"ok": False, "error": "shim_failed", "why": started.get("why")}
    previous = _cloud_snapshot()
    url = _url() + "/v1/chat/completions"
    try:
        cloud_api.save({"provider": "custom", "url": url, "model": model,
                        "key": PLACEHOLDER_KEY, "enabled": True})
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "cloud_save_failed", "why": f"{type(exc).__name__}: {exc}"}
    st = cloud_api.public_status()
    state = {"enabled": True, "model": model, "url": url, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
             "previous": previous, "placeholder_key": PLACEHOLDER_KEY}
    note = _save_state(state_path, state)
    return {"ok": True, "action": "brain_on", "model": model, "url": url,
            "cloud_provider": st.get("provider"), "cloud_enabled": st.get("enabled"),
            "previous": previous, "note": note or "recorded in save/delegate/free_brain.json"}


def off(state_path: Any) -> dict[str, Any]:
    """Put the brain back where it was, then stop the shim."""
    import cloud_api
    state = _load_state(state_path)
    prev = state.get("previous") if isinstance(state.get("previous"), dict) else {}
    patch: dict[str, Any] = {"enabled": bool(prev.get("enabled", False))}
    if prev.get("provider"):
        patch["provider"] = str(prev["provider"])
        patch["model"] = str(prev.get("model") or "")
    else:
        patch["enabled"] = False
    try:
        cloud_api.save(patch)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "cloud_save_failed", "why": f"{type(exc).__name__}: {exc}"}
    stopped = stop()
    st = cloud_api.public_status()
    state.update({"enabled": False, "at": time.strftime("%Y-%m-%d %H:%M:%S"), "model": ""})
    note = _save_state(state_path, state)
    return {"ok": True, "action": "brain_off", "restored": patch, "cloud_provider": st.get("provider"),
            "cloud_enabled": st.get("enabled"), "shim": stopped,
            "note": note or "recorded in save/delegate/free_brain.json"}


def ensure_if_enabled(state_path: Any) -> dict[str, Any]:
    """The adapter comes back with the console when the switch is already ON.

    Measured the hard way: the console was restarted with the switch on. `config/api.json` still
    pointed at the shim, the shim was gone, and every turn paid one failed hop - connection refused -
    before the console's own walk handed it to the next provider. The switch being on IS the operator's
    standing choice, so the adapter is rebound at boot. This is a loopback bind: no request to a
    metered service, no subprocess, and nothing is spent.
    """
    state = _load_state(state_path)
    if not state.get("enabled"):
        return {"ok": True, "healed": False, "why": "the switch is off"}
    if listening():
        return {"ok": True, "healed": False, "why": "already listening", "port": _SERVER["port"]}
    got = serve()
    return {"ok": bool(got.get("ok")), "healed": bool(got.get("ok")), "port": got.get("port"),
            "why": got.get("why") or "rebound at boot"}


def status(state_path: Any, roster: list[str] | None = None) -> dict[str, Any]:
    """Where the brain is now, and whether it is the shim answering - checked, not assumed."""
    import cloud_api
    state = _load_state(state_path)
    st = cloud_api.public_status()
    url = str(st.get("url") or "")
    on_shim = bool(url and listening() and f"127.0.0.1:{_SERVER['port']}" in url)
    return {
        "signature": SIGNATURE,
        "enabled": bool(state.get("enabled")),
        "model": str(state.get("model") or ""),
        "url": str(state.get("url") or ""),
        "previous": state.get("previous") or {},
        "shim": {"listening": listening(), "port": _SERVER["port"], "url": _url(),
                 "answers": _SERVER["answers"], "errors": _SERVER["errors"],
                 "last_model": _SERVER["last_model"], "last_seconds": _SERVER["last_seconds"],
                 "last_error": _SERVER.get("last_error") or {},
                 "requests": _SERVER.get("requests") or []},
        "cloud": {"provider": st.get("provider"), "model": st.get("model"), "url": url,
                  "enabled": bool(st.get("enabled")), "label": st.get("label"),
                  "points_at_shim": on_shim, "has_key": bool(st.get("has_key"))},
        "free": list(roster or _models()),
        "max_tokens_note": MAX_TOKENS_NOTE,
        "cost": ("every turn spawns the OpenCode CLI: seconds per answer, no token streaming, "
                 "metered by the free tier and masked when exhausted"),
    }
