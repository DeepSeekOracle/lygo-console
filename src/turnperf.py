"""LYGO TurnPerf — what one turn cost, and who spent the tokens.

The engine already reports how many tokens it generated (llama.cpp's own `timings`, and
`prompt_n` / `predicted_n` on every call). What it cannot know is how many of those tokens
reached the operator's eyes: between the engine and the bubble the console sanitises,
strips, trims, re-shapes and sometimes replaces the text entirely.

Measured on this box 2026-09-22 (qwen2.5-coder:7b, RTX 4060 Ti, ngl 99, 16k window):
three turns asked "Reply with exactly: CACHE TEST" and the engine generated **137 tokens
each time at 47.7 tok/s** to display **10 characters**. 2.87 s of the 3.2 s turn was text
nobody saw, and nothing in the console could see it happen - the engine's own cumulative
counter (`llamacpp:tokens_predicted_total 412`) is the only party that knew.

This module is the missing accounting. It is deliberately engine-agnostic and cheap:

- token counts come from the ENGINE's own tokenizer (`/tokenize`) when it is reachable, so
  the shown text and the discarded text are counted by the same ruler, and the method used
  is always reported (`counted`). An unreachable engine falls back to the kit's existing
  pessimistic estimator and says so - never a silent guess.
- the difference between generated and shown is ATTRIBUTED, not just measured: tokens that
  went into a limb call, into a reasoning block, into an answer that a later call replaced,
  and a residual that nothing explains. A residual is the finding; a rounded-over gap is not.
- the asked SHAPE is read from the operator's own words ("reply with exactly X", "one word",
  "three sentences"), and turned into a hard token budget and - for a pure shape ask - a
  turn with no limb schema at all. That is where the 137 tokens came from: a two-word echo
  was offered 24 tool definitions and a 4096-token budget.

Nothing here writes state, calls a limb, or raises into a turn. Every entry point is
total: the worst outcome is a record that says less.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Callable

SIGNATURE = "Δ9Φ963-LYGO-TURNPERF-v1"

# A shape ask still needs room for the answer plus a sentence of framing. 24 is the floor
# because below it the engine truncates ordinary words mid-token and the answer is worse
# than the padding it saved.
SHAPE_FLOOR = 24
# Above this, tokens the operator never saw are worth sampling in the turn record.
SURPLUS_SAMPLE_AT = 24
SAMPLE_CHARS = 160
# Grace given to a *counted* shape (N sentences, N bullet lines) after the count is met:
# the model may still be finishing a clause. A pure echo gets no grace - nothing can
# legitimately follow the phrase that was asked for.
GRACE_TOKENS = 16
TOKENIZE_TIMEOUT = 2.0  # the shape is read on the turn's own critical path: keep the ceiling low


# --------------------------------------------------------------------------------------
# the ruler: the engine's own tokenizer, with a labelled fallback
# --------------------------------------------------------------------------------------
def engine_tokenize(text: str, *, port: int | None, api_key: str = "", timeout: float = TOKENIZE_TIMEOUT) -> int | None:
    """Token count from the running engine's `/tokenize`. None when it cannot be asked.

    llama-server counts tokens without taking a slot, so this costs no generation time and
    cannot queue behind the operator's own turn.
    """
    if not text or not port:
        return None
    body = json.dumps({"content": text[:40000]}).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{int(port)}/tokenize",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            got = json.loads(resp.read().decode("utf-8", "replace") or "{}")
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    toks = got.get("tokens") if isinstance(got, dict) else None
    return len(toks) if isinstance(toks, list) else None


def count(text: str, *, port: int | None = None, api_key: str = "") -> tuple[int, str]:
    """(tokens, how). The method is always named, because an estimate is not a measurement."""
    if not text:
        return 0, "empty"
    n = engine_tokenize(text, port=port, api_key=api_key)
    if n is not None:
        return n, "engine"
    try:
        import compaction

        return int(compaction.est_tokens(text)), "estimate"
    except Exception:  # noqa: BLE001 - a telemetry helper may never break a turn
        return len(text) // 4 + 1, "estimate"


def count_many(texts: list[str], *, port: int | None = None, api_key: str = "") -> tuple[int, str]:
    """One ruler for a set of strings: summed, and the method that produced them."""
    joined = "\n".join(t for t in texts if t)
    if not joined:
        return 0, "empty"
    # Count the concatenation, not each piece: the pieces are alternatives, and one
    # /tokenize call per call would be several round trips on the turn's critical path.
    return count(joined, port=port, api_key=api_key)


# --------------------------------------------------------------------------------------
# one engine call, as the engine answered it
# --------------------------------------------------------------------------------------
def call_texts(parsed_obj: Any) -> dict[str, str]:
    """(content, reasoning, calls_json) for the first choice of an engine reply.

    `predicted_n` counts every token the engine generated. Content is only one of the places
    those tokens can land: llama-server moves a tool call out into `tool_calls` and, for a
    template with a thinking block, moves the reasoning into `reasoning_content`. Both leave
    `content` short - which is exactly how a 137-token generation displays 10 characters.
    """
    out = {"content": "", "reasoning": "", "calls": ""}
    try:
        if not isinstance(parsed_obj, dict):
            return out
        msg = ((parsed_obj.get("choices") or [{}])[0] or {}).get("message") or {}
        if not isinstance(msg, dict):
            return out
        for key, slot in (("content", "content"), ("reasoning_content", "reasoning"),
                          ("reasoning", "reasoning"), ("thinking", "reasoning")):
            val = msg.get(key)
            if isinstance(val, str) and val:
                out[slot] = (out[slot] + "\n" + val) if out[slot] else val
        calls = msg.get("tool_calls")
        if calls:
            out["calls"] = json.dumps(calls, default=str)
    except Exception:  # noqa: BLE001
        return out
    return out


def call_timings(parsed_obj: Any) -> dict[str, Any] | None:
    """The engine's own timing block for one call, or None when it sent none.

    A STREAMED call carries its numbers in the final chunk, and only when the request asked for
    it (`stream_options.include_usage`): read the streamed turn like any other call, or a whole
    class of turns - the ones the operator actually watches - would have no record at all.
    `usage` is the fallback for a build that reports token counts without `timings`.
    """
    try:
        if not isinstance(parsed_obj, dict):
            return None
        t = parsed_obj.get("timings")
        if not isinstance(t, dict) or not (t.get("predicted_n") or t.get("prompt_n")):
            usage = parsed_obj.get("usage")
            if not isinstance(usage, dict):
                return None
            if not (usage.get("completion_tokens") or usage.get("prompt_tokens")):
                return None
            t = {
                "prompt_n": usage.get("prompt_tokens"),
                "predicted_n": usage.get("completion_tokens"),
            }
            details = usage.get("prompt_tokens_details")
            if isinstance(details, dict) and details.get("cached_tokens") is not None:
                t["cache_n"] = details.get("cached_tokens")
        return t
    except Exception:  # noqa: BLE001
        return None


def note_call(log: list[dict[str, Any]], parsed_obj: Any) -> None:
    """Append one engine call to a turn's log: timings plus the text those tokens produced.

    Never raises: a missing timing block or an odd shape is not a turn failure. Callers keep
    the log for one turn only - module-level state would leak across turns and across threads.
    """
    try:
        t = call_timings(parsed_obj)
        if not t:
            return
        texts = call_texts(parsed_obj)
        entry = {
            "prompt_n": int(t.get("prompt_n") or 0),
            "prompt_tok_s": round(float(t.get("prompt_per_second") or 0), 1),
            "gen_n": int(t.get("predicted_n") or 0),
            "gen_tok_s": round(float(t.get("predicted_per_second") or 0), 1),
            "content_chars": len(texts["content"]),
            "reasoning_chars": len(texts["reasoning"]),
            "call_chars": len(texts["calls"]),
        }
        for src, dst in (("prompt_ms", "prompt_ms"), ("predicted_ms", "gen_ms")):
            try:
                if t.get(src) is not None:
                    entry[dst] = round(float(t.get(src)), 1)
            except (TypeError, ValueError):
                pass
        # How much of this prompt the engine already had in cache (llama.cpp's own `cache_n`).
        # This is the number that answers "did the prefix-cache work this turn?" without a guess.
        try:
            if t.get("cache_n") is not None:
                entry["cache_n"] = int(t.get("cache_n") or 0)
        except (TypeError, ValueError):
            pass
        log.append(entry)
        # The texts ride the same list under private keys so a single log serves both the
        # numbers and the attribution; `calls` in the public record strips them back out.
        log[-1]["_content"] = texts["content"]
        log[-1]["_reasoning"] = texts["reasoning"]
        log[-1]["_calls"] = texts["calls"]
    except Exception:  # noqa: BLE001
        return


def _ms_of(entry: dict[str, Any], kind: str) -> float:
    """Milliseconds for one phase of a call, derived from the engine's own rates when absent."""
    key = "prompt_ms" if kind == "prompt" else "gen_ms"
    try:
        if entry.get(key) is not None:
            return float(entry[key])
    except (TypeError, ValueError):
        pass
    n = int(entry.get("gen_n" if kind == "gen" else "prompt_n") or 0)
    rate = float(entry.get("gen_tok_s" if kind == "gen" else "prompt_tok_s") or 0)
    if n and rate > 0:
        return round(1000.0 * n / rate, 1)
    return 0.0


