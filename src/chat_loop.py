from __future__ import annotations

import difflib
import json
import os
import re
from typing import Any

from align import load_align
from p0_hook import gate_output_window
from tools import ALIASES as TOOL_ALIASES, TOOLS_SCHEMA, dispatch, parse_fence_tool

SYSTEM = load_align()
URL_RE = re.compile(r"https://[^\s<>\]\)\"'`]+", re.I)
SEARCH_HINT = re.compile(
    r"\b(look\s*up|google|web_search|search for|find out|how many|how much|what is|what are|who is|where is|do \w+ have|does \w+ have)\b",
    re.I,
)

# --- arithmetic is answered here, never searched for -----------------------------
# Measured: "What is 17 * 23?" matched SEARCH_HINT on the words "what is", so host_prefetch ran
# web_search + web_fetch before the model and the 7B answered with a search-page number (53, 401).
# These helpers let the host do the sum itself, and run_tools_round redirect a web call that is
# really arithmetic.
MATH_HEAD = re.compile(
    r"^\s*(?:what(?:'s|\s+is)|whats|how\s+much\s+is|how\s+many\s+is|"
    r"calc(?:ulate)?|compute|solve|ev(?:al|aluate))\s*",
    re.I,
)
MATH_INSTR = re.compile(
    r"[\s,;.?!]*(?:(?:and\s+)?(?:please\s+)?(?:use|using|with|via|by)\s+(?:the\s+)?"
    r"(?:calc|calculator|math)(?:\s+tool)?|please|thanks|thank\s+you)[\s.?!]*$",
    re.I,
)
MATH_TAIL = re.compile(r"[\s=?!.:]+$")
MATH_CHARS = re.compile(r"^[\s\d.,()+\-*/%^×÷]+$")
MATH_OP = re.compile(r"[+\-*/%^×÷]")
MATH_WORDS = (
    (re.compile(r"\bmultiplied\s+by\b", re.I), "*"),
    (re.compile(r"\bdivided\s+by\b", re.I), "/"),
    (re.compile(r"\btimes\b", re.I), "*"),
    (re.compile(r"\bplus\b", re.I), "+"),
    (re.compile(r"\bminus\b", re.I), "-"),
)


def math_expr(text: str) -> str:
    """The arithmetic inside a plain question, in a form `calc` can evaluate.

    "What is 17 * 23?" -> "17 * 23"; "17 times 23" -> "17 * 23"; "1,000 + 500" -> "1000 + 500".
    Anything that is not purely arithmetic comes back as itself and fails math_only().
    """
    s = MATH_HEAD.sub("", str(text or ""))
    while True:
        smaller = MATH_INSTR.sub("", s)
        if smaller == s:
            break
        s = smaller
    s = MATH_TAIL.sub("", s)
    for rx, rep in MATH_WORDS:
        s = rx.sub(rep, s)
    s = s.replace("×", "*").replace("÷", "/").replace("^", "**")
    s = re.sub(r"(?<=\d),(?=\d)", "", s)
    return s.strip()


def math_only(text: str) -> bool:
    """True when the whole message is a bare arithmetic question and nothing else."""
    s = math_expr(text)
    if not MATH_CHARS.match(s):
        return False
    return bool(re.search(r"\d", s)) and bool(MATH_OP.search(s))


MATH_CANDIDATE = re.compile(r"\d[\d\s,.()]*[+\-*/%^×÷][\d\s,.()+\-*/%^×÷]*")
MATH_LIMB_NAMED = re.compile(
    r"\b(?:use|using|via|with|call|run|invoke)\b[^.]{0,40}?\b(?:calc|calculator|arithmetic|math)\b"
    r"[^.]{0,20}?\b(?:tool|limb)\b",
    re.I,
)


def math_expr_in(text: str) -> str:
    """The arithmetic sitting inside a longer sentence, in `calc`'s form; "" when there is none.

    "Use your calc limb to work out 47 * 89." -> "47 * 89". Needed because math_only() only accepts a
    message that is arithmetic and nothing else, and an operator who names the limb still writes a
    sentence around the expression.
    """
    for m in MATH_CANDIDATE.finditer(str(text or "")):
        cand = math_expr(m.group(0))
        if math_only(cand):
            return cand
    return ""


def math_limb_named(text: str) -> bool:
    """True when the operator asked for arithmetic by naming the limb ("use your calc limb")."""
    return bool(MATH_LIMB_NAMED.search(text or ""))


META_TOOLS = re.compile(
    r"(access the internet|search tools|hooked up|are your tools|can you search|tools working)",
    re.I,
)
STEWARD_HINT = re.compile(
    r"\b(github|hugging\s*face|\bhf\b|huggingface|lattice|webpage|webpages|clawhub|"
    r"chatagent|eternalhaven|password|passwords|drives?|whoami|this pc|"
    r"on (the|this) (pc|disk|usb)|find files|manage our|huggingface)\b",
    re.I,
)
PASS_HINT = re.compile(r"\b(password|passwords|credential|\.pass|gitea|winrm)\b", re.I)
FIND_HINT = re.compile(
    r"\b(find files|look for files|on this pc|list (the )?drives|search (the )?(pc|disk|usb))\b",
    re.I,
)
GH_HINT = re.compile(r"\bgithub\b", re.I)
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1] / "workspace"
HF_HINT = re.compile(r"\b(hugging\s*face|huggingface|\bhf\b)\b", re.I)
NOTE_HINT = re.compile(
    r"\b(notepad|look at (my |the )?notes|read (my |the )?notes|from (the |my )?notes|"
    r"saved notes|note pad)\b",
    re.I,
)
SELF_CHECK_HINT = re.compile(r"\b(self[-\s]?check|admin check|end of admin check)\b", re.I)
LIST_ASK = re.compile(
    r"\b(list|what('s| is| are) in|how many (entries|files|folders)|contents of)\b", re.I
)
FILE_HINT = re.compile(
    r"\b(create|make|write|save|put|drop)\b[^.?!]{0,40}\b(file|note|text file|txt|md|document|folder)\b"
    r"|\b(file|note)\b[^.?!]{0,30}\b(on|to|in)\b[^.?!]{0,20}\b(desktop|documents?|downloads?|home)\b"
    r"|\bsave (it|this|that)\b",
    re.I,
)
SKILL_HINT = re.compile(
    r"\b(/skill|skill_read|clawhub|skillhub|skill hub|lygoskillhub|invoke|summon|align with|"
    r"enable (the )?(skill|champion)|skills panel|champion)\b",
    re.I,
)


def extract_user_text(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for m in messages:
        if m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, list):
            for p in c:
                if isinstance(p, dict) and p.get("type") == "text":
                    parts.append(str(p.get("text") or ""))
    return "\n".join(parts)


def normalise_messages(obj: Any) -> list[dict[str, Any]]:
    """Accept every payload shape a caller might send and return a real messages[] list.

    `messages[]` is the contract; `prompt` is the documented shorthand. Anything else with no
    user text is NOT silently answered — the caller gets 400 no_input (see server._api_chat).
    """
    if not isinstance(obj, dict):
        return []
    msgs = obj.get("messages")
    out = [m for m in msgs if isinstance(m, dict)] if isinstance(msgs, list) else []
    if out:
        return out
    for key in ("prompt", "message", "input", "text"):
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            return [{"role": "user", "content": val}]
    return []


def user_text_of(messages: list[dict[str, Any]]) -> str:
    """Text of the newest user turn that actually carries text (else "")."""
    for m in reversed(list(messages or [])):
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        c = m.get("content")
        text = c if isinstance(c, str) else extract_user_text([m])
        if str(text or "").strip():
            return str(text)
    return ""


def has_image(messages: list[dict[str, Any]]) -> bool:
    for m in messages:
        c = m.get("content")
        if isinstance(c, list):
            for p in c:
                if isinstance(p, dict) and p.get("type") in {"image_url", "image"}:
                    return True
    return False


def _args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            v = json.loads(raw)
            return v if isinstance(v, dict) else {"value": raw}
        except json.JSONDecodeError:
            return {"value": raw}
    return {}


def extract_tool_calls(message: dict[str, Any], content: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function") or tc
        name = (fn.get("name") if isinstance(fn, dict) else None) or tc.get("name")
        if not name:
            continue
        args = _args(fn.get("arguments") if isinstance(fn, dict) else tc.get("arguments"))
        out.append({"name": str(name), "arguments": args})
    fence = parse_fence_tool(content or "")
    if fence:
        out.append(fence)
    for m in re.finditer(r"<(?P<tag>tool_call|tools)>\s*(\{.*?\})\s*</(?P=tag)>", content or "", re.S):
        # <tools> as well as <tool_call>: this model wraps its call in <tools> on 2 of 3 weather turns
        # (measured 2026-09-21), and nothing parsed that wrapper, so the limb it asked for never ran
        # and the operator was shown the raw JSON as the answer.
        try:
            obj = json.loads(m.group(2))
        except json.JSONDecodeError:
            continue
        name = obj.get("name") or obj.get("tool")
        if name:
            out.append({"name": str(name), "arguments": _args(obj.get("arguments") or obj.get("parameters") or obj)})
    # Some small models drop the wrapper entirely and send the call object as their whole reply
    # ({"name": "arxiv_search", "arguments": {...}}), so nothing above matches and the call is lost.
    # Accept it only when the *whole* trimmed reply is that object, so prose that merely quotes JSON
    # is left alone.
    stripped = (content or "").strip().strip("`").strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict):
            fn = obj.get("function")
            if isinstance(fn, dict):
                name = fn.get("name") or obj.get("name")
                args_raw = fn.get("arguments")
            else:
                name = obj.get("name") or obj.get("tool")
                args_raw = obj.get("arguments") or obj.get("parameters")
            if name:
                out.append({"name": str(name), "arguments": _args(args_raw)})
    # dedupe
    seen = set()
    uniq = []
    for c in out:
        key = (c["name"], json.dumps(c.get("arguments") or {}, sort_keys=True))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)
    return uniq


def extract_urls(text: str) -> list[str]:
    out: list[str] = []
    for m in URL_RE.finditer(text or ""):
        u = m.group(0).rstrip(".,;:)!?")
        if u not in out:
            out.append(u)
    return out[:3]


