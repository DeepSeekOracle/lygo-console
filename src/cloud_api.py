"""Cloud API brain for the admin console. Keys stay in gitignored config/api.json. Never echo secrets."""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from paths import CONFIG, ensure_dirs
from atomicio import atomic_write_text

from brain_router import API as API_MODE
from brain_router import LOCAL as LOCAL_MODE
from brain_router import mode_of

API_PATH = CONFIG / "api.json"

# After a failed API turn the console stops trying for this long and lets the local
# engine answer; an explicit re-activation (or clear_error) lifts it immediately.
COOLDOWN_S = 300.0

# What the console can SEND through each provider, taken from that provider's own documentation - never
# inferred from a model name. A route with no image capability is not a broken route: it is a text route,
# and the console routes a photo to one that accepts pictures (or to the local vision model) instead of
# letting a text model answer about a photo it never saw. `source` travels in the payload so the operator
# can check the claim, and `custom` claims NOTHING because an unknown endpoint's limits are not ours to assert.
PROVIDER_CAPS: dict[str, dict[str, Any]] = {
    "deepseek": {"caps": ["text", "tools"], "source": "https://api-docs.deepseek.com/"},
    "groq": {"caps": ["text", "tools"], "source": "https://console.groq.com/docs/models"},
    "openai": {"caps": ["text", "tools", "image-in"], "source": "https://platform.openai.com/docs/guides/vision"},
    "xai": {"caps": ["text", "tools", "image-in"], "source": "https://docs.x.ai/docs/models"},
    "gemini": {"caps": ["text", "tools", "image-in", "sound-in"],
               "source": "https://ai.google.dev/gemini-api/docs/vision"},
    "openrouter": {"caps": ["text", "tools", "image-in"],
                   "source": "https://openrouter.ai/docs/features/multimodal/overview"},
    "custom": {"caps": [], "source": "unknown - a custom endpoint declares its own capabilities"},
    "ofm": {"caps": ["text", "tools"], "source": "http://127.0.0.1:18900/v1 — Our Free Model standalone (DSH plugin / OpenCode+Kilo pool)"},
    "opencode": {"caps": ["text", "tools"], "source": "local opencode CLI 1.19 — the same free models the Forge Manager dispatches"},
    "forge-nvidia": {"caps": ["text", "tools"], "source": "http://127.0.0.1:8082/v1 — Forge Manager NVIDIA bridge"},
}