# --------------------------------------------------------------------------------------
# the accounting
# --------------------------------------------------------------------------------------
def attribute(
    *,
    timing_log: list[dict[str, Any]],
    shown_text: str,
    raw_texts: list[str] | None = None,
    wall_ms: float | None = None,
    limbs: int = 0,
    shape: dict[str, Any] | None = None,
    stopped: dict[str, Any] | None = None,
    port: int | None = None,
    api_key: str = "",
    default_cap: int | None = None,
) -> dict[str, Any]:
    """Build the turn's record: generated vs shown, and where the difference went.

    `raw_texts` is every piece of text the engine's tokens could have become this turn
    (each call's content, in order). The text the operator kept is `shown_text`. Nothing
    is guessed: `unaccounted_tokens` is what is left when every named sink is subtracted,
    and it is reported even when it is zero.
    """
    try:
        shown_text = str(shown_text or "")
        if raw_texts is None:
            # Every piece of text this turn's tokens could have become, in call order. The log
            # carries them under `_content`; passing them is only for a caller that has another
            # candidate list (a stream that was cut, an answer a later call replaced).
            raw_texts = [e.get("_content") or "" for e in timing_log]
        gen_n = sum(int(e.get("gen_n") or 0) for e in timing_log)
        new_n = sum(int(e.get("prompt_n") or 0) for e in timing_log)
        prefill_ms = round(sum(_ms_of(e, "prompt") for e in timing_log), 1)
        gen_ms = round(sum(_ms_of(e, "gen") for e in timing_log), 1)
        shown_n, ruler = count(shown_text or "", port=port, api_key=api_key)
        calls_n, _ = count_many([e.get("_calls") or "" for e in timing_log], port=port, api_key=api_key)
        think_n, _ = count_many([e.get("_reasoning") or "" for e in timing_log], port=port, api_key=api_key)
        shown_core = (shown_text or "").strip()
        superseded = [t for t in (raw_texts or []) if t and t.strip() and t.strip() != shown_core]
        superseded_n, _ = count_many(superseded, port=port, api_key=api_key)
        accounted = shown_n + calls_n + think_n + superseded_n
        rec: dict[str, Any] = {
            "engine_calls": len(timing_log),
            "gen_tokens": gen_n,
            "gen_tok_s": (timing_log[-1].get("gen_tok_s") if timing_log else None),
            "shown_tokens": shown_n,
            "shown_chars": len(shown_text or ""),
            "prefill_new_tokens": new_n,
            "prefill_tokens": new_n,
            "prefill_tok_s": (timing_log[0].get("prompt_tok_s") if timing_log else None),
            "prompt_tokens": max([int(e.get("prompt_n") or 0) for e in timing_log] or [0]),
            "prompt_tok_s": (max(timing_log, key=lambda e: int(e.get("prompt_n") or 0)).get("prompt_tok_s")
                             if timing_log else None),
            "call_tokens": calls_n,
            "thinking_tokens": think_n,
            "superseded_tokens": superseded_n,
            "unaccounted_tokens": max(0, gen_n - accounted),
            "surplus_tokens": max(0, gen_n - shown_n),
            "prefill_ms": prefill_ms,
            "gen_ms": gen_ms,
            "limbs": int(limbs or 0),
            "counted": ruler,
            "signature": SIGNATURE,
        }
        if wall_ms is not None:
            rec["wall_ms"] = round(float(wall_ms), 1)
            rec["unshown_ms"] = round(gen_ms * rec["surplus_tokens"] / gen_n, 1) if gen_n else 0.0
        if gen_n:
            rec["surplus_pct"] = int(round(100.0 * rec["surplus_tokens"] / gen_n))
        else:
            rec["surplus_pct"] = 0
        # The other direction, and it is not an error: the console sometimes writes part of the
        # answer itself (a limb the on-box brain did not call, so the console did the work and said
        # so). Measured 2026-09-22: 2 tokens generated, 100 shown, on a file-write turn. Naming the
        # surplus as 0 and leaving the extra unlabelled read as a contradiction in the panel.
        # The tolerance is deliberate: an ESTIMATED ruler is a few tokens off the engine's, and a
        # one-token difference is the ruler, not the console writing prose.
        if gen_n and (shown_n - gen_n) >= max(8, int(0.2 * gen_n)):
            rec["console_authored_tokens"] = shown_n - gen_n
        if shape:
            rec["shape"] = shape.get("kind") or "free"
            rec["shape_why"] = shape.get("why") or ""
            if shape.get("cap"):
                rec["shape_cap"] = int(shape["cap"])
            if shape.get("tools") is False:
                rec["limbs_withheld"] = "shape_only_ask"
        if default_cap:
            rec["reply_cap"] = int(default_cap)
        # Prefix-cache reuse, in llama.cpp's own terms: cache_n tokens were already in the engine,
        # prompt_n had to be read. 100% means this turn paid only for its new tokens; a low number
        # on a long session means something volatile moved the head of the prompt (or the engine
        # has just booted - the first turn after a boot is always 0%).
        _cache = sum(int(e.get("cache_n") or 0) for e in timing_log)
        if _cache or rec["prefill_tokens"]:
            _total = _cache + rec["prefill_tokens"]
            rec["cache_tokens"] = _cache
            rec["cache_hit_pct"] = int(round(100.0 * _cache / _total)) if _total else 0
        if stopped:
            rec["stopped"] = stopped.get("why") or "shape_complete"
            rec["stopped_at_tokens"] = int(stopped.get("at") or 0)
            rec["stopped_saved_tokens"] = int(stopped.get("saved") or 0)
        if rec["surplus_tokens"] >= SURPLUS_SAMPLE_AT:
            # Name what was thrown away. Without this, "137 generated, 3 shown" is a number
            # nobody can act on - the next question is always *what* was dropped.
            dropped = []
            for t in (raw_texts or []):
                if t and t.strip() and t.strip() != shown_core:
                    dropped.append(t.strip())
            if not dropped:
                dropped = [e.get("_reasoning") or "" for e in timing_log]
            head = next((d for d in dropped if d.strip()), "")
            if not head and rec["call_tokens"]:
                head = "[tool call] " + (next((e.get("_calls") or "" for e in timing_log if e.get("_calls")), ""))
            rec["dropped_head"] = (head[:SAMPLE_CHARS] + "…") if len(head) > SAMPLE_CHARS else head
        rec["calls"] = [
            {k: v for k, v in e.items() if not k.startswith("_")} for e in timing_log[-4:]
        ]
        return rec
    except Exception as exc:  # noqa: BLE001 - telemetry may never break the turn it measures
        return {"error": f"{type(exc).__name__}: {exc}"[:200], "signature": SIGNATURE}