def _flat(v: Any, depth: int = 0) -> str:
    """One-line rendering of a tool result value (never a dict repr — a small model parrots those)."""
    if isinstance(v, (str, int, float, bool)) or v is None:
        return str(v)
    if isinstance(v, dict):
        return ", ".join(f"{k}={_flat(x, depth + 1)}" for k, x in list(v.items())[:8])
    if isinstance(v, (list, tuple)):
        return "; ".join(_flat(x, depth + 1) for x in list(v)[:6])
    return str(v)


def tool_prose(traces: list[dict[str, Any]], limit: int = 700) -> str:
    """Readable summary of the newest tool result — the answer of last resort when the model echoed
    its own tool call instead of narrating what came back."""
    if not traces:
        return ""
    t = traces[-1]
    name = str(t.get("name") or "tool")
    res = t.get("result")
    args = t.get("arguments") if isinstance(t.get("arguments"), dict) else {}
    if not isinstance(res, dict):
        return ""
    if res.get("ok") is False:
        why = res.get("error") or "unknown error"
        hint = res.get("hint")
        return f"{name} could not answer ({why})" + (f" — {hint}" if hint else "") + "."
    body = {k: v for k, v in res.items() if k not in {"class", "ok", "url"} and v not in (None, "", [], {})}
    text = f"{name}({_flat(args)}) -> {_flat(body)}"
    return text[:limit].rstrip() + ("…" if len(text) > limit else "")


def is_tool_call_echo(text: str, message: dict[str, Any] | None = None) -> bool:
    """True when the whole reply is a tool call (or a dump of one) with no prose around it."""
    raw = (text or "").strip()
    if not raw or not extract_tool_calls(message or {}, raw):
        return False
    body = re.sub(r"(?s)```.*?```", " ", raw)
    body = re.sub(r"(?s)<tool_call>.*?</tool_call>", " ", body)
    body = re.sub(r"(?s)\{.*\}", " ", body)
    return len(re.sub(r"\W+", "", body)) < 24


# --- the limb the operator asked for BY NAME ------------------------------------------------
# Measured 2026-09-18: a prompt like "Use the skill_list tool" matched SEARCH_HINT on "how many",
# so host_prefetch ran web_search+web_fetch and the model never got a turn in which it could call
# the named limb; "Use the wayback tool on <url>" was answered with a plain page fetch. When the
# operator names a limb, the host defers and the model gets the clean turn.
WEBISH = {
    "web_search", "web_fetch", "jina_fetch", "download_url", "wayback", "http_json",
    "page_thumbnail", "github_search", "hn_search", "arxiv_search", "web",
}
NAMED_TOOL_RE = re.compile(
    r"\b(?:use|using|via|with|call|run|invoke)\s+(?:the\s+)?([a-z_][a-z0-9_]{2,})\s+(?:tool|limb)\b",
    re.I,
)


def tool_names() -> set[str]:
    """Every limb name plus its aliases (read -> read_file)."""
    names = set(TOOL_ALIASES)
    for t in TOOLS_SCHEMA:
        fn = t.get("function") if isinstance(t, dict) else None
        if isinstance(fn, dict) and fn.get("name"):
            names.add(str(fn["name"]))
    return names


# A turn that asks for nothing: a greeting, a thanks, an acknowledgement — and nothing else.
# Measured on this box 2026-09-21 (qwen2.5-coder:7b, the console's own local schema of 24 limbs, the
# identity block and the volatile tail exactly as the console sends them): offered the limbs, the
# model answered a bare "hi" with a world_pulse call on 3 of 3 turns and "heloo?" on 2 of 3, so the
# console ran the limb and handed the operator a city-clock and weather report as the answer to their
# greeting. The same brain routes real asks correctly (17*23 -> calc 3/3, "weather in Tokyo" ->
# weather, "what time is it?" -> now), and the tail's wording is not the lever: with the order removed
# and the clock left alone, 4 of 8 turns still reached for `now`. What is wrong is offering 24 limbs
# on a turn that asks for nothing — so the fix belongs here, and it is deliberately strict: the WHOLE
# message has to be the greeting. "hi, what's the weather in Tokyo?" keeps every limb.
GREETING_RE = re.compile(
    r"^\s*(?:"
    r"hello|hel+o+|hi+|hey+|yo|sup|howdy|hullo|hiya|"
    r"good\s+(?:morning|afternoon|evening|night)|morning|evening|"
    r"thanks|thank\s+you|thanks\s+a\s+lot|thx|ty|cheers|"
    r"ok|okay|k|cool|nice|great|sweet|perfect|got\s+it|understood|"
    r"lol|lmao|haha|hehe|"
    r"(?:are\s+you\s+there|you\s+there|you\s+good|how\s+are\s+you(?:\s+doing)?)"
    r")[\s!.?,~:'’()\-\u2014]*$",
    re.I,
)

CONVERSATIONAL_MAX_CHARS = 60

# What a turn that asks nothing is told instead of being given the tail's clock to hand back.
# Measured on this box 2026-09-21 (qwen2.5-coder:7b, cuda), session holding a greeting the console had
# already answered with the clock -- the operator's own condition after "heloo?" -- 3 texts x 4 reps:
#
#     newest text   with the shipped tail                          with this directive
#     "hi"          1 of 4 answered (rest recited the clock)       4 of 4 answered
#     "thanks"      0 of 4 ("Understood. The current time is UTC  4 of 4 ("Hello! How can I assist
#                   2026-09-21T..." / "Let's proceed with your       you today?")
#                   instructions.")                                4 of 4
#     "you there?"  3 of 4                                         4 of 4
#
# 7 of 12 -> 12 of 12. Withholding the clock on such a turn as well measured the same 12 of 12, so this
# sentence is the fix and the withheld clock is a guard. It states the turn, and orders nothing: an
# imperative in this position is obeyed on any input, which is defect 30 (see tests/test_continuity.py).
CONVERSATIONAL_DIRECTIVE = (
    "The operator's newest message is a greeting or a word of thanks and asks nothing of you. Answer it "
    "as one person greeting another: one short, warm line, in your own words. Do not report status, do "
    "not recite the time or a readout, do not name a tool, do not restate these instructions."
)


def is_conversational(text: str) -> bool:
    """True when the operator's newest text asks for nothing (see the note above GREETING_RE)."""
    t = str(text or "").strip()
    if not t or len(t) > CONVERSATIONAL_MAX_CHARS:
        return False
    return bool(GREETING_RE.match(t))


def named_tool(text: str) -> str:
    """The limb the operator asked for by name ("use the weather tool"), else ""."""
    names = tool_names()
    for m in NAMED_TOOL_RE.finditer(text or ""):
        cand = m.group(1).lower()
        if cand in names:
            return TOOL_ALIASES.get(cand, cand)
    for m in re.finditer(r"`([a-z_][a-z0-9_]{2,})`", text or ""):
        cand = m.group(1).lower()
        if cand in names:
            return TOOL_ALIASES.get(cand, cand)
    return ""


# Limbs the HOST may run itself when the operator named one and the model would not call it.
# Read-only or trivially reversible only — a shell command, a python snippet, a file write or an
# edit is NEVER invented on the operator's behalf; those stay honest "I did not call it" answers.
AUTO_ZERO = {
    "list_dir", "whoami", "soul_read", "todo_list", "stack_health", "notepad_list",
    "sessions_list", "self_check", "kernel_status", "workspace_map", "memory_read", "identity_read",
}
AUTO_ONE = {
    "skill_read": "slug", "skill_enable": "slug", "skill_disable": "slug",
    "skillhub_list": "q", "clawhub_search": "q", "search_corpus": "q", "memory_recall": "q",
    "recall_history": "q", "session_list": "q", "session_open": "sid",
    "session_search": "q", "session_label": "title", "session_resume": "sid",
    "http_json": "url", "wayback": "url", "download_url": "url", "jina_fetch": "url",
    "web_fetch": "url", "page_thumbnail": "url", "geocode": "place", "weather": "place",
}


# Limb the host may run for the operator, but ONLY with arguments the operator's own message
# carries — never invented: a write needs the text AND the filename, a shell call needs a single
# read-only command the operator typed, a python call needs a print(...) they typed.
AUTO_OPERATOR = {"write_file", "shell", "python_exec"}
SHELL_READONLY = {
    "echo", "dir", "ls", "pwd", "whoami", "hostname", "ver", "date", "time", "type",
    "where", "find", "tasklist", "netstat", "ipconfig", "systeminfo",
}
SHELL_META = re.compile(r"[|&;<>$`(){}!\n\r]")


def _operator_args(name: str, text: str) -> dict[str, Any] | None:
    """Args taken only from the operator's words. None = not enough there; answer honestly."""
    t = str(text or "")
    if name == "write_file":
        body = re.search(r"['\"]([^'\"]{1,400})['\"]", t)
        dest = re.search(r"([A-Za-z0-9_.\-/\\]+\.(?:txt|md|json|html|csv|log|py|tsv))", t)
        if body and dest:
            return {"path": dest.group(1), "content": body.group(1)}
        return None
    if name == "shell":
        m = re.search(r"\b(?:run|execute)\s*:?\s+(.+)$", t.strip(), re.I)
        if not m:
            return None
        cmd = m.group(1).strip().strip('"').strip("'").strip()
        if not cmd or SHELL_META.search(cmd):
            return None
        if cmd.split()[0].lower().rstrip(".") not in SHELL_READONLY:
            return None
        return {"cmd": cmd}
    if name == "python_exec":
        m = re.search(r"\bprint\s*\(([^()]{1,200})\)", t)
        if m:
            return {"code": "print(" + m.group(1).strip() + ")"}
        m = re.search(r"\bprint\s+([0-9A-Za-z_+\-*/%. ]{1,120})", t)
        if m:
            expr = m.group(1).strip().rstrip(".")
            if expr:
                return {"code": "print(" + expr + ")"}
        return None
    return None