PROVIDERS: dict[str, dict[str, Any]] = {
    "deepseek": {
        "label": "DeepSeek",
        "url": "https://api.deepseek.com/v1/chat/completions",
        "model": "deepseek-chat",
        "models": ["deepseek-chat", "deepseek-reasoner", "deepseek-flash"],
        "help": "platform.deepseek.com — keys. Model deepseek-chat or deepseek-flash.",
    },
    # NVIDIA NIM (build.nvidia.com). Added for the operator's test key: its account answers on
    # nemotron-3-super-120b-a12b - the model this kit's LOCAL engine cannot read (ledger L3), so
    # the API is a real workaround for it. Measured: with thinking ON that model writes its
    # reasoning into the answer (and sets reasoning_content), with thinking OFF it answers
    # cleanly. The same is true of the other nemotron reasoning ids, so it is off by default.
    "nvidia": {
        "label": "NVIDIA NIM",
        "url": "https://integrate.api.nvidia.com/v1/chat/completions",
        "model": "nvidia/nemotron-3.5-lightning-30b-a3b",
        "models": [
            "nvidia/nemotron-3-super-120b-a12b",
            "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
            "nvidia/nemotron-3.5-lightning-30b-a3b",
            "meta/llama-3.2-11b-vision-instruct",
        ],
        "body": {"chat_template_kwargs": {"thinking": False}},
        "help": "build.nvidia.com - OpenAI-compatible. The test account answers on nemotron-3-super, "
                "nemotron-3-nano-omni, nemotron-3.5-lightning and llama-3.2-11b-vision; every other id "
                "is listed but not enabled for it, and the account has worker rate limits.",
    },
    "groq": {
        "label": "Groq",
        # Measured: api.groq.com answers urllib with Cloudflare Error 1010 (browser-integrity
        # block), 403, even with a valid key. One browser-like User-Agent and the same call
        # answers 200. This is what `extra` is for: headers the provider needs.
        "extra": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/128.0 Safari/537.36",
                  "Accept": "application/json"},
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "model": "openai/gpt-oss-20b",
        "models": ["openai/gpt-oss-20b", "openai/gpt-oss-120b"],
        "help": "console.groq.com/keys",
    },
    "openai": {
        "label": "OpenAI",
        "url": "https://api.openai.com/v1/chat/completions",
        "model": "gpt-4o-mini",
        "models": ["gpt-4o-mini", "gpt-4o"],
        "help": "platform.openai.com",
    },
    "xai": {
        "label": "xAI Grok",
        "url": "https://api.x.ai/v1/chat/completions",
        "model": "grok-2-latest",
        "models": ["grok-2-latest", "grok-3"],
        "help": "console.x.ai",
    },
    "gemini": {
        "label": "Google Gemini",
        "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "model": "gemini-flash-latest",
        "models": ["gemini-flash-latest", "gemini-pro-latest", "gemini-2.0-flash", "gemini-2.0-flash-001"],
        "help": "aistudio.google.com/apikey — OpenAI-compatible Gemini. Use the -latest ids: the 1.5/2.5 pins are retired.",
    },
    "openrouter": {
        "label": "OpenRouter",
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "model": "openrouter/auto",
        "models": ["openrouter/auto"],
        "help": "openrouter.ai/keys",
    },
    "custom": {
        "label": "OpenAI-compatible URL",
        "url": "",
        "model": "",
        "models": [],
        "help": "Full chat/completions URL + key + model id",
    },
    "ofm": {
        "label": "Our Free Model (18900)",
        "url": "http://127.0.0.1:18900/v1/chat/completions",
        "model": "kilo-auto/free",
        "models": [
            "kilo-auto/free",
            "nvidia/nemotron-3.5-lightning:free",
            "nvidia/nemotron-3.5-super-vl:free",
            "openrouter/free",
            "cohere/north-mini-code:free",
            "glm-5.3-flash-free",
            "deepseek-v4.1-flash-free",
            "mimo-v2.6-flash-free",
        ],
        "help": "Local Our Free Model gateway on 18900. Start Desktop\\LYGO Model Boots\\00-START-FREE-GATEWAY.bat. GLM/DeepSeek Flash may 429 when the OpenCode free pool is empty; Kilo IDs were live on this PC.",
    },
    "opencode": {
        "label": "OpenCode Free (Forge)",
        "url": "http://127.0.0.1:18901/v1/chat/completions",
        "model": "opencode/deepseek-v4.1-flash-free",
        "models": [
            "opencode/deepseek-v4.1-flash-free",
            "opencode/beam-free",
            "opencode/flint-40b-free",
            "opencode/glm-5.3-flash-free",
            "opencode/mimo-v2.6-flash-free",
            "opencode/minimax-m3.1-flash-free",
            "opencode/nemotron-3-ultra-free",
            "opencode/nemotron-3.5-lightning-free",
            "opencode/nemotron-3.5-super-vl-free",
        ],
        "kind": "opencode",
        "help": "Free OpenCode models already used by the Forge Manager; runs the local opencode CLI per turn.",
    },
    "forge-nvidia": {
        "label": "NVIDIA Bridge (Forge 8082)",
        "url": "http://127.0.0.1:8082/v1/chat/completions",
        "model": "nvidia/nemotron-3-ultra-550b-a55b",
        "models": [
            "nvidia/nemotron-3-ultra-550b-a55b",
            "z-ai/glm-5.3-flash",
        ],
        "help": "Forge Manager NVIDIA bridge on 8082 (key: local-bridge). Works in the Forge today.",
    },
}

# --- house policy: DeepSeek is the default API and the standing backup -----------------------
# One rule, one place: with nothing wired — or with a provider name we do not ship — the API side
# of this brain IS DeepSeek, and DeepSeek also leads the fallback order behind any other primary.
DEFAULT_PROVIDER = "nvidia"
DEFAULT_MODEL = "deepseek-chat"


# ---- two-key failover (admin stick upgrade) -------------------------------------------
# The primary brain is `provider` + `key`. Every provider named in `fallbacks` that also holds
# a key in the `keys` map is tried, in order, when the primary answers with an auth, quota or
# server error. The local engine stays the last resort, so a dead cloud chain still answers.
FAIL_CODES = (401, 402, 403, 408, 409, 429, 500, 502, 503, 504)


def sanitize_key(k: str) -> str:
    k = (k or "").replace("\ufeff", "")
    k = k.replace("\u200b", "").replace("\u00a0", "")
    k = k.strip().replace("Bearer ", "").replace("bearer ", "")
    k = k.strip("\"'`")
    k = "".join(ch for ch in k if 33 <= ord(ch) <= 126)
    return k


def _blank() -> dict[str, Any]:
    return {
        "enabled": False,
        "provider": DEFAULT_PROVIDER,
        "model": DEFAULT_MODEL,
        "url": "",
        "key": "",
        "keys": {},
        "fallbacks": [],
        "last_provider": "",
        "chain_tried": [],
        "chain_at": 0.0,
        "chain_when": "",
        "degraded": False,
        "last_error": "",
        "last_code": 0,
        "last_at": 0.0,
        "handoffs": 0,
    }