# --------------------------------------------------------------------------------------
# the asked shape: a token budget and, for a pure echo, no limb schema at all
# --------------------------------------------------------------------------------------
_WORD_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "twelve": 12, "twenty": 20, "a": 1, "an": 1,
    "single": 1, "couple": 2,
}

# "Reply with exactly: CACHE TEST" / "say only \"ok\"" / "output exactly hello"
_EXACT = re.compile(
    r"\b(?:reply|respond|answer|say|write|output|return|print|repeat)\b[^.\n]{0,24}?"
    r"\b(?:with\s+)?(?:exactly|only|just|verbatim|precisely|word\s+for\s+word)\b\s*[:\-–]?\s*"
    r"(?P<p>[\"'\u201c\u2018]?[^\"'\u201d\u2019\n]{1,120})",
    re.I,
)
_SHAPE_ASKS = (
    (re.compile(r"\b(?:in\s+)?(?:one|a\s+single|1)\s+word\b", re.I), "one_word", 12),
    (re.compile(r"\byes\s+or\s+no\b|\btrue\s+or\s+false\b", re.I), "yes_no", 8),
    (re.compile(r"\b(?:one|a\s+single|1)\s+sentence\b", re.I), "one_sentence", 72),
    (re.compile(r"\b(?:one|a\s+single|1)\s+(?:short\s+)?paragraph\b", re.I), "one_paragraph", 240),
)
_N_WORDS = re.compile(r"\b(?:in\s+|use\s+|write\s+)?(?P<n>\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten)\s+words?\b", re.I)
_N_SENTENCES = re.compile(r"\b(?P<n>\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten)\s+sentences?\b", re.I)
_N_ITEMS = re.compile(
    r"\b(?P<n>\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
    r"(?:bullet(?:\s+points?)?|items?|lines?|rows?|examples?|reasons?|steps?|options?)\b",
    re.I,
)
# The whole message is the shape ask - not a task that merely mentions a shape. Matching this
# loosely is how a real question loses its limbs, so the gate is deliberately narrow: a short
# message that carries a shape phrase, naming nothing a limb would be needed for. Where the phrase
# sits does not matter - `_SHAPE_PHRASE` below is what is tested, and `_ACTION_VETO` is the guard.
# Measured risk (2026-09-22): "Write the file to disk with exactly the text X" leads with a shape
# verb and mentions "exactly" - read naively it is a pure echo, and withholding the limb schema
# would break a real write. A message that names work keeps every limb: the shape rules then only
# trim the token budget. "Reply with exactly: CACHE TEST" names no work and is a pure echo.
# The plurals matter: "the files in my workspace" is work, and a rule that only vetoed the singular
# read it as a pure shape ask.
_ACTION_VETO = re.compile(
    r"\b(?:save|create|delete|move|copy|rename|append|list|read|open|run|execute|search|fetch|"
    r"download|install|upload|send|post|commit|push|github|git|https?|url|files?|folders?|"
    r"directory|directories|paths?|disks?|drives?|workspace|desktop|documents|downloads|notes?|"
    r"limbs?|tools?|scripts?|code|then|after\s+that|first)\b|[\\/]|\.(?:txt|md|json|py|csv|html|png|jpg)\b",
    re.I,
)