def auto_limb(name: str, text: str) -> dict[str, Any] | None:
    """Args to run `name` on the host when the model would not call it. None = do not run it."""
    if name in AUTO_OPERATOR:
        return _operator_args(name, text)
    if name in AUTO_ZERO:
        args: dict[str, Any] = {}
        if name == "list_dir":
            m = re.search(r"([A-Za-z]:[\\/][^\s\"']{2,}|/[^\s\"']{3,})", text or "")
            hit = re.search(r"([A-Za-z]:[\\/][^\n]{2,})", text or "")
            if hit:
                tail = re.split(r"\s+(?:and|then|please|tool)\b", hit.group(1).strip().strip('"').strip("'"), 1)[0]
                args = {"path": tail.rstrip(" .,;:!?")}
            else:
                hit = re.search(r"(/[^\s]{3,})", text or "")
                args = {"path": hit.group(1).rstrip(" .,;:!?")} if hit else {}
        return args
    key = AUTO_ONE.get(name)
    if not key:
        return None
    if key == "url":
        urls = extract_urls(text or "")
        return {"url": urls[0]} if urls else None
    if key == "place":
        m = re.search(r"\b(?:in|for|at)\s+([A-Z][A-Za-z .'\-]{2,40})", text or "")
        return {"place": m.group(1).strip(" .,")} if m else None
    if key == "q":
        q = re.sub(r"^\s*use\s+(?:the\s+)?[a-z_]+\s+(?:tool|limb)\b", " ", text or "", flags=re.I)
        q = re.sub(r"\b(search for|search|browse for|browse|look for|for|about|to)\b", " ", q, flags=re.I)
        q = re.sub(r"[^A-Za-z0-9_.\- ]+", " ", q).strip()
        return {"q": q[:120]} if q else None
    m = re.search(r"\bslug\s+([A-Za-z0-9_.\-]{2,})", text or "", re.I)
    if not m:
        m = re.search(r"\b([a-z0-9]+-[a-z0-9][a-z0-9\-]{2,})\b", text or "")
    return {key: m.group(1)} if m else None


def tool_card(name: str) -> str:
    """One-limb instruction with the exact schema — the retry when a named limb was not called."""
    desc, props = "", {}
    for t in TOOLS_SCHEMA:
        fn = t.get("function") if isinstance(t, dict) else None
        if isinstance(fn, dict) and fn.get("name") == name:
            desc = str(fn.get("description") or "")
            props = ((fn.get("parameters") or {}).get("properties") or {})
    args = ", ".join('"%s": <%s>' % (k, (v or {}).get("type", "string")) for k, v in props.items()) or "(no arguments)"
    return (
        "You were asked for the " + name + " limb and you did not call it. Reply with exactly ONE "
        'tool call and nothing else:\n{"name": "' + name + '", "arguments": {' + args + "}}\n"
        "Limb: " + desc + "\n"
        "Do not answer in prose. Do not explain. Do not invent a result. Emit the call."
    )


BS = chr(92)  # a literal backslash, built at runtime so no escape layer can mangle it
QUOTES = chr(34) + chr(39)
BADCHARS = chr(34) + chr(39) + "<>|"

# A file path in the operator's message is DATA, not intent. Measured 2026-09-19: the steward sent
# "check this photo <path>" where the path was drive I:, folder "E Drive", YOUTUBE LYGO VIDEOS,
# Pictures, cc988550....png. STEWARD_HINT matched the word "Drive" inside that path, so host_prefetch
# ran steward_map and injected a drives/policy recital BEFORE the model was called - he asked about a
# picture and got a read_roots dump, then the model repeated it. Same trigger hit his earlier "find a
# image on my pc in ..." turn. Mask paths out of every hint test below; the untouched words survive in
# `raw` and the image branch reads the real path back out of it.
PATH_TOKEN = re.compile(
    "[A-Za-z]:" + "[" + BS + BS + "/]" + "[^" + BADCHARS + "]*"
    + "|" + BS + BS + "[^" + BADCHARS + "]+",
    re.I,
)
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff")
LOOK_HINT = re.compile("(check|look|see|view|describe|inspect|analyse|analyze|read|photo|image|picture|what)", re.I)
# Generate a NEW picture (not inspect one). Gemma 4 / aligned chat GGUFs lecture and skip
# image_generate; the host draws instead so the local renderer is what decides.
DRAW_HINT = re.compile(
    r"\b(?:draw|generate|create|make|render|paint|sketch)\b.{0,160}?\b(?:photo|photograph|picture|image|illustration|artwork|png)\b"
    r"|\b(?:photo|photograph|picture|image)\s+of\b"
    r"|\bimage_generate\b"
    r"|\b(?:make|create)\s+me\s+a\s+(?:pic(?:ture)?|photo|image)\b",
    re.I,
)
INSPECT_PICTURE = re.compile(
    r"\b(?:what(?:'s| is) in|describe|look at|check this|inspect this|see this)\b.{0,60}\b(?:photo|picture|image)\b",
    re.I,
)
_MINOR_SUBJECT = re.compile(
    r"\b(child|children|minor|minors|underage|preteen|pre-teen|loli|shota|young girl|young boy)\b",
    re.I,
)
_SAFETY_LECTURE = re.compile(
    r"(i cannot fulfill|i(?:'m| am) (?:not |unable to )?(?:able to )?(?:create|generate|draw|fulfill)|"
    r"safety guidelines prohibit|sexually suggestive|"
    r"programmed to be a helpful and harmless)",
    re.I,
)


def picture_draw_prompt(text: str) -> str | None:
    """The scene to hand image_generate, or None when this is not a draw request."""
    raw = (text or "").strip()
    if not raw or INSPECT_PICTURE.search(raw):
        return None
    if not DRAW_HINT.search(raw):
        return None
    p = re.sub(
        r"^(?:please\s+)?(?:using\s+(?:your|the)\s+\w+\s+tool\s+)?"
        r"(?:please\s+)?(?:create|make|draw|generate|render|paint|sketch)\s+"
        r"(?:me\s+)?(?:a|an|the)?\s*"
        r"(?:photo|photograph|picture|image|illustration|artwork)?\s*(?:of\s+)?",
        "",
        raw,
        count=1,
        flags=re.I,
    ).strip(" :,-")
    return p or raw


def host_picture_draw(user_text: str) -> list[dict[str, Any]]:
    """Run image_generate on the host when the operator asked for a new picture.

    Aligned chat GGUFs (Gemma 4 especially) refuse costume/adult-character scenes in prose and never
    call the limb. The local SD/Qwen renderer has no such lecture. CSAM / minor subjects stay refused.
    """
    prompt = picture_draw_prompt(user_text)
    if not prompt:
        return []
    if _MINOR_SUBJECT.search(prompt):
        return [{
            "name": "image_generate",
            "arguments": {"prompt": prompt[:240]},
            "result": {"ok": False, "error": "refused_minor_subject"},
            "host": True,
        }]
    result = dispatch("image_generate", {"prompt": prompt})
    return [{
        "name": "image_generate",
        "arguments": {"prompt": prompt},
        "result": result,
        "host": True,
    }]


# --- songs: the operator asks for music, so the console makes music -----------------
# Measured 2026-09-25 on this kit's own local brain: asked to "write me a song about the lattice",
# a small model answers with a poem about writing a song and never calls the limb - the same shape
# that made the picture path a host path. The division of labour here is honest and specific: the
# console's own engine writes the LYRICS (a text job, and the engine is already up serving this
# turn), and the local music engine does the part only it can do - sing them. If no lyrics can be
# written, the limb is still called, so the operator gets the engine's own `lyrics_required` instead
# of a silent prose answer.
SONG_HINT = re.compile(
    r"\b(?:write|make|create|generate|compose|produce|record|sing)\b"
    r".{0,90}?\b(?:song|track|tune|anthem|jingle|ballad|rap|lyrics|melody|beat)\b"
    r"|\bmusic_generate\b"
    r"|\b(?:song|track|tune)\s+(?:about|for|on)\b",
    re.I,
)
# A search for somebody ELSE's music is not a request to make one.
SONG_NOT_OURS = re.compile(
    r"\b(?:playlist|spotify|youtube|download|find me|search for|look up|what(?:'s| is) the name|"
    r"who sings|who wrote|lyrics to|sheet music|chords for|tabs? for)\b",
    re.I,
)
# The genre words a person says, in the engine's own tag vocabulary. First match wins; the head of
# the list is the order a request usually means them in.
_SONG_STYLES: tuple[tuple[str, str], ...] = (
    ("hip hop", "gritty boom bap hip hop, deep sub bass, crisp drums, confident male rap vocal"),
    ("hiphop", "gritty boom bap hip hop, deep sub bass, crisp drums, confident male rap vocal"),
    ("rap", "gritty boom bap hip hop, deep sub bass, crisp drums, confident male rap vocal"),
    ("country", "warm country, acoustic guitar, pedal steel, steady drums, honest male vocal"),
    ("metal", "heavy metal, distorted guitars, double kick drums, powerful male vocal"),
    ("punk", "fast punk rock, distorted power chords, driving drums, raw shouted vocal"),
    ("rock", "anthemic rock, electric guitars, live drums, raspy male vocal"),
    ("blues", "slow blues, slide guitar, walking bass, smoky female vocal"),
    ("jazz", "smoky jazz, upright bass, brushed drums, muted trumpet, intimate female vocal"),
    ("folk", "warm acoustic folk, fingerpicked guitar, soft harmonies, gentle female vocal"),
    ("gospel", "uplifting gospel, choir harmonies, organ, hand claps, powerful female lead vocal"),
    ("reggae", "laid back reggae, offbeat guitar skank, deep bass, relaxed male vocal"),
    ("orchestral", "cinematic orchestral, strings, timpani, wordless choir, no drums"),
    ("classical", "chamber classical, piano and strings, gentle dynamics, instrumental"),
    ("ambient", "ambient electronic, pads, soft percussion, breathy wordless vocal"),
    ("lo-fi", "lo-fi chill, dusty drums, warm vinyl noise, mellow jazzy chords, soft female vocal"),
    ("lofi", "lo-fi chill, dusty drums, warm vinyl noise, mellow jazzy chords, soft female vocal"),
    ("techno", "driving techno, four on the floor kick, acid bassline, hypnotic synth stabs"),
    ("house", "uplifting house, four on the floor, warm bassline, soulful female vocal"),
    ("edm", "festival electronic, big supersaw lead, punchy kick, soaring female vocal"),
    ("electronic", "bright electronic pop, arpeggiated synths, steady beat, airy female vocal"),
    ("synthwave", "retro synthwave, analog synth pads, gated drums, breathy female vocal"),
    ("pop", "bright uplifting pop, airy female vocal, electronic drums, catchy synth hook"),
    ("dance", "dance pop, four on the floor, bright synths, energetic female vocal"),
    ("ballad", "slow piano ballad, strings, intimate expressive female vocal"),
)
_SONG_HEAD = re.compile(
    r"^\s*(?:please\s+)?(?:can\s+you\s+|could\s+you\s+|would\s+you\s+|i\s+want\s+you\s+to\s+)?"
    r"(?:write|make|create|generate|compose|produce|record|sing)\s+(?:me\s+|us\s+)?"
    r"(?:a|an|the|another|one\s+more)?\s*(?:new\s+|short\s+|full\s+|little\s+|nice\s+)?"
    r"(?:song|track|tune|anthem|jingle|ballad|rap)\s*(?:about|on|for|describing|that\s+says)?\s*",
    re.I,
)
_LYRIC_SYSTEM = (
    "You write song lyrics for a local music engine. Reply with ONLY the lyrics: no title, no "
    "commentary, no explanation, no notes. Put these section tags on their own lines: [verse], "
    "[chorus], [bridge]. Write two verses and a chorus that repeats after the second verse. Four "
    "short lines per section, concrete images, singable lines, no brackets inside a line."
)