def _load() -> dict[str, Any]:
    if not API_PATH.is_file():
        return _blank()
    try:
        obj = json.loads(API_PATH.read_text(encoding="utf-8"))
    except Exception:
        obj = {}
    if not isinstance(obj, dict):
        obj = {}
    obj.setdefault("enabled", False)
    # A config that names a provider we do not ship (hand-edited, retired, or a typo) must never
    # become a chain entry posting to an empty URL: fall back to the house default instead.
    if str(obj.get("provider") or "").strip() not in PROVIDERS:
        obj["provider"] = DEFAULT_PROVIDER
    if not str(obj.get("model") or "").strip():
        obj["model"] = DEFAULT_MODEL
    obj.setdefault("url", "")
    obj.setdefault("key", "")
    obj.setdefault("degraded", False)
    obj.setdefault("last_error", "")
    obj.setdefault("last_code", 0)
    obj.setdefault("last_at", 0.0)
    obj.setdefault("handoffs", 0)
    obj.setdefault("last_provider", "")
    obj["key"] = sanitize_key(str(obj.get("key") or ""))
    raw_keys = obj.get("keys")
    clean: dict[str, str] = {}
    if isinstance(raw_keys, dict):
        for name, val in raw_keys.items():
            v = sanitize_key(str(val or ""))
            if v:
                clean[str(name)] = v
    obj["keys"] = clean
    raw_fb = obj.get("fallbacks")
    seen: list[str] = []
    if isinstance(raw_fb, list):
        for name in raw_fb:
            n = str(name or "").strip()
            if n and n not in seen:
                seen.append(n)
    obj["fallbacks"] = seen
    tried = obj.get("chain_tried")
    obj["chain_tried"] = [str(x) for x in tried][-8:] if isinstance(tried, list) else []
    return obj


def save(obj: dict[str, Any]) -> dict[str, Any]:
    ensure_dirs()
    CONFIG.mkdir(parents=True, exist_ok=True)
    cur = _load()
    if "key" in obj:
        k = sanitize_key(str(obj.get("key") or ""))
        if k:
            cur["key"] = k
            # File it under its provider as well. The legacy single-key field cannot say who owns a
            # key once the operator switches providers, and a chain candidate is only built from a
            # provider's own key.
            owner = str(obj.get("provider") or cur.get("provider") or DEFAULT_PROVIDER).strip()
            if owner in PROVIDERS:
                if not isinstance(cur.get("keys"), dict):
                    cur["keys"] = {}
                cur["keys"][owner] = k
        elif obj.get("key") == "":
            cur["key"] = ""
    if isinstance(obj.get("keys"), dict):
        # merge, per provider: a non-empty value wires that provider up, "" unwires just it
        for name, val in obj["keys"].items():
            n = str(name or "").strip()
            if not n:
                continue
            v = sanitize_key(str(val or ""))
            if v:
                cur["keys"][n] = v
            elif val == "":
                cur["keys"].pop(n, None)
    if isinstance(obj.get("fallbacks"), list):
        cur["fallbacks"] = [str(x or "").strip() for x in obj["fallbacks"] if str(x or "").strip()]
    if "provider" in obj:
        _was = str(cur.get("provider") or "")
        cur["provider"] = str(obj.get("provider") or "deepseek")
        # The model must follow the provider. Measured 2026-09-21 on the running console: choosing a
        # provider left the previous provider's model in place, so every turn posted `deepseek-chat`
        # to NVIDIA, Gemini and Groq alike and came back 404 "model not found" - reported by the
        # operator as "the API system is not working". An explicit model in the same call still wins,
        # and a save that does not change the provider leaves the model alone.
        if "model" not in obj and cur["provider"] != _was:
            cur["model"] = str((PROVIDERS.get(cur["provider"]) or {}).get("model") or cur.get("model") or "")
            # a new provider has not walked anything yet: last turn's codes read as this turn's
            cur["chain_tried"] = []
    if "model" in obj:
        cur["model"] = str(obj.get("model") or "")
    if "url" in obj:
        cur["url"] = str(obj.get("url") or "").strip()
    if "enabled" in obj:
        cur["enabled"] = bool(obj.get("enabled"))
    if cur.get("enabled") or obj.get("clear_error"):
        # switching the API on (or an explicit clear) ends the previous handoff
        cur["degraded"] = False
        cur["last_error"] = ""
        cur["last_code"] = 0
    p = PROVIDERS.get(cur["provider"]) or PROVIDERS["deepseek"]
    if cur["provider"] != "custom":
        cur["url"] = p.get("url") or cur.get("url") or ""
    if not cur.get("model"):
        cur["model"] = p.get("model") or "deepseek-chat"
    atomic_write_text(API_PATH, json.dumps(cur, indent=2))
    return public_status()


def chain_label(state: dict[str, Any] | None = None) -> str:
    """What the panel's walk readout MEANS: the last cloud walk, and when - never "this turn".

    A turn that is answered locally, or served from the chain's first key, may not walk at all, so the
    codes on screen can belong to an earlier turn. Saying so costs one clause and removes the only
    reading of that line that was untrue.
    """
    st = state
    if st is None:
        try:
            st = _load()
        except Exception:
            st = {}
    tried = [str(x) for x in (st.get("chain_tried") or [])]
    if not tried:
        return "no cloud walk recorded yet"
    when = str(st.get("chain_when") or "").strip()
    return "last cloud walk%s - %s" % ((" at " + when) if when else "", ", ".join(tried))