# Where a shape ask can sit. It is not always at the front: "Is 2+2=4? Answer in one word." put it
# last, that ask was not recognised at all (measured 2026-09-22: shape None, 24 limbs offered,
# 2.43 s for the word "UNKNOWN"), and the operator's own words were the only thing saying what the
# turn should have cost. Same gate as before - short message, nothing a limb would be needed for.
_SHAPE_PHRASE = re.compile(
    r"(?:reply|respond|answer|say|write|output|return|print|repeat|give)\b[^.\n]{0,24}?"
    r"\b(?:with\s+)?(?:exactly|only|just|verbatim|precisely|word\s+for\s+word)\b"
    r"|\b(?:in\s+|use\s+|with\s+)?(?:one|a\s+single|\d{1,3}|two|three|four|five|six|seven|eight|nine|ten)"
    r"\s+(?:short\s+|brief\s+)?(?:word|sentence|sentences|paragraph|paragraphs|line|lines|bullet|bullets|item|items)\b"
    r"|\byes\s+or\s+no\b|\btrue\s+or\s+false\b",
    re.I,
)


def _num(token: str) -> int:
    t = (token or "").strip().lower()
    if t.isdigit():
        return int(t)
    return int(_WORD_NUM.get(t, 0))


def _clean_phrase(p: str) -> str:
    """The asked phrase, with the trailing punctuation a sentence carries but a quote does not."""
    s = (p or "").strip().strip("\"'\u201c\u201d\u2018\u2019").strip()
    s = re.sub(r"\s+(?:and|then)\s+(?:stop|nothing else)\b.*$", "", s, flags=re.I)
    s = re.sub(r"\s*(?:and|then)\s+stop\b.*$", "", s, flags=re.I)
    return s.strip().strip(".!?,;:").strip()