def song_style_for(text: str) -> str:
    """The engine's style tags for the genre words in the ask, else a bright pop default."""
    low = (text or "").lower()
    for word, style in _SONG_STYLES:
        if re.search(r"\b" + re.escape(word) + r"\b", low):
            return style
    return "inspiring uplifting pop, bright synths, steady beat, airy female vocal"


def song_theme(text: str) -> str:
    """The subject of the song, with the request's own words stripped off."""
    raw = re.sub(r"\s+", " ", (text or "").strip())
    theme = _SONG_HEAD.sub("", raw, count=1).strip(" :,-.")
    theme = re.sub(r"\b(?:please|thanks|thank you)\b", "", theme, flags=re.I).strip(" :,-.")
    return (theme or raw)[:400]


def song_request(text: str) -> dict[str, Any] | None:
    """(style, theme, lyrics the operator already gave) for a song ask, else None."""
    raw = (text or "").strip()
    if not raw or SONG_NOT_OURS.search(raw):
        return None
    if not SONG_HINT.search(raw):
        return None
    given = ""
    # Lyrics the operator wrote themselves arrive as tagged sections or as several short lines.
    if re.search(r"^\s*\[(?:verse|chorus|bridge|intro|outro)\]", raw, re.I | re.M):
        given = raw
    else:
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        body = [ln for ln in lines[1:] if not ln.lower().startswith(("write", "make", "compose"))]
        if len(lines) >= 4 and len(body) >= 3:
            given = "\n".join(body)
    return {"style": song_style_for(raw), "theme": song_theme(raw), "lyrics": given}