def hermes_route() -> dict[str, str]:
    """Loopback or configured OpenAI-compatible URL for the isolated Hermes.

    Returns empty when the API brain is off. The key is for the child process only.
    """
    cur = _load()
    if not cur.get("enabled"):
        return {}
    provider = str(cur.get("provider") or "")
    p = PROVIDERS.get(provider) or {}
    url = str(cur.get("url") or p.get("url") or "").strip()
    if "/chat/completions" in url:
        base = url.split("/chat/completions", 1)[0]
    else:
        base = url.rstrip("/")
    if not (base.startswith("http://") or base.startswith("https://")):
        return {}
    keys = cur.get("keys") if isinstance(cur.get("keys"), dict) else {}
    key = sanitize_key(str(cur.get("key") or keys.get(provider) or ""))
    model = str(cur.get("model") or p.get("model") or "").strip()
    if not model:
        return {}
    return {"base": base, "model": model, "key": key, "provider": provider}


def public_status() -> dict[str, Any]:
    cur = _load()
    p = PROVIDERS.get(cur.get("provider") or "") or {}
    chain = chain_for(cur)
    st: dict[str, Any] = {
        "ok": True,
        "enabled": bool(cur.get("enabled") and chain),
        "has_key": bool(chain),
        "key_count": len(chain),
        "keys_wired": [str(c.get("provider")) for c in chain],
        "chain": [str(c.get("label")) for c in chain],
        "last_provider": str(cur.get("last_provider") or ""),
        "chain_tried": list(cur.get("chain_tried") or []),
        "chain_at": float(cur.get("chain_at") or 0.0),
        "chain_when": str(cur.get("chain_when") or ""),
        "chain_label": chain_label(cur),
        "fallbacks": list(cur.get("fallbacks") or []),
        "failover": True,
        "default_provider": DEFAULT_PROVIDER,
        "is_default": str(cur.get("provider") or DEFAULT_PROVIDER) == DEFAULT_PROVIDER,
        "provider": cur.get("provider") or DEFAULT_PROVIDER,
        "label": p.get("label") or cur.get("provider"),
        "model": cur.get("model") or p.get("model"),
        "url": (cur.get("url") or p.get("url") or "")[:80],
        "help": p.get("help") or "",
        "providers": {k: {"label": v["label"], "model": v["model"], "help": v["help"],
                          "models": v.get("models") or [],
                          "caps": (PROVIDER_CAPS.get(k) or {}).get("caps") or [],
                          "caps_source": (PROVIDER_CAPS.get(k) or {}).get("source")}
                      for k, v in PROVIDERS.items()},
        "path": str(API_PATH),
        "secret": "never_echoed",
        "degraded": bool(cur.get("degraded")),
        "last_error": str(cur.get("last_error") or ""),
        "last_code": int(cur.get("last_code") or 0),
        "last_at": float(cur.get("last_at") or 0.0),
        "handoffs": int(cur.get("handoffs") or 0),
        "cooldown_s": COOLDOWN_S,
    }
    st["mode"] = mode_of(st)
    st["active"] = st["mode"] == API_MODE
    return st


# Providers whose answers arrive as *reasoning* first: given a small output budget they spend it
# thinking and return almost nothing. Measured 2026-09-21 - asked to reply "GEMINI OK" with a
# 64-token budget, Gemini returned "GEM" after 11.2 s and Groq's shipped model returned an empty
# string, while a plain provider answered the same ask in 0.9 s on the same budget.
REASONING_FLOOR = {"gemini": 512, "groq": 512}


def effective_max_tokens(provider: str, asked: Any) -> int:
    """The output budget a provider actually needs: at least its floor, never more than was asked."""
    try:
        want = int(asked)
    except (TypeError, ValueError):
        want = 0
    floor = int(REASONING_FLOOR.get(str(provider or "")) or 0)
    return 0 if want <= 0 else max(want, floor)


def local_payload_from(payload: dict[str, Any], model: str) -> dict[str, Any]:
    """The same turn, addressed to our own engine.

    When the API refuses and the local engine is asked to take over, the payload must not carry the
    API's model or the API's provider-only fields: measured 2026-09-21, a turn whose provider model
    was invalid fell back to an engine that was ready - *still carrying that model* - so the engine
    refused it too, nothing answered, and the operator was shown the "API handoff" notice instead
    of a reply.
    """
    out = dict(payload or {})
    out["model"] = str(model or "")
    for api_only in ("chat_template_kwargs", "extra_body", "stream_options", "reasoning_effort"):
        out.pop(api_only, None)
    return out