def shape_budget(user_text: str, default_cap: int | None = None, *, port: int | None = None, api_key: str = "") -> dict[str, Any]:
    """What this ask actually needs: a max_tokens cap, and whether it needs limbs at all.

    Returns {} when the operator asked no shape - the turn then behaves exactly as before.
    A returned dict always carries `kind` and `why` so the record can explain the decision,
    and a pure echo also carries `tools: False`, because a request to repeat a phrase back
    is not a request to run anything: offering it 24 tool definitions is how a two-word
    answer came to be a 137-token generation.
    """
    text = str(user_text or "").strip()
    if not text:
        return {}
    # An escape hatch, because this changes how a turn is answered: LYGO_TURNPERF=0 puts the turn
    # back exactly as it was without editing code (the same convention as LYGO_HOST_PREFETCH).
    # The measurement itself (attribute) stays on - what it reports is worth having either way.
    if str(os.environ.get("LYGO_TURNPERF", "1")).strip().lower() in ("0", "off", "false", "no"):
        return {}
    try:
        whole_is_ask = (
            bool(_SHAPE_PHRASE.search(text))
            and len(text) <= 240
            and not _ACTION_VETO.search(text)
        )
        out: dict[str, Any] = {}
        m = _EXACT.search(text)
        if m and whole_is_ask:
            phrase = _clean_phrase(m.group("p"))
            if phrase:
                cost, ruler = count(phrase, port=port, api_key=api_key)
                cap = max(SHAPE_FLOOR, cost + 16)
                out = {
                    "kind": "exact",
                    "why": f'reply-with-exactly (phrase is {cost} tokens by {ruler})',
                    "phrase": phrase,
                    "cap": cap,
                    "tools": False,
                    "grace": 0,
                }
        if not out:
            for rx, kind, base in _SHAPE_ASKS:
                if rx.search(text) and whole_is_ask:
                    out = {"kind": kind, "why": rx.pattern[:40], "cap": base, "tools": False, "grace": 0}
                    break
        if not out:
            m = _N_SENTENCES.search(text)
            if m and whole_is_ask:
                n = _num(m.group("n"))
                if n:
                    out = {"kind": "sentences", "why": f"{n} sentences asked", "cap": 48 * n + 24,
                           "tools": False, "grace": GRACE_TOKENS, "count": n}
        if not out:
            m = _N_ITEMS.search(text)
            if m and whole_is_ask:
                n = _num(m.group("n"))
                if n:
                    out = {"kind": "items", "why": f"{n} items asked", "cap": 40 * n + 24,
                           "tools": False, "grace": GRACE_TOKENS, "count": n}
        if not out:
            m = _N_WORDS.search(text)
            if m and whole_is_ask:
                n = _num(m.group("n"))
                if n:
                    out = {"kind": "words", "why": f"{n} words asked", "cap": max(SHAPE_FLOOR, 4 * n + 16),
                           "tools": False, "grace": GRACE_TOKENS, "count": n}
        if not out:
            return {}
        if default_cap:
            out["cap"] = min(int(out["cap"]), int(default_cap))
        return out
    except Exception:  # noqa: BLE001 - a broken shape read must not break the turn
        return {}