def write_lyrics(theme: str, style: str, timeout: float = 300.0) -> str:
    """Lyrics from this console's own engine. "" when it could not produce any - never invented.

    This is the one job the console hands to its own brain inside a host path: writing words. It is
    asked for lyrics and nothing else, at a temperature that suits language rather than tool calls,
    and a reply that is empty (or that is prose ABOUT the song) is treated as no lyrics at all.
    """
    import json as _json
    import urllib.error
    import urllib.request

    from paths import LLAMA_KEY_PATH, LLAMA_PORT

    key = ""
    try:
        key = LLAMA_KEY_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        key = ""
    payload = {
        "model": "",
        "messages": [
            {"role": "system", "content": _LYRIC_SYSTEM},
            {"role": "user", "content": f"Subject: {theme}\nMusic style: {style}"},
        ],
        "max_tokens": 700,
        "temperature": 0.9,
        "top_p": 0.95,
        "stream": False,
    }
    req = urllib.request.Request(
        f"http://127.0.0.1:{int(os.environ.get('LYGO_LLAMA_PORT') or LLAMA_PORT)}/v1/chat/completions",
        data=_json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = _json.loads(resp.read().decode("utf-8", "replace") or "{}")
    except Exception:  # noqa: BLE001 - no lyrics is an answer; the limb reports it by name
        return ""
    try:
        text = str(body["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        return ""
    if not text or len(text) < 40:
        return ""
    if not re.search(r"\[(?:verse|chorus|bridge)\s*\]", text, re.I):
        return ""
    return text


def host_song_generate(user_text: str) -> list[dict[str, Any]]:
    """Make the song on the host: the console's engine writes the words, the music engine sings them."""
    req = song_request(user_text)
    if not req:
        return []
    lyrics = str(req.get("lyrics") or "")
    if not lyrics:
        lyrics = write_lyrics(str(req["theme"]), str(req["style"]))
    result = dispatch("music_generate", {
        "style": req["style"],
        "lyrics": lyrics,
        "seconds": 60,
    })
    return [{
        "name": "music_generate",
        "arguments": {"style": req["style"], "lyrics": lyrics},
        "result": result,
        "host": True,
        "theme": req["theme"],
    }]


def mask_paths(text: str) -> str:
    """The text with file paths blanked, so a filename can never select a host limb."""
    return PATH_TOKEN.sub(" ", text or "")


def paths_in(text: str) -> list[str]:
    """Absolute Windows paths in the text, in order, stripped of quotes and trailing punctuation."""
    out: list[str] = []
    for m in PATH_TOKEN.findall(text or ""):
        p = m.strip().strip(QUOTES).rstrip(" .,;:)")
        if p and p not in out:
            out.append(p)
    return out


ATTACH_MARK = re.compile(r"attached file|its contents follow|read_file limb", re.I)
# A path alone on its line. The composer writes it that way because this kit's own folder has a space in
# it ("<EDRIVE>\..."), which every inline path pattern truncates.
PATH_LINE = re.compile(r"^\s*\"?([A-Za-z]:[\\/][^\n]*?)\"?\s*$")


def attached_files(text: str) -> list[str]:
    """The files the composer attached to this message, out of the operator's own words.

    The portal sends "<absolute path> - open it with the read_file limb before you answer" for anything
    it could not inline, and a small model often never issues that call: measured 2026-09-19 the console
    answered "I was asked for the read_file limb and did not issue the call" to a message whose own
    attachment line named the file. The path is right there in the operator's message, so the host reads
    it and the answer stops depending on the model remembering to ask. Pictures are left to the image
    path (they need the projector, not read_file).
    """
    out: list[str] = []
    for line in (text or "").splitlines():
        m = PATH_LINE.match(line)
        if not m:
            continue
        p = m.group(1).strip().strip(QUOTES).rstrip(" .,;:)")
        if p and not p.lower().endswith(IMAGE_EXT) and p not in out:
            out.append(p)
    return out


def image_paths_in(text: str) -> list[str]:
    """The image files among the paths in the text."""
    return [p for p in paths_in(text) if p.lower().endswith(IMAGE_EXT)]


def host_file_chain(user_text: str) -> list[dict[str, Any]]:
    """D3: read file A then write file B from A's contents, on the host, in one turn.

    Small models call save_note / file_intent once and never do the second step. The operator's
    words already named both files; the console completes the chain and leaves a receipt.
    """
    traces: list[dict[str, Any]] = []
    text = user_text or ""
    if not re.search(r"\bthen\b", text, re.I):
        return traces
    names = re.findall(r"([A-Za-z0-9._-]+\.(?:txt|md|py|json))", text)
    uniq: list[str] = []
    for n in names:
        if n not in uniq:
            uniq.append(n)
    if len(uniq) < 2:
        return traces
    src_name, dest_name = uniq[0], uniq[1]
    src = None
    for cand in (WORKSPACE / "notes" / src_name, WORKSPACE / src_name):
        if cand.is_file():
            src = cand
            break
    if src is None:
        found = [p for p in WORKSPACE.rglob(src_name) if p.is_file()]
        src = found[0] if found else None
    if src is None:
        # Measured (gauntlet T12, 2026-09-22): "Create two files ... mod_a.py and run_a.py ... Then
        # run run_a.py" names two .py files, so this chain fired, found no mod_a.py and returned a
        # read_file ok=false readout. The host readout then replaced the create request: the coder
        # answered "UNKNOWN (SHADOW) - no mod_a.py file found" and wrote neither file. A chain whose
        # first step has nothing to read is not a chain - it is a create request. Stay silent and let
        # the rest of host_prefetch handle the turn.
        return []
    body = src.read_text(encoding="utf-8", errors="replace")
    traces.append(
        {
            "name": "read_file",
            "arguments": {"path": str(src)},
            "result": {"ok": True, "path": str(src), "text": body[:8000]},
            "host": True,
        }
    )
    extra_m = re.search(r"followed by the word\s+([A-Za-z0-9_-]+)", text, re.I)
    out_body = body
    if extra_m:
        word = extra_m.group(1)
        out_body = body.rstrip() + (" " if body.strip() else "") + word
        if not out_body.endswith("\n"):
            out_body += "\n"
    dest = src.parent / dest_name
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(out_body, encoding="utf-8")
        verified = dest.read_text(encoding="utf-8") == out_body
        traces.append(
            {
                "name": "save_note",
                "arguments": {"name": dest_name, "where": str(dest.parent)},
                "result": {
                    "ok": True,
                    "path": str(dest),
                    "bytes": dest.stat().st_size,
                    "verified": verified,
                    "chain": True,
                },
                "host": True,
            }
        )
    except OSError as exc:
        traces.append(
            {
                "name": "save_note",
                "arguments": {"name": dest_name},
                "result": {"ok": False, "error": f"{type(exc).__name__}"},
                "host": True,
            }
        )
    return traces


def host_prefetch(user_text: str) -> list[dict[str, Any]]:
    """3B models talk about tools instead of calling them. Host runs URL/search/map first."""
    traces: list[dict[str, Any]] = []
    text = user_text or ""
    named = named_tool(text)
    raw = text                  # the operator's own words, for the limbs that need the real path
    text = mask_paths(text)     # every hint test below now sees intent, not filenames
    try:
        from skills_mod import match_invoked

        invoked = match_invoked(text)
    except Exception:
        invoked = []
    if SKILL_HINT.search(text):
        traces.append({"name": "skill_list", "arguments": {}, "result": dispatch("skill_list", {}), "host": True})
    for slug in invoked:
        traces.append(
            {
                "name": "skill_read",
                "arguments": {"slug": slug},
                "result": dispatch("skill_read", {"slug": slug}),
                "host": True,
            }
        )
    if re.search(r"\b(skillhub|skill hub|lygoskillhub)\b", text, re.I):
        q = re.sub(r"\s+", " ", text).strip()[:120]
        traces.append(
            {
                "name": "skillhub_list",
                "arguments": {"q": q, "channel": "all"},
                "result": dispatch("skillhub_list", {"q": q, "channel": "all"}),
                "host": True,
            }
        )
    if re.search(r"\bclawhub\b", text, re.I) and not invoked:
        q = re.sub(r"\s+", " ", text).strip()[:120]
        traces.append(
            {
                "name": "clawhub_search",
                "arguments": {"q": q},
                "result": dispatch("clawhub_search", {"q": q}),
                "host": True,
            }
        )
    if NOTE_HINT.search(text):
        traces.append(
            {
                "name": "notepad_list",
                "arguments": {},
                "result": dispatch("notepad_list", {}),
                "host": True,
            }
        )
    if SELF_CHECK_HINT.search(text):
        traces.append(
            {
                "name": "self_check",
                "arguments": {},
                "result": dispatch("self_check", {}),
                "host": True,
            }
        )
    if STEWARD_HINT.search(text):
        traces.append(
            {
                "name": "steward_map",
                "arguments": {},
                "result": dispatch("steward_map", {}),
                "host": True,
            }
        )
    if PASS_HINT.search(text):
        traces.append(
            {
                "name": "credential_where",
                "arguments": {"q": ""},
                "result": dispatch("credential_where", {"q": ""}),
                "host": True,
            }
        )
    if FIND_HINT.search(text):
        traces.append(
            {
                "name": "list_dir",
                "arguments": {"path": ""},
                "result": dispatch("list_dir", {"path": ""}),
                "host": True,
            }
        )
    if ATTACH_MARK.search(raw):
        # The composer named the file in this very message. Read it here rather than asking the model to
        # issue the call, and do not go to the web for an answer that is sitting in the attachment.
        read_any = False
        for path in attached_files(raw):
            result = dispatch("read_file", {"path": path})
            traces.append({"name": "read_file", "arguments": {"path": path}, "result": result, "host": True})
            read_any = True
        if read_any:
            return traces
    urls = extract_urls(text) if (not named or named in WEBISH) else []
    for url in urls:
        result = dispatch("web_fetch", {"url": url})
        traces.append({"name": "web_fetch", "arguments": {"url": url}, "result": result, "host": True})
    if urls:
        return traces
    pics = image_paths_in(raw)
    if pics:
        # "check this photo <path>": the picture IS the request. Inspect the file on the host, and when
        # the operator asked to look at it (or sent nothing but the path) hand the bytes to the local
        # vision model, so the answer is grounded in the picture instead of in a filename that looks
        # like a policy question.
        for pic in pics[:2]:
            info = dispatch("image_info", {"path": pic})
            traces.append({"name": "image_info", "arguments": {"path": pic}, "result": info, "host": True})
            if (info or {}).get("ok") and (LOOK_HINT.search(text) or len(text.strip()) < 12):
                traces.append(
                    {
                        "name": "image_see",
                        "arguments": {"path": pic},
                        "result": dispatch("image_see", {"path": pic}),
                        "host": True,
                    }
                )
        return traces
    if GH_HINT.search(text) and not any(t.get("name") == "steward_map" for t in traces):
        traces.append(
            {
                "name": "github_search",
                "arguments": {"q": "lygo"},
                "result": dispatch("github_search", {"q": "lygo"}),
                "host": True,
            }
        )
    if HF_HINT.search(text) and not any(t.get("name") == "steward_map" for t in traces):
        traces.append(
            {
                "name": "web_fetch",
                "arguments": {"url": "https://huggingface.co/DeepSeekOracle"},
                "result": dispatch("web_fetch", {"url": "https://huggingface.co/DeepSeekOracle"}),
                "host": True,
            }
        )
    chained = host_file_chain(raw)
    if any(t.get("name") == "save_note" for t in chained):
        # The chain owns the destination name for this turn (gauntlet T6): stop before the generic
        # FILE_HINT path below, which reads "contents are what you read followed by the word CHECKED"
        # as the file body and would overwrite the chained text with the instruction.
        traces.extend(chained)
        return traces
    if FILE_HINT.search(text) and not any(t.get("name") == "file_intent" for t in traces):
        _spec = host_write_request(raw)
        if _spec and not any(t.get("name") == "save_note" for t in traces):
            try:
                from limbs import extra as _extra

                _got = _extra("save_note", _spec)
                if isinstance(_got, dict) and _got.get("ok"):
                    traces.append({"name": "save_note", "arguments": {"where": _spec["where"]},
                                   "result": _got, "host": True})
            except Exception as _exc:  # a failed host write must not cost the turn
                traces.append({"name": "save_note", "arguments": {"where": _spec.get("where")},
                               "result": {"ok": False, "error": f"{type(_exc).__name__}"}, "host": True})
        import user_paths

        traces.append(
            {
                "name": "file_intent",
                "arguments": {"text": text[:120]},
                "result": {
                    "operator_folders": user_paths.folder_map(),
                    "rule": "To write a file call save_note (where=desktop/documents/downloads/home/"
                            "workspace, or an absolute folder; consent=true outside the workspace). It "
                            "returns the absolute path it really wrote. Do not describe a file as created "
                            "unless that result says so.",
                },
                "host": True,
            }
        )
    if LIST_ASK.search(text) and not any(t.get("name") in ("list_dir", "workspace_map") for t in traces):
        # Measured (gauntlet T3, both runs): asked to list its own workspace the on-box brain reached for
        # web_search and produced a listing it never looked at. A listing request needs no judgement either.
        import user_paths as _up

        _target = WORKSPACE
        for _key in ("desktop", "documents", "downloads", "home"):
            if _key in text.lower():
                _target = _up.known_folder(_key) or WORKSPACE
                break
        _got = dispatch("list_dir", {"path": str(_target)})
        if (_got or {}).get("ok"):
            traces.append({"name": "list_dir", "arguments": {"path": str(_target)}, "result": _got, "host": True})
    if math_limb_named(text) and not any(t.get("name") == "calc" for t in traces):
        # Measured (gauntlet T1, 2026-09-22): "Use your calc limb to work out 47 * 89. Tell me only
        # the number." is arithmetic inside a sentence, so math_only() said no, no limb ran, and the
        # on-box coder answered 4163 (wrong). An operator who names the arithmetic limb and states the
        # expression has left nothing to judge - run it, exactly as the bare-arithmetic case below.
        expr = math_expr_in(text)
        if expr:
            result = dispatch("calc", {"expr": expr})
            if (result or {}).get("ok"):
                traces.append({"name": "calc", "arguments": {"expr": expr}, "result": result, "host": True})
                return traces
    if math_only(text) and named in ("", "calc"):
        # Bare arithmetic: answer it on the host instead of searching the web for the numbers.
        expr = math_expr(text)
        result = dispatch("calc", {"expr": expr})
        if (result or {}).get("ok"):
            traces.append({"name": "calc", "arguments": {"expr": expr}, "result": result, "host": True})
        return traces

    if not any(t.get("name") == "image_generate" for t in traces):
        drawn = host_picture_draw(raw)
        if drawn:
            traces.extend(drawn)
            return traces

    if not any(t.get("name") == "music_generate" for t in traces):
        sung = host_song_generate(raw)
        if sung:
            traces.extend(sung)
            return traces

    if META_TOOLS.search(text) and not SEARCH_HINT.search(text) and not STEWARD_HINT.search(text):
        return traces
    if SEARCH_HINT.search(text) and not named and not GH_HINT.search(text) and not HF_HINT.search(text):
        q = re.sub(r"\s+", " ", text).strip()[:220]
        result = dispatch("web_search", {"q": q})
        hits = (result or {}).get("hits") or []
        if hits:
            traces.append({"name": "web_search", "arguments": {"q": q}, "result": result, "host": True})
            top = hits[0].get("url") or ""
            if top.startswith("https://"):
                traces.append(
                    {
                        "name": "web_fetch",
                        "arguments": {"url": top},
                        "result": dispatch("web_fetch", {"url": top}),
                        "host": True,
                    }
                )
    return traces


# Limbs that exist to bring text back to the model. Their body must survive the slim trace.
READOUT_LIMBS = {
    "read_file", "read_text", "search_corpus", "skill_read", "list_dir", "notepad_read",
    "session_read", "memory_read", "identity_read", "whoami", "http_json", "jina_fetch",
}


def _compact_trace(t: dict[str, Any]) -> dict[str, Any]:
    res = t.get("result") if isinstance(t.get("result"), dict) else {}
    name = t.get("name")
    if name == "calc":
        return {
            "name": name,
            "expr": (t.get("arguments") or {}).get("expr"),
            "value": res.get("value"),
            "error": res.get("error"),
        }

    if name == "self_check":
        return {
            "name": name,
            "verdict": res.get("verdict"),
            "github": res.get("github"),
            "huggingface": res.get("huggingface"),
            "lattice": res.get("lattice"),
            "chatagent_exists": res.get("chatagent_exists"),
            "chatagent_sample": res.get("chatagent_sample"),
            "n_live_mounts": res.get("n_live_mounts"),
            "skills": res.get("skills"),
        }
    if name == "steward_map":
        return {
            "name": name,
            "role": res.get("role"),
            "github": res.get("github_org"),
            "hf": res.get("hf_org"),
            "sites": (res.get("sites") or res.get("lattice") or [])[:8],
            "drives": res.get("drives"),
            "rule": "never github.com/user/repo or lattice.example.com",
        }
    if name == "web_fetch":
        res = res if isinstance(res, dict) else {}
        return {
            "name": name,
            "ok": res.get("ok"),
            "url": res.get("url") or (t.get("arguments") or {}).get("url"),
            "error": res.get("error"),
            "hint": res.get("hint"),
            "marker": res.get("marker"),
            # An X post read carries the post body in ~600 chars; give it a little more room.
            "text": str(res.get("text") or "")[: 900 if res.get("kind") == "x_post" else 600],
        }
    if name == "github_search":
        hits = res.get("hits") or []
        return {"name": name, "query": res.get("query"), "hits": hits[:6]}
    if name in READOUT_LIMBS:
        # A limb whose whole job is to bring text back must carry that text into the prompt. The slim
        # branch below keeps only name/ok/path, and a host read of the operator's own attached file then
        # reached the model as {"name": "read_file", "ok": true, "path": ...} with none of its contents -
        # so it answered "LUMINA-77" for a file that says OMEGA-441 (measured 2026-09-19). Same class as
        # the calc value that went missing: a readout without its payload is a readout the model invents.
        slim = {"name": name, "ok": res.get("ok", True)}
        for k in ("role", "n", "path", "error", "hint", "marker", "value", "verdict"):
            if res.get(k) is not None:
                slim[k] = res.get(k)
        body = res.get("text")
        if body in (None, ""):
            body = res.get("content")
        if isinstance(body, str) and body:
            slim["text"] = body[:2000]
        for k in ("entries", "hits", "matches", "skills", "lines"):
            v = res.get(k)
            if v:
                slim[k] = v[:20]
        return slim
    slim = {"name": name, "ok": res.get("ok", True)}
    for k in ("role", "n", "path", "error", "github"):
        if res.get(k) is not None:
            slim[k] = res.get(k)
    return slim


def prefetch_message(traces: list[dict[str, Any]]) -> str:
    """What the model sees when the host already ran the limbs for this turn.

    Plain instruction, not a template order: an over-prescriptive prompt here is what made every
    answer look like the same canned readout.
    """
    if not traces:
        return ""
    compact = [_compact_trace(t) for t in traces]
    return (
        "HOST READOUT (RESOURCE, not CANON) — the host already ran these limbs for this turn, "
        "so do not run them again.\n"
        "Use these values for the live facts and answer the operator's newest message in your own "
        "words, as short as the question deserves. Lead with the answer, then the receipts (the real "
        "paths/URLs/values above). Never repeat your previous answer, never narrate the tool calls, "
        "never add next steps. "
        "If a readout has ok=false the limb failed: say what failed and why (the url and the error "
        "code), never invent the content it was supposed to bring.\n"
        + json.dumps(compact, default=str)[:3000]
        + _file_intent_note(traces)
    )


_FILE_TEXT = re.compile(
    r"(?:with (?:the )?(?:exact )?(?:text|content|contents)\s*[:=]?\s*[\"\u201c']?(?P<t1>[^\"\u201d']{1,300})[\"\u201d']?"
    r"|(?:containing|that says|which says|reading|saying)\s*[:=]?\s*[\"\u201c']?(?P<t2>[^\"\u201d']{1,300})[\"\u201d']?"
    r"|contents?\s*(?:(?:are|is)\s*)?[:=]?\s*[\"\u201c']?(?P<t3>[^\"\u201d']{1,300})[\"\u201d']?)",
    re.I,
)


def host_write_request(text: str) -> dict[str, Any] | None:
    """Run the write on the host when the operator's own sentence names both the file and its contents.

    Measured (gauntlet 2026-09-21, T4/T5/T7): asked in plain words for a file, the on-box brain did not
    call save_note in five of twelve tasks - it described the file instead. A request that already states
    the name and the text needs no judgement, so the console runs it, exactly as it already does for
    arithmetic, github and hugging-face asks. The model then reports the path it really got.
    """
    if not text or not FILE_HINT.search(text):
        return None
    named = _FILE_NAME.search(text)
    if not named:
        return None
    body = "Created by the console at the operator's request."
    got_text = _FILE_TEXT.search(text)
    if got_text:
        body = (got_text.group("t1") or got_text.group("t2") or got_text.group("t3") or "").strip().rstrip(".") or body
        # The capture runs to the end of the sentence, so it swallows the ask itself
        # ("... WORKER OK. Use your tools"). Drop a trailing instruction clause and the
        # filler word "exactly", so the file holds the value and not the request.
        body = re.split(r"\.\s+(?:use|then|and tell|and confirm|and report|do not|don't)\b",
                        body, flags=re.I)[0].strip()
        body = re.sub(r"^exactly\s+", "", body, flags=re.I).strip() or body
    where = "workspace"
    low = text.lower()
    for key in ("desktop", "documents", "downloads", "home", "workspace"):
        if key in low:
            where = key
            break
    return {"name": named.group(1), "text": body, "where": where,
            "consent": where not in ("workspace", "ws", "")}


def _file_intent_note(traces: list[dict[str, Any]]) -> str:
    """Say the folder map out loud when the operator asked for a file to be put somewhere.

    Measured 2026-09-21: with the map only present as a generic trace the on-box brain still described a
    file it had not written. So state it plainly, in the same breath as the instruction.
    """
    for t in traces or []:
        if t.get("name") != "file_intent":
            continue
        res = t.get("result") or {}
        folders = res.get("operator_folders") or {}
        here = ", ".join(f"{k}={v}" for k, v in list(folders.items())[:5])
        return ("\n\nFILE REQUEST: the operator's real folders are " + (here or "(none found)")
                + ". To put a file there you MUST call save_note with where=desktop|documents|downloads|home "
                  "(or workspace) and consent=true, then report the absolute path in its result. Do not "
                  "describe a file as created, saved or written unless save_note returned it - if you did "
                  "not call it, nothing was written.")
    return ""


def _human_skills(v: Any) -> str:
    """`{'n': 18, 'enabled': 6}` is a dict repr; humans (and the transcript) want a phrase."""
    if isinstance(v, dict):
        n, e = v.get("n"), v.get("enabled")
        if n is None:
            return "" if e is None else f"{e} enabled"
        return f"{n} on disk, {e} enabled" if e is not None else f"{n} on disk"
    return str(v or "")


def fallback_from_traces(traces: list[dict[str, Any]]) -> str:
    """Last-resort host readout — ONLY for a turn where the model returned nothing usable.

    This text is saved into the session, and a small model will happily parrot whatever it finds
    there, so it must read like prose, never like a Python dict.
    """
    sc = None
    sm = None
    for t in traces:
        if t.get("name") == "self_check" and isinstance(t.get("result"), dict):
            sc = t["result"]
        if t.get("name") == "steward_map" and isinstance(t.get("result"), dict):
            sm = t["result"]
    if sc:
        sample = ", ".join(str(x) for x in (sc.get("chatagent_sample") or [])[:8]) or "(none)"
        skills = _human_skills(sc.get("skills"))
        lines = [
            f"Self-check: {sc.get('verdict')} — limbs answered, so this is measured, not guessed.",
            f"- GitHub {sc.get('github')}",
            f"- Hugging Face {sc.get('huggingface')}",
            f"- Lattice {sc.get('lattice')}",
            f"- D:\\chatagent present: {sc.get('chatagent_exists')} · sample {sample}",
            f"- Live mounts: {sc.get('n_live_mounts')}" + (f" · skills {skills}" if skills else ""),
            "No placeholder URLs were used.",
        ]
        return "\n".join(lines)
    if not sm:
        return ""
    sites = sm.get("sites") or sm.get("lattice") or []
    return (
        "LYGO admin map is live (RESOURCE, not CANON).\n"
        f"GitHub: {sm.get('github_org') or 'https://github.com/DeepSeekOracle'}\n"
        f"Hugging Face: {sm.get('hf_org') or 'https://huggingface.co/DeepSeekOracle'}\n"
        f"Lattice: {', '.join(str(s) for s in sites[:6]) or 'https://chatagent.ca/'}\n"
        "I will not fetch github.com/user/repo or lattice.example.com.\n"
        "Name a real path (D:\\chatagent) or a URL from this map and I will list_dir / web_fetch it."
    )


PLACEHOLDER_MAP = {
    "github.com/user/repo": "https://github.com/DeepSeekOracle",
    "lattice.example.com": "https://chatagent.ca/",
    "huggingface.co/models/transformers": "https://huggingface.co/DeepSeekOracle",
}

PLACEHOLDER_OUT = re.compile("|".join(re.escape(k) for k in PLACEHOLDER_MAP), re.I)


def _scrub_placeholders(raw: str) -> tuple[str, bool]:
    """Rewrite an invented placeholder in place instead of throwing the whole answer away."""
    hit = False

    def rep(m: re.Match[str]) -> str:
        nonlocal hit
        hit = True
        return PLACEHOLDER_MAP.get(m.group(0).lower(), m.group(0))

    return PLACEHOLDER_OUT.sub(rep, raw), hit


BOILER = re.compile(r"end of admin check|next steps:|tools used:", re.I)
BOILER_LEAD = re.compile(r"^\W*(?:end of admin check|next steps|tools used)\W*:?\s*", re.I)


def strip_boiler(text: str) -> str:
    """Drop the template lines the prompt forbids, keep everything the model actually said.

    A line that merely *leads* with the marker ("Next Steps: file the receipt") keeps its substance.
    """
    keep: list[str] = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        if not s:
            keep.append(ln)
            continue
        m = BOILER_LEAD.match(s)
        if not m:
            keep.append(ln)
            continue
        rest = s[m.end() :].strip(" -–—•*")
        if len(rest) >= 12:
            keep.append(rest)
    return "\n".join(keep).strip()


def _is_only_boiler(raw: str) -> bool:
    body = re.sub(r"[\s\-•*_#]+", "", BOILER.sub("", raw or ""))
    return len(body) < 12


_READOUT_LINE = re.compile(r"^\s*NOW UTC \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}.*$", re.M)
_LIMB_FAILURE = re.compile(r"^[\w.\-]+ could not answer \(.*\)\.?$")
_READOUT_NOTE = re.compile(r"^\s*READOUT \(.*$", re.M)
# the tail's own capability sentence, echoed back as if it were the answer (measured 2026-09-21)
_TAIL_CAPABILITY = re.compile(r"^\s*world_pulse holds .*$", re.M)


def strip_readout(text: str) -> str:
    """Drop the volatile readout that rides the newest message and can come back inside an answer.

    Measured 2026-09-21: asked to reply exactly "LOCAL SIDE", the console answered "LOCAL SIDE" and
    then the clock and world line. Only a line that is itself a timestamped readout is removed, so a
    genuine answer about the time ("The current UTC time is ...") is left untouched.
    """
    src = text or ""
    out = _READOUT_NOTE.sub("", _TAIL_CAPABILITY.sub("", _READOUT_LINE.sub("", src)))
    if out == src:
        return src
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def is_limb_failure(text: str) -> bool:
    """True when an answer is nothing but our own limb-failure prose - not an answer at all."""
    return bool(_LIMB_FAILURE.match((text or "").strip()))


def limb_failure_name(text: str) -> str:
    """Which limb failed, for an honest sentence that keeps the internal error out of the reply."""
    m = _LIMB_FAILURE.match((text or "").strip())
    return m.group(0).split(" could not answer")[0].strip() if m else "tool"


# Limbs that WRITE a file. A reader (image_see / image_info / image_list) is never advertised as a
# new picture: the path in its result is the file it was *handed*.
_ARTIFACT_READERS = {"image_see", "image_info", "image_list", "vision_read"}
_ARTIFACT_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".mp3", ".wav", ".flac", ".ogg")
# Which of those the music limb wrote. A song is named under the answer the same way a picture is,
# and it says song rather than picture because that is the file the person is about to play.
_SONG_EXTS = (".mp3", ".wav", ".flac", ".ogg")


def _produced_artifacts(traces):
    """(path, bytes) for every file this turn's limbs actually produced."""
    out = []
    for t in traces or []:
        try:
            if not isinstance(t, dict):
                continue
            name = str(t.get("name") or "")
            res = t.get("result")
            if not isinstance(res, dict) or not res.get("ok"):
                continue
            if name in _ARTIFACT_READERS:
                continue
            path = str(res.get("path") or "")
            if not path or not path.lower().endswith(_ARTIFACT_EXTS):
                continue
            out.append((path, res.get("bytes")))
        except Exception:  # a cosmetic helper may never break a turn
            continue
    return out


def surface_artifacts(text, traces) -> str:
    """Name a picture the turn produced, when the answer does not already name it.

    Measured 2026-09-21: the operator asked the API brain for a picture and the answer was a promise -
    "I'll render it now - first run loads the checkpoint, so give it a minute." The limb did produce a
    real PNG (23.6s, 957,430 bytes); the answer simply never said where it was, so the result was
    invisible to the person who asked for it. The answer is kept as written and the result is named
    under it - never the other way round.
    """
    body = str(text or "")
    made = _produced_artifacts(traces)
    if not made:
        return body
    if _SAFETY_LECTURE.search(body):
        # The file is already on disk. Gemma's baked lecture is not this console's policy.
        body = ("Made on this machine with music_generate."
                if any(str(p).lower().endswith(_SONG_EXTS) for p, _ in made)
                else "Drawn on this machine with image_generate.")
    lines = []
    for p, nbytes in made:
        if os.path.basename(p).lower() in body.lower():
            continue
        try:
            pretty = format(int(nbytes), ",") + " bytes"
        except (TypeError, ValueError):
            pretty = ""
        kind = "song" if str(p).lower().endswith(_SONG_EXTS) else "picture"
        lines.append("\u00b7 %s: %s%s" % (kind, p, (" (" + pretty + ")") if pretty else ""))
    return (body + ("\n" if body.strip() else "") + "\n".join(lines)).strip() if lines else body


CLAIM_FILE = re.compile(
    r"(i (have )?(created|made|saved|written)|(created|saved|written) (the|a) (file|note|document)|"
    r"file (is|has been|was) (created|saved|written)|receipt|path:)",
    re.I,
)
# Limbs whose result is evidence that something was actually written.
_WRITER_LIMBS = ("save_note", "python_exec", "write_file", "shell")
# A picture limb writes a real file too, and its result carries the path it wrote.
_PICTURE_LIMBS = ("image_generate",)
# So does the music limb. MEASURED live 2026-09-25 before this line existed: asked for a song, the
# console answered "I have generated the song" with a workspace path and the music limb had not run -
# the same shape of lie the picture path was built to catch, so a song is watched the same way.
_SONG_LIMBS = ("music_generate",)
# And so does the video limb. MEASURED live 2026-10-10: a turn really did render a 97-frame clip and
# write LYGO_00006_.mp4, and the check still told the operator "[host check] no write limb ran in this
# turn, so nothing was created" - the one file claim it did not know, denying a file that was sitting
# on disk. A video is watched exactly the way a picture and a song are watched.
_VIDEO_LIMBS = ("video_generate",)
_MEDIA_LIMBS = _PICTURE_LIMBS + _SONG_LIMBS + _VIDEO_LIMBS
_WRITES_A_FILE = _WRITER_LIMBS + _MEDIA_LIMBS
# MEASURED live 2026-09-23: every picture reply in the operator's session was shaped
# "I have generated the image of ... The image is saved at: `<path>`" (also "is available at", "is stored
# at", "I have successfully generated the image"), none of it matched CLAIM_FILE, and the renders behind
# them had died - so five prompts in a row were answered with an invented path and no picture on disk.
CLAIM_PICTURE = re.compile(
    r"(?:generated|created|drew|made|rendered|painted|produced)\s+"
    r"(?:the|a|an|this|that|your|you)?\s*"
    r"(?:image|picture|photo|photograph|meme|illustration|artwork|render)"
    r"|(?:image|picture|photo|photograph|meme|illustration|artwork)\b[^.\n]{0,60}?(?:is|has been|was)\s+"
    r"(?:\w+\s+){0,3}(?:saved|stored|written|created|generated|drawn|rendered|available|ready)"
    r"|(?:saved|stored|written)\s+(?:it|the|that)\s+(?:as|to|in)",
    re.I,
)


CLAIM_SONG = re.compile(
    r"(?:generated|created|made|rendered|produced|wrote|recorded|composed|sung|sang)\s+"
    r"(?:the|a|an|this|that|your|you)?\s*(?:song|track|tune|music|anthem|jingle|ballad|audio|mp3)"
    r"|(?:song|track|tune|audio|mp3)\b[^.\n]{0,60}?(?:is|has been|was)\s+"
    r"(?:\w+\s+){0,3}(?:saved|stored|written|created|generated|rendered|ready|available|done)"
    r"|(?:saved|stored|written|exported|rendered)\s+(?:it|the|that)\s+(?:as|to|in)",
    re.I,
)

CLAIM_VIDEO = re.compile(
    r"(?:generated|created|made|rendered|produced|animated|filmed)\s+"
    r"(?:the|a|an|this|that|your|you)?\s*(?:video|clip|movie|animation|mp4|footage)"
    r"|(?:video|clip|movie|animation|mp4|footage)\b[^.\n]{0,60}?(?:is|has been|was)\s+"
    r"(?:\w+\s+){0,3}(?:saved|stored|written|created|generated|rendered|ready|available|done)"
    r"|(?:saved|stored|written|exported|rendered)\s+(?:it|the|that)\s+(?:as|to|in)",
    re.I,
)


def _claim_advice(picture: bool, song: bool = False) -> str:
    """What to offer next, in the words of the thing that was claimed."""
    if picture:
        return ("Ask again and I will call image_generate for it and return the path it really wrote.")
    if song:
        return ("Ask again and I will call music_generate for it and return the path it really wrote "
                "(a song renders in minutes on this machine).")
    return ("Ask again and I will call save_note with where=desktop (or documents/downloads) and return "
            "the path it really wrote.")


def _failed_limb_trace(traces, limbs) -> tuple[str, dict[str, Any]] | None:
    """The newest limb in `limbs` that RAN this turn and did not answer ok, with its own result.

    A failure is the interesting case for the host, and it is the case a naive `ok or path` filter
    silently drops: MEASURED 2026-09-25, a song whose engine died came back `{ok: false, error:
    music_engine_failed, why: 'CUDA out of memory'}` and the operator was told "nothing wrote a song"
    with the engine's own reason and remedy thrown away. The failed result is the evidence - the
    error name and the hint must reach the person who asked.
    """
    for t in reversed(list(traces or [])):
        if t.get("name") not in limbs:
            continue
        res = t.get("result")
        if isinstance(res, dict) and not res.get("ok"):
            return str(t.get("name")), res
    return None


def _failed_picture_trace(traces) -> tuple[str, dict[str, Any]] | None:
    """The picture limb that ran this turn and did not answer ok, with its own result."""
    return _failed_limb_trace(traces, _PICTURE_LIMBS)


def _drawn_trace(traces) -> tuple[str, dict[str, Any]] | None:
    """The picture or music limb that really wrote a file this turn (its result carries the path)."""
    for t in reversed(list(traces or [])):
        if t.get("name") not in _MEDIA_LIMBS:
            continue
        res = t.get("result")
        if isinstance(res, dict) and res.get("ok") and res.get("path"):
            return str(t.get("name")), res
    return None
# a real path needs a drive, a separator and some length: prose like "t:**" is not a path

_PATHISH = re.compile(r"[A-Za-z]:[\\/][^ \t\n`\"\')\]]{2,}", re.I)


def verify_file_claims(text: str, traces: list[dict[str, Any]]) -> str:
    """A receipt the host cannot find is not a receipt.

    Measured live 2026-09-21: asked to "create a note on the desktop", the on-box brain replied with a
    receipt for a path under <USER>n/Desktop that did not exist anywhere - wrong user name, and no
    writing limb had run. So: when a turn claims a file and nothing wrote one, say so; when it names an
    absolute path, look at the disk and report what is really there.

    Measured live 2026-09-23, the picture case: asked for a kitten, the limb answered
    `{"ok": false, "error": "image_failed", "exit": 3221225786, "seconds": 183.4}` with no file on disk,
    and the reply said "The image is saved at: `<path>`". Nothing caught it, so the operator asked five
    times. A picture is a file claim like any other - and when the limb really did draw one, the path it
    wrote is the only path that is true.
    """
    from pathlib import Path

    picture = bool(text and CLAIM_PICTURE.search(text))
    song = bool(text and CLAIM_SONG.search(text))
    video = bool(text and CLAIM_VIDEO.search(text))
    if not text or '[host check]' in text or not (picture or song or video or CLAIM_FILE.search(text)):
        return text
    for t in traces or []:
        if t.get("name") not in _WRITES_A_FILE:
            continue
        res = t.get("result")
        if isinstance(res, dict) and res.get("ok") and (res.get("path") or res.get("verified") or res.get("ran")):
            break          # something really was written, or really did run
    else:
        seen: list[str] = []
        for m in _PATHISH.finditer(text):
            raw = m.group(0).rstrip(".,;:)`")
            if raw and raw not in seen:
                seen.append(raw)
        real = [q for q in seen if Path(q).exists()]
        missing = [q for q in seen if q not in real]
        failed = _failed_picture_trace(traces)
        if failed is None and song:
            failed = _failed_limb_trace(traces, _SONG_LIMBS)
        if failed is None and video:
            failed = _failed_limb_trace(traces, _VIDEO_LIMBS)
        if failed is not None:
            name, res = failed
            secs = res.get("seconds")
            did = "drawn" if picture else "written"
            line = ("[host check] %s ran in this turn and failed (%s%s), so no song was %s"
                    % (name, str(res.get("error") or "no_file"),
                       ", %.1f s" % float(secs) if isinstance(secs, (int, float)) and secs else "", did)
                    if song and not picture else
                    "[host check] %s ran in this turn and failed (%s%s), so nothing was %s"
                    % (name, str(res.get("error") or "no_file"),
                       ", %.1f s" % float(secs) if isinstance(secs, (int, float)) and secs else "", did))
            if real:
                line += "; the path in this answer is an older file already on disk: " + real[0]
            if missing:
                line += "; this is not on disk: " + ", ".join(missing[:3])
            hint = str(res.get("hint") or "").strip()
            line += ". " + (hint[:300] if hint else _claim_advice(picture, song))
            return text + "\n\n" + line
        if real:
            return text + "\n\n[host check] that file really is on disk: " + real[0]
        if video and not picture:
            what = "made a video"
        elif song and not picture:
            what = "wrote a song"
        elif picture:
            what = "drew a picture"
        else:
            what = ""
        if what:
            if missing:
                return text + ("\n\n[host check] nothing in this turn %s, and this is not on "
                               "disk: " % what + ", ".join(missing[:3]) + ". " + _claim_advice(picture, song))
            return text + ("\n\n[host check] nothing in this turn %s, and no file is named. " % what
                           + _claim_advice(picture, song))
        if missing:
            return text + ("\n\n[host check] no write limb ran in this turn, and this is not on disk: "
                           + ", ".join(missing[:3]) + ". Nothing was created. "
                           + _claim_advice(picture, song or video))
        return text + ("\n\n[host check] no write limb ran in this turn, so nothing was created. "
                       + _claim_advice(picture, song or video))
    # A writer ran, and a picture can still be described with a path it never wrote.
    drawn = _drawn_trace(traces)
    if drawn is not None:
        name, res = drawn
        true_path = str(res.get("path") or "")
        named = [m.group(0).rstrip(".,;:)`") for m in _PATHISH.finditer(text)]
        if true_path and not any(Path(q) == Path(true_path) for q in named):
            return text + ("\n\n[host check] %s really wrote %s this turn; the path above is not that "
                           "file." % (name, true_path))
    return text


CLAIM_LIMB_OUTPUT = re.compile(
    r"(?:(?:the\s+)?(?:output|result|return value)\s+of\s+(?P<l1>python_exec|shell|calc|read_file|"
    r"list_dir|find_files|glob_files|web_search|web_fetch|steward_map|task_add|download_url))"
    r"|(?P<l2>python_exec|shell|calc|read_file|list_dir|find_files|glob_files|web_search|web_fetch|"
    r"steward_map|task_add|download_url)\s*"
    r"(?:returned|returns|printed|prints|gave|gives|produced|reported|reports|says|said|outputs)",
    re.I,
)
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def _trace_output(traces) -> dict[str, str]:
    """limb name -> everything the result said, this turn, only for limbs that actually answered."""
    out: dict[str, str] = {}
    for t in traces or []:
        try:
            if not isinstance(t, dict):
                continue
            res = t.get("result")
            if not isinstance(res, dict) or not res.get("ok"):
                continue
            out[str(t.get("name") or "")] = json.dumps(res, default=str)
        except Exception:  # a cosmetic helper may never break a turn
            continue
    return out


def verify_output_claims(text: str, traces: list[dict[str, Any]]) -> str:
    """A tool output the host cannot show is not a tool output.

    Measured 2026-09-21 (gauntlet T12): the reply ended "here is the output of python_exec: 63" while the
    turn's traces held only `steward_map` - the number came from nowhere. `verify_file_claims` catches a
    claimed FILE; nothing caught a claimed limb OUTPUT, which is the same lie in its other shape.

    A named limb with no trace this turn is reported. A limb that did run is questioned only when the
    reply states a number its own result does not contain, so an honest answer that quotes an earlier
    turn's value is not touched. The answer is never rewritten: it is kept as written and the host check
    goes under it, exactly like the file-path check.
    """
    body = str(text or "")
    if not body or "[host check]" in body:
        return body
    claims: list[str] = []
    for m in CLAIM_LIMB_OUTPUT.finditer(body):
        limb = (m.group("l1") or m.group("l2") or "").lower()
        if limb and limb not in claims:
            claims.append(limb)
    if not claims:
        return body
    ran = _trace_output(traces)
    problems: list[str] = []
    for limb in claims:
        if limb not in ran:
            problems.append("this reply names %s, and %s did not run in this turn" % (limb, limb))
            continue
        for m in CLAIM_LIMB_OUTPUT.finditer(body):
            named = (m.group("l1") or m.group("l2") or "").lower()
            if named != limb:
                continue
            window = body[max(0, m.start() - 90): m.end() + 140]
            for num in _NUMBER.findall(window):
                if num not in ran[limb]:
                    problems.append("%s is stated as %s's output, and its result holds no %s"
                                    % (num, limb, num))
                    break
    if not problems:
        return body
    return (body + "\n\n[host check] " + "; ".join(problems[:2])
            + ". Nothing in this turn's traces supports that, so treat it as not measured - ask again and "
              "I will call the limb and report the value it really returned.")


FILE_ASK = re.compile(
    r"\b(create|make|save|write|put)\b[^.?!]{0,50}\b(file|note|txt|md|document)\b", re.I
)
_FILE_NAME = re.compile(r"\b([A-Za-z0-9_.-]+\.(?:txt|md|json|csv|log|py|rst))\b", re.I)


def console_completes_the_write(user_text: str, traces: list[dict[str, Any]], reply: str) -> tuple[str, dict[str, Any] | None]:
    """Last rung of the ladder: the operator asked for a file, no limb wrote one, so the console writes it.

    The console exists to make the machine work, not to watch a model fail politely. When the writing limb
    was never called, the console performs the write itself and says so in the reply - so the operator gets
    the file AND an honest account of who made it. Returns (reply, receipt) where receipt is None if the
    limb already did the job (or the ask was not a write).
    """
    import time as _time

    from limbs import extra as _extra

    if not user_text or not reply or not FILE_ASK.search(user_text):
        return reply, None
    for t in traces or []:
        if t.get("name") in _WRITER_LIMBS:
            res = t.get("result")
            if isinstance(res, dict) and res.get("ok") and (res.get("path") or res.get("ran") or res.get("verified")):
                return reply, None  # a limb really wrote something; leave it alone
    named = _FILE_NAME.search(user_text)
    name = named.group(1) if named else f"note_{_time.strftime('%H%M')}.txt"
    where = "workspace"
    for key in ("desktop", "documents", "downloads", "home"):
        if key in user_text.lower():
            where = key
            break
    body = ("Written by the console for the operator's request:\n\n"
            f"\"{user_text.strip()[:400]}\"\n\n"
            "The on-box brain did not call the writing limb this turn, so the console wrote this file to "
            "complete the request. The model's own words for this turn follow.\n\n---\n" + reply.strip()[:2000])
    got = _extra("save_note", {"name": name, "text": body, "where": where,
                               "consent": where not in ("workspace", "ws", "")})
    if not (isinstance(got, dict) and got.get("ok") and got.get("verified")):
        return reply + ("\n\n[console] no writing limb ran and my own attempt failed ("
                        + str((got or {}).get("error")) + "). Nothing was written."), None
    note = (f"\n\n[console] the on-box brain did not call the writing limb, so the console wrote it "
            f"itself: {got['path']} ({got['bytes']} bytes). Receipt is the limb's own, read back from disk.")
    return reply + note, got


def sanitize_assistant(text: str, traces: list[dict[str, Any]]) -> str:
    return verify_file_claims(_sanitize_assistant_base(text, traces), traces)

def _sanitize_assistant_base(text: str, traces: list[dict[str, Any]]) -> str:
    """Clean the model's answer. It never gets replaced by a canned string any more.

    The old behaviour (any draft containing "End of Admin Check" was thrown away and swapped for a
    hardcoded readout) is what made every turn look identical and killed the agent's voice.
    """
    raw = strip_readout(strip_boiler((text or "").strip()))
    if not raw or _is_only_boiler(raw) or is_limb_failure(raw):
        fb = strip_readout(fallback_from_traces(traces))
        if is_limb_failure(fb):
            # A limb that failed is not an answer. Measured 2026-09-21: asked for three words that
            # rhyme with "stack", the model repeated our own internal error instead of answering and
            # the operator was shown "calc could not answer (invalid syntax (<unknown>, line 1))." -
            # an internal message presented as the reply. Name the limb, keep the traceback out.
            return "The %s limb could not answer, so this reply does not use it." % limb_failure_name(fb)
        return fb or raw
    raw, hit = _scrub_placeholders(raw)
    if hit and traces:
        raw += "\n\n(Placeholder URLs in the draft were replaced with the real org links.)"
    return raw


def same_answer(prev: str, cur: str, ratio: float = 0.86) -> bool:
    """True when the model just repeated its previous answer instead of answering the new turn."""
    a = re.sub(r"\W+", " ", (prev or "").lower()).strip()
    b = re.sub(r"\W+", " ", (cur or "").lower()).strip()
    if not a or not b:
        return False
    if min(len(a), len(b)) < 80:
        # Short replies repeat legitimately ("ok", "done") — not worth a regeneration.
        return False
    if a == b:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= ratio


HISTORY_CHARS = 9000


def trim_history(messages: list[dict[str, Any]], budget: int = HISTORY_CHARS) -> list[dict[str, Any]]:
    """Keep the newest turns inside the engine's window so the system prompt always survives.

    The engine runs 8192 tokens; the RUNTIME/LIMBS/SOUL block is the part that must never be lost.
    """
    out: list[dict[str, Any]] = []
    used = 0
    for m in reversed(messages or []):
        c = m.get("content")
        n = len(c) if isinstance(c, str) else 400
        if out and used + n > budget:
            break
        out.append(m)
        used += n
    out.reverse()
    return out


def run_tools_round(assistant_text: str, message: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    calls = extract_tool_calls(message or {}, assistant_text or "")
    traces = []
    for spec in calls[:4]:
        name, args = spec["name"], spec.get("arguments") or {}
        # A small model will still aim arithmetic at the web tools. The query says what it is, so
        # route it to calc rather than let a search page stand in for a sum.
        q = args.get("q") or args.get("url") or ""
        if name in ("web_search", "web_fetch") and math_only(q):
            name, args = "calc", {"expr": math_expr(q)}
        result = dispatch(name, args)
        traces.append({"name": name, "arguments": args, "result": result})
    return traces