def chain_for(cur: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Ordered candidates: the primary (provider + key) first, then every keyed fallback.

    A provider is only ever a candidate if it actually holds a key, so a half-wired stick
    simply runs a shorter chain. Returns [] when nothing is keyed (caller treats as no key).
    """
    cur = _load() if cur is None else cur
    primary = str(cur.get("provider") or DEFAULT_PROVIDER)
    if primary not in PROVIDERS:
        primary = DEFAULT_PROVIDER
    keys = cur.get("keys") if isinstance(cur.get("keys"), dict) else {}
    order: list[str] = [primary]
    # DeepSeek also leads the BACKUP order: an operator who wired several keys and set no order
    # still fails over to the house default first. An explicit primary always outranks it.
    for name in [DEFAULT_PROVIDER] + list(cur.get("fallbacks") or []) + list(keys):
        n = str(name or "").strip()
        if n and n not in order:
            order.append(n)
    out: list[dict[str, Any]] = []
    for name in order:
        p = PROVIDERS.get(name) or {}
        # The legacy top-level `key` belongs to the provider that saved it. It is always offered to
        # the primary, and it stays available to the house default when the operator points the
        # console at another provider - otherwise switching the brain silently deleted a saved key
        # from the chain (measured live: `keys_wired` went from four providers to three the moment
        # the primary was moved, and a shorter chain is invisible until every remaining one is down).
        legacy = str(cur.get("key") or "")
        if legacy:
            claimed = [sanitize_key(str(v)) for k, v in keys.items() if str(k) != name]
            if legacy in claimed:
                legacy = ""  # a value already owned by another provider is not re-offered
        key = sanitize_key(str(keys.get(name) or (legacy if name in (primary, DEFAULT_PROVIDER) else "")))
        if not key:
            continue
        url = str(((cur.get("url") if name == primary else "") or p.get("url") or "")).strip()
        model = str(((cur.get("model") if name == primary else "") or p.get("model") or ""))
        out.append(
            {
                "provider": name,
                "label": str(p.get("label") or name),
                "key": key,
                "url": url,
                "model": model,
                "extra": p.get("extra") or {},
                "body": p.get("body") or {},
            }
        )
    return out


def enabled() -> bool:
    cur = _load()
    return bool(cur.get("enabled") and chain_for(cur))



# --- Forge routes -------------------------------------------------------------------------------
# The Forge Manager drives these successfully today (forge/orchestrator.py LANES). The console reuses
# the same model ids: `opencode` shells the local CLI, `forge-nvidia` is the 8082 bridge.
OPENCODE_EXE = os.environ.get("LYGO_OPENCODE") or r"<USER>\AppData\Roaming\npm\node_modules\opencode-ai\bin\opencode.exe"
# OpenCode treats its cwd as the project and scans it. Running in the home directory stalled every
# turn for 60-76 s; a clean scratch dir answers in ~4 s (measured 2026-10-29).
OPENCODE_CWD = Path(os.environ.get("LYGO_OPENCODE_CWD") or (CONFIG.parent / "workspace" / "opencode-bridge"))
OPENCODE_MODELS = [
    "opencode/deepseek-v4.1-flash-free",
    "opencode/beam-free",
    "opencode/flint-40b-free",
    "opencode/glm-5.3-flash-free",
    "opencode/mimo-v2.6-flash-free",
    "opencode/minimax-m3.1-flash-free",
    "opencode/nemotron-3-ultra-free",
    "opencode/nemotron-3.5-lightning-free",
    "opencode/nemotron-3.5-super-vl-free",
]




def ensure_opencode_bridge(timeout: float = 6.0) -> bool:
    """Start the OpenCode HTTP bridge if it is not listening. Called at console boot and lazily
    before an opencode turn, so the route survives a reboot without a manual step."""
    import socket
    import sys

    def up() -> bool:
        with socket.socket() as s:
            s.settimeout(0.3)
            return s.connect_ex(("127.0.0.1", 18901)) == 0

    if up():
        return True
    script = Path(__file__).resolve().parent.parent / "tools" / "opencode_bridge.py"
    if not script.exists():
        return False
    flags = 0
    if os.name == "nt":
        flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen([sys.executable, str(script)], cwd=str(script.parent.parent),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=flags)
    except Exception:
        return False
    deadline = time.time() + max(1.0, timeout)
    while time.time() < deadline:
        if up():
            return True
        time.sleep(0.2)
    return up()


def _model_vision(provider: str, model: str) -> bool:
    """Vision is a property of the model id first, then of the route's declared caps."""
    mid = str(model or "").lower()
    if any(tag in mid for tag in ("vision", "-vl", "vl-", "omni")):
        return True
    return "image-in" in ((PROVIDER_CAPS.get(str(provider or "")) or {}).get("caps") or [])


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(p.get("text") or "") for p in content
                         if isinstance(p, dict) and p.get("type") == "text")
    return ""


def _has_image_body(body: dict[str, Any]) -> bool:
    for m in body.get("messages") or []:
        c = m.get("content") if isinstance(m, dict) else None
        if isinstance(c, list) and any(isinstance(p, dict) and p.get("type") in {"image_url", "image"} for p in c):
            return True
    return False


def _strip_images(body: dict[str, Any]) -> dict[str, Any]:
    """A text route must never be handed a photo: NVIDIA answers 400 and the turn is lost."""
    out = dict(body)
    msgs = []
    for m in body.get("messages") or []:
        if not isinstance(m, dict):
            msgs.append(m)
            continue
        c = m.get("content")
        if isinstance(c, list):
            text = _text_of(c)
            if any(isinstance(p, dict) and p.get("type") in {"image_url", "image"} for p in c):
                text = (text + "\n[operator attached an image; this route has no vision]").strip()
            m = dict(m)
            m["content"] = text
        msgs.append(m)
    out["messages"] = msgs
    return out