def _norm(s: str) -> str:
    return re.sub(r"[\s\u201c\u201d\u2018\u2019\"']+", " ", str(s or "")).strip().lower()


# A sentence END, not just any full stop: "e.g. something" and "2.5 units" must not satisfy a
# "one sentence" ask, or the read would stop (and the answer be cut) inside an abbreviation.
_SENTENCE_END = re.compile(r"[.!?](?=\s+[\"\u201c\u2018(]?[A-Z0-9]|\s*$)")


def shape_complete(user_text: str, text: str, shape: dict[str, Any] | None = None) -> bool:
    """True when the operator's asked shape is already satisfied by `text`.

    This is the stop condition: once the shape is answered, every further token is text
    nobody asked for. Only an explicitly asked shape can return True - a free-form turn
    never stops early, because there is nothing to measure it against.
    """
    try:
        shape = shape if shape is not None else shape_budget(user_text)
        if not shape or not (text or "").strip():
            return False
        kind = shape.get("kind")
        body = _norm(text)
        if kind == "exact":
            phrase = _norm(shape.get("phrase") or "")
            return bool(phrase) and phrase in body
        if kind == "yes_no":
            return bool(re.match(r"^\s*(?:yes|no|true|false)\b", (text or "").strip(), re.I))
        if kind == "one_word":
            return len(re.findall(r"[\w'-]+", text or "")) >= 1
        if kind == "words":
            return len(re.findall(r"[\w'-]+", text or "")) >= int(shape.get("count") or 0)
        if kind == "one_sentence":
            return len(_SENTENCE_END.findall(text or "")) >= 1
        if kind == "sentences":
            return len(_SENTENCE_END.findall(text or "")) >= int(shape.get("count") or 0)
        if kind == "one_paragraph":
            return "\n\n" in (text or "").strip("\n") or len(text or "") >= 400
        if kind == "items":
            lines = [ln for ln in (text or "").splitlines() if re.match(r"^\s*(?:[-*\u2022]|\d+[.)])\s+\S", ln)]
            return len(lines) >= int(shape.get("count") or 0)
        return False
    except Exception:  # noqa: BLE001
        return False