def _opencode_prompt(body: dict[str, Any]) -> str:
    """Compact prompt for the CLI: Windows caps a command line at 32k, so a full transcript cannot
    be passed as one argument (measured: [WinError 206] filename or extension too long)."""
    msgs = [m for m in (body.get("messages") or []) if isinstance(m, dict)]
    system = [m for m in msgs if str(m.get("role")) == "system"]
    convo = [m for m in msgs if str(m.get("role")) != "system"][-4:]
    parts = []
    if system:
        parts.append("System: " + _text_of(system[-1].get("content"))[:2000])
    for m in convo:
        role = str(m.get("role") or "user")
        text = _text_of(m.get("content")).strip()
        if not text:
            continue
        prefix = "Assistant: " if role == "assistant" else "User: "
        parts.append(prefix + text[:3000])
    if _has_image_body(body):
        parts.append("[operator attached an image; the OpenCode CLI route is text-only]")
    prompt = "\n\n".join(parts).strip()
    return prompt[:12000]


def _opencode_chat(model: str, body: dict[str, Any], timeout: float = 180.0) -> tuple[int, bytes, str]:
    """One turn through `opencode run --format json`, the exact call the Forge Manager makes."""
    exe = OPENCODE_EXE if Path(OPENCODE_EXE).exists() else "opencode"
    prompt = _opencode_prompt(body)
    if not prompt:
        return 400, b'{"error":"empty_prompt"}', "application/json"
    cmd = [exe, "run", "--format", "json", "--auto", "--model", model, prompt]
    try:
        OPENCODE_CWD.mkdir(parents=True, exist_ok=True)
        # CREATE_NO_WINDOW: a console child of a windowless bridge pops a terminal window over
        # whatever Justin is doing. Measured 2026-10-30: five windows in 30 seconds.
        creation = 0x08000000 if os.name == "nt" else 0
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=min(float(timeout), 180.0),
                           cwd=str(OPENCODE_CWD), creationflags=creation)
    except subprocess.TimeoutExpired:
        return 504, json.dumps({"error": "opencode_timeout", "model": model}).encode(), "application/json"
    except Exception as exc:  # noqa: BLE001
        return 502, json.dumps({"error": "opencode_failed", "detail": str(exc)[:300]}).encode(), "application/json"
    text = ""
    for line in (r.stdout or "").splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict) and ev.get("type") == "text":
            text += str((ev.get("part") or {}).get("text") or "")
    if not text.strip():
        detail = ((r.stderr or "") + (r.stdout or ""))[-400:].strip()
        return 502, json.dumps({"error": "opencode_no_text", "detail": detail}).encode(), "application/json"
    payload = {
        "id": "chatcmpl-opencode", "object": "chat.completion", "created": int(time.time()), "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
    return 200, json.dumps(payload).encode(), "application/json"


def _post(url: str, key: str, body: dict[str, Any], extra: dict[str, Any], timeout: float) -> tuple[int, bytes, str]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + key,
        },
        method="POST",
    )
    for hk, hv in (extra or {}).items():
        req.add_header(str(hk), str(hv))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(), resp.headers.get("Content-Type") or "application/json"
    except urllib.error.HTTPError as e:
        return e.code, e.read() or b"{}", "application/json"
    except Exception as e:
        err = json.dumps({"error": str(e)[:400]}).encode("utf-8")
        return 502, err, "application/json"


def _why(code: int, body: bytes = b"") -> str:
    """The refusal in the operator's terms, naming the provider's own words when it gave any.

    The three that look alike are not alike: 401 means the key itself was refused, 402 means the account
    is out of credit, 429 means the key is fine but asking too fast. The next move differs in each case,
    and a single "the API failed" sentence would hide that. Never echoes key material - these sentences
    are written to `config/api.json` and printed in the portal.
    """
    said = ""
    try:
        parsed = json.loads(body.decode("utf-8", "replace") or "{}")
        err = parsed.get("error") if isinstance(parsed, dict) else None
        if isinstance(err, dict):
            said = str(err.get("message") or err.get("type") or "")
        elif err:
            said = str(err)
        elif isinstance(parsed, dict):
            said = str(parsed.get("message") or "")
    except Exception:  # noqa: BLE001
        said = ""
    said = " ".join(said.split())[:160]
    table = {
        400: "the provider refused the request as malformed (400)",
        401: "the provider rejected the API key (401)",
        402: "the account is out of credit (402)",
        403: "the key is not allowed to do this (403)",
        404: "the url or the model does not exist (404)",
        408: "the provider timed out (408)",
        429: "the key is being rate limited (429)",
        500: "the provider had a server error (500)",
        502: "the provider was unreachable (502)",
        503: "the provider is unavailable (503)",
    }
    head = table.get(int(code or 0), "the provider answered HTTP %s" % int(code or 0))
    return head + (": " + said if said else "")


def _note_failure(code: int, message: str = "", provider: str = "") -> None:
    """Record the code and the reason next to `chain_tried` - the walk's own, so no caller can drop them.

    Measured 2026-09-21: `config/api.json` read `chain_tried: ["deepseek:401"]` with `last_code: 0` and
    `last_error: ""`. The walk knew the answer and the portal's two fields were empty, so the page said
    "API handoff · " and stopped. Whichever caller lost the code, this is where it cannot be lost.
    """
    try:
        ensure_dirs()
        CONFIG.mkdir(parents=True, exist_ok=True)
        cur = _load()
        if provider:
            cur["last_provider"] = str(provider)
        cur["last_code"] = int(code or 0)
        if message:
            cur["last_error"] = " ".join(str(message).split())[:300]
        atomic_write_text(API_PATH, json.dumps(cur, indent=2))
    except Exception:
        pass


def _note_chain(tried: list[str], last_provider: str = "") -> None:
    """Record which keys were attempted (codes only — never key material)."""
    try:
        ensure_dirs()
        CONFIG.mkdir(parents=True, exist_ok=True)
        cur = _load()
        cur["chain_tried"] = [str(x) for x in tried][-8:]
        # The record is worth keeping - it must just say WHEN it was taken. Measured
        # 2026-09-21: one turn's refusals sat beside an answer another turn produced, so the
        # panel read as if this turn had failed over when it had not.
        cur["chain_at"] = time.time()
        cur["chain_when"] = time.strftime("%H:%M:%S", time.localtime())
        if last_provider:
            cur["last_provider"] = last_provider
        atomic_write_text(API_PATH, json.dumps(cur, indent=2))
    except Exception:
        pass


def _note_success(provider: str) -> None:
    try:
        ensure_dirs()
        CONFIG.mkdir(parents=True, exist_ok=True)
        cur = _load()
        cur["last_provider"] = str(provider)
        atomic_write_text(API_PATH, json.dumps(cur, indent=2))
    except Exception:
        pass