def make_stopper(user_text: str, shape: dict[str, Any] | None = None) -> Callable[[str], bool] | None:
    """A predicate for the streaming path: has the asked shape been delivered yet?

    None when the turn asked no shape, so the caller leaves the stream alone, and a
    streaming turn can never be cut short by this module.
    """
    shape = shape if shape is not None else shape_budget(user_text)
    if not shape:
        return None
    kind = shape.get("kind")

    def done(text: str) -> bool:
        return shape_complete(user_text, text, shape)

    # The shape's grace rides the shape itself so the caller can honour it.
    setattr(done, "grace", GRACE_TOKENS if kind in ("sentences", "items", "words") else int(shape.get("grace") or 0))
    setattr(done, "kind", kind)
    return done


def report(record: dict[str, Any] | None) -> str:
    """One honest line for the console log: what a turn cost and what it did not show."""
    if not record or record.get("error"):
        return ""
    try:
        gen = int(record.get("gen_tokens") or 0)
        shown = int(record.get("shown_tokens") or 0)
        if not gen:
            return ""
        line = (f"[turnperf] generated {gen} token(s) at {record.get('gen_tok_s')} tok/s, "
                f"showed {shown} ({record.get('surplus_tokens')} unseen, {record.get('surplus_pct')}%) "
                f"· prefill {record.get('prefill_new_tokens')} new in {record.get('prefill_ms')} ms "
                f"· calls {record.get('engine_calls')} · limbs {record.get('limbs')} "
                f"· ruler {record.get('counted')}")
        if record.get("call_tokens"):
            line += f" · {record['call_tokens']} in limb calls"
        if record.get("thinking_tokens"):
            line += f" · {record['thinking_tokens']} in reasoning"
        if record.get("unaccounted_tokens"):
            line += f" · {record['unaccounted_tokens']} unaccounted"
        if record.get("stopped"):
            line += f" · stopped early ({record['stopped']})"
        return line
    except Exception:  # noqa: BLE001
        return ""