def _ledger_log(provider: str, model: str, data: bytes, seconds: float) -> None:
    """One line per API turn: provider, model, tokens, seconds. Never key material, never raises."""
    try:
        import json as _json
        import os as _os
        import time as _time
        from pathlib import Path as _Path

        payload = _json.loads((data or b"{}").decode("utf-8", "replace"))
        usage = payload.get("usage") or {}
        rec = {
            "at": _time.strftime("%Y-%m-%dT%H:%M:%S"),
            "source": "console",
            "provider": provider,
            "model": payload.get("model") or model,
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "seconds": round(seconds, 2),
        }
        base = _Path(_os.environ.get("LOCALAPPDATA") or str(_Path.home())) / "LYGO" / "AgentManager"
        base.mkdir(parents=True, exist_ok=True)
        with (base / "api-usage.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(_json.dumps(rec) + "\n")
    except Exception:  # noqa: BLE001 - the ledger must never break a turn
        pass

def chat(payload: dict[str, Any], timeout: float = 180.0) -> tuple[int, bytes, str]:
    """One API turn, walked down the wired key chain.

    The primary provider answers first; if it fails in a way another key could survive
    (auth / quota / rate limit / server error) the next keyed provider is tried. A request
    the provider merely rejected as malformed (4xx that is not in FAIL_CODES) stops the walk
    immediately — repeating it elsewhere would waste a second quota. When every key fails the
    LAST failure is returned untouched, so the console's existing handoff to the local engine
    (note_error -> cooldown -> local takeover) still fires with a truthful status code.
    """
    cur = _load()
    _t0 = time.time()
    chain = chain_for(cur)
    if not chain:
        return 401, b'{"error":"no_api_key"}', "application/json"
    last: tuple[int, bytes, str] = (401, b'{"error":"no_api_key"}', "application/json")
    tried: list[str] = []
    for i, cand in enumerate(chain):
        url = str(cand.get("url") or "")
        if str(cand.get("provider") or "") != "opencode" and not url.startswith("https://") and not url.startswith("http://127.0.0.1"):
            tried.append("%s:bad_url" % cand.get("provider"))
            last = (400, b'{"error":"bad_url"}', "application/json")
            continue
        body = dict(payload)
        # a reasoning provider given a small budget answers nothing: raise it, never lower an ask
        if body.get("max_tokens"):
            body["max_tokens"] = effective_max_tokens(str(cand.get("provider") or ""), body.get("max_tokens"))
        # A provider may need body fields the OpenAI shape does not have (NVIDIA's thinking
        # switch, reasoning effort). `extra` is headers; this is the body. The kit still owns
        # model and stream, so a provider entry can never point a turn at another model.
        body.update(cand.get("body") or {})
        body["model"] = cand.get("model") or payload.get("model")
        body["stream"] = False
        if str(cand.get("provider") or "") == "opencode" and not url.startswith("http"):
            code, data, ctype = _opencode_chat(str(body.get("model") or ""), body, timeout)
        else:
            if not _model_vision(str(cand.get("provider") or ""), str(body.get("model") or "")):
                body = _strip_images(body)
            if str(cand.get("provider") or "") == "opencode":
                ensure_opencode_bridge()
            code, data, ctype = _post(url, str(cand.get("key")), body, cand.get("extra") or {}, timeout)
            # the bridge is a convenience process: if it is not up, shell the CLI the Forge uses
            if str(cand.get("provider") or "") == "opencode" and int(code or 0) >= 400:
                code, data, ctype = _opencode_chat(str(body.get("model") or ""), body, timeout)
        tried.append("%s:%s" % (cand.get("provider"), code))
        last = (code, data, ctype)
        if code < 400:
            _note_success(str(cand.get("provider") or ""))
            if len(tried) > 1:
                _note_chain(tried, str(cand.get("provider") or ""))
            _ledger_log(str(cand.get("provider") or ""), str(cand.get("model") or payload.get("model") or ""),
                        data, time.time() - _t0)
            return code, data, ctype
        if code not in FAIL_CODES:
            break
        if i + 1 < len(chain):
            _note_chain(tried, str(cand.get("provider") or ""))
    _note_chain(tried, str(tried[-1].split(":")[0]) if tried else "")
    try:
        _last_code = int(last[0] or 0)
    except (TypeError, ValueError):
        _last_code = 0
    _note_failure(_last_code, _why(_last_code, last[1] if len(last) > 1 else b""),
                  str(tried[-1].split(":")[0]) if tried else "")
    return last


def probe(timeout: float = 45.0) -> dict[str, Any]:
    """One small call to the configured provider: "is my API key alive?".

    The operator's report was that the Agent does not respond using the API key, and there was no way to
    ask the key itself. This asks, and records the answer in the same state the portal reads - so a probe
    that fails and a turn that fails look the same to whoever is looking at the panel.

    Never raises, never returns key material.
    """
    try:
        cur = _load()
        chain = chain_for(cur)
        provider = str(cur.get("provider") or "")
        if not chain:
            why = "no API key is saved for this console (config/api.json is empty, or `keys` holds none)"
            return {"ok": False, "code": 401, "provider": provider, "model": str(cur.get("model") or ""),
                    "seconds": 0.0, "why": why}
        cand = chain[0]
        provider = str(cand.get("provider") or provider)
        model = str(cand.get("model") or cur.get("model") or "")
        started = time.time()
        ping = {"model": model or "gpt-4o-mini",
                "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
                "max_tokens": 8, "stream": False}
        if provider == "opencode":
            code, data, _ctype = _opencode_chat(model, ping, timeout)
        else:
            code, data, _ctype = _post(str(cand.get("url") or ""), str(cand.get("key") or ""),
                                       ping, cand.get("extra") or {}, timeout)
        took = round(time.time() - started, 1)
        if code < 400:
            _note_success(provider)
            clear_error()
            return {"ok": True, "code": int(code), "provider": provider, "model": model,
                    "seconds": took, "why": "the key answered"}
        why = _why(int(code), data)
        note_error(int(code), "probe: " + why)
        return {"ok": False, "code": int(code), "provider": provider, "model": model,
                "seconds": took, "why": why}
    except Exception as exc:  # noqa: BLE001 - a diagnostic that raises is not a diagnostic
        return {"ok": False, "code": 0, "provider": "", "model": "", "seconds": 0.0,
                "why": "%s: %s" % (type(exc).__name__, exc)}


def active() -> bool:
    """API usable *right now*: key saved, switched on, and not in a handoff."""
    st = public_status()
    return bool(st["enabled"] and not st["degraded"])


def note_error(code: int | Any, message: str = "") -> dict[str, Any]:
    """Record that an API turn failed; the console hands the next turns to local until cleared."""
    ensure_dirs()
    CONFIG.mkdir(parents=True, exist_ok=True)
    cur = _load()
    cur["degraded"] = True
    cur["last_code"] = int(code or 0)
    cur["last_error"] = " ".join(str(message or "").split())[:300]
    cur["last_at"] = time.time()
    cur["handoffs"] = int(cur.get("handoffs") or 0) + 1
    atomic_write_text(API_PATH, json.dumps(cur, indent=2))
    return public_status()


def clear_error() -> dict[str, Any]:
    """Operator re-activation: forget the handoff so the API is tried again."""
    ensure_dirs()
    CONFIG.mkdir(parents=True, exist_ok=True)
    cur = _load()
    cur["degraded"] = False
    cur["last_error"] = ""
    cur["last_code"] = 0
    atomic_write_text(API_PATH, json.dumps(cur, indent=2))
    return public_status()


def cooldown_active(seconds: float | None = None) -> bool:
    """True while a recent handoff should keep the console from calling the API again."""
    cur = _load()
    if not cur.get("degraded"):
        return False
    span = COOLDOWN_S if seconds is None else float(seconds)
    try:
        last = float(cur.get("last_at") or 0.0)
    except (TypeError, ValueError):
        last = 0.0
    return (time.time() - last) < span

