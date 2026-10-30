"""Δ9Φ963-LYGO-MUSIC-LYRICS-v1 - what the operator typed, turned into the sections an engine sings.

YuE does not take a block of text. Its own `split_lyrics()` matches `r"\\[(\\w+)\\](.*?)\\n(?=\\[|\\Z)"`
and generates ONE SECTION PER `[tag]` MARKER: `run_n_segments = min(run_n_segments, len(sections))`.
Plain lyrics therefore yield ZERO sections and a "full song" request renders NOTHING - which is why a
paragraph of words produced either silence or a single fragment. This module is the missing half: it
keeps whatever structure the operator wrote, gives plain words a structure taken from their own stanzas,
and says how many sections - i.e. how much song - that means.

It is deliberately engine-neutral: ACE-Step takes the same `[verse] / [chorus]` shape, so one system
feeds both engines and the operator writes their words once.

Honesty rules kept here:
  * The operator's own tags win. Nothing is retagged behind their back.
  * Grouping plain lyrics is REPORTED (`tagged: False` plus a note), never presented as their choice.
  * The section count is the song's length, so it is stated in seconds as well as sections.
"""

from __future__ import annotations

import re
from typing import Any

#: The app's own unit, from its own slider label: "1000 tokens = 10s".
SECONDS_PER_1000_TOKENS = 10.0

#: The app's own default ("Number of tokens per sequence").
DEFAULT_TOKENS = 3000

#: The app's own slider tops out at 10 sequences ("paragraphs in Lyrics"), 1 at the bottom.
MIN_SECTIONS = 1
MAX_SECTIONS = 10

#: Tags the engine's pattern accepts (`\\w+`), normalised from what people actually type. A hyphen or a
#: space inside a tag would break the engine's own regex, so tags are folded to word characters.
_TAG_FIXES = {
    "pre-chorus": "prechorus",
    "prechorus": "prechorus",
    "pre chorus": "prechorus",
    "post-chorus": "postchorus",
    "post chorus": "postchorus",
    "verse1": "verse",
    "verse2": "verse",
    "v": "verse",
    "c": "chorus",
    "refrain": "chorus",
    "hook": "chorus",
    "chorus1": "chorus",
    "chorus2": "chorus",
    "instrumental": "instrumental",
    "break": "break",
    "bridge": "bridge",
    "intro": "intro",
    "outro": "outro",
    "solo": "solo",
    "end": "outro",
    "bgv": "bgv",
}

#: A bracket at the start of a line, exactly the shape the engine looks for.
_MARKER = re.compile(r"^\s*\[([^\]]{1,32})\]\s*$", re.MULTILINE)


def _norm_tag(raw: str) -> str:
    """A tag the engine's pattern can see: word characters only, lower case, synonyms folded."""
    t = (raw or "").strip().lower()
    if t in _TAG_FIXES:
        return _TAG_FIXES[t]
    t = re.sub(r"[^0-9a-zA-Z_]+", "", t.replace(" ", ""))
    if t in _TAG_FIXES:
        # `[VERSE 1]` -> "verse1" -> verse. The engine's pattern only ever sees a one-word tag, so a
        # numbered verse has to fold onto the plain tag or it renders as an unknown section.
        return _TAG_FIXES[t]
    t = re.sub(r"\d+$", "", t) or t
    return t or "verse"


def _stanzas(text: str) -> list[list[str]]:
    """Blocks of lines separated by a blank line, with a line-count fallback for one long block."""
    lines = [ln.rstrip() for ln in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.strip():
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    if len(blocks) == 1 and len(blocks[0]) > 8:
        # A wall of words with no blank lines: four-line stanzas are the convention worth assuming,
        # and the count says so rather than pretending the operator wrote them.
        return [blocks[0][i : i + 4] for i in range(0, len(blocks[0]), 4)]
    return blocks


#: Lines a human writes FOR THE READER, never for the singer. `#` and `//` are stripped before any
#: plan is made, so a header block (ARTIST / ALBUM / STYLE / BEAT DIRECTION) can ride in the same box
#: as the words without ever being sung. YuE's own `split_lyrics` already discards anything before the
#: first `[tag]`; this keeps the console's own parser honest about the same rule.
_COMMENT = re.compile(r"^\s*(#|//)")


def strip_comments(text: str) -> str:
    """The words as the engine will see them: comment lines removed, everything else untouched."""
    keep = [ln for ln in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
            if not _COMMENT.match(ln)]
    return "\n".join(keep)


def header_fields(text: str) -> dict[str, str]:
    """`ARTIST: …` style lines from the comment block, so the console can lift the style into the
    genre box instead of asking the operator to type it twice. Comments only - never lyrics."""
    out: dict[str, str] = {}
    for ln in (text or "").split("\n"):
        if not _COMMENT.match(ln):
            continue
        body = ln.strip().lstrip("#").lstrip("/").strip()
        if ":" in body:
            key, _, val = body.partition(":")
            key = key.strip().upper()
            if key and val.strip():
                out[key] = val.strip()
    return out


def looks_tagged(text: str) -> bool:
    """Did the operator write `[verse]`-style markers of their own? Comments do not count."""
    return bool(_MARKER.search(strip_comments(text)))


def parse_tagged(text: str) -> list[dict[str, Any]]:
    """The operator's own sections, in their own order and with their own tags."""
    text = strip_comments(text)
    out: list[dict[str, Any]] = []
    marks = list(_MARKER.finditer(text or ""))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = (text[m.end() : end] or "").strip("\n")
        lines = [ln.strip() for ln in body.split("\n") if ln.strip()]
        out.append({"tag": _norm_tag(m.group(1)), "lines": lines, "own_tag": m.group(1).strip()})
    # Words before the first marker belong to nobody: keep them as a first verse rather than binning
    # what the operator typed. Comment lines were already stripped, so a header block leaves nothing
    # here to sing - which is the whole point of writing it as a comment.
    if marks and (text[: marks[0].start()] or "").strip():
        head = [ln.strip() for ln in text[: marks[0].start()].split("\n") if ln.strip()]
        out.insert(0, {"tag": "verse", "lines": head, "own_tag": "verse"})
    return [s for s in out if s["lines"]]


def sections(text: str) -> list[dict[str, Any]]:
    """The sections a song will be made of: the operator's own if they wrote any, else their stanzas."""
    text = strip_comments(text)
    if looks_tagged(text):
        return parse_tagged(text)
    blocks = _stanzas(text)
    if not blocks:
        return []
    counts: dict[str, int] = {}
    for b in blocks:
        key = " ".join(b).strip().lower()
        counts[key] = counts.get(key, 0) + 1
    out: list[dict[str, Any]] = []
    for i, b in enumerate(blocks):
        key = " ".join(b).strip().lower()
        if counts[key] > 1:
            tag = "chorus"            # a stanza that comes back at all is the chorus (both times)
        elif len(b) == 1 and i == 0:
            tag = "intro"
        elif len(b) == 1 and i == len(blocks) - 1 and i > 0:
            tag = "outro"
        else:
            tag = "verse"
        out.append({"tag": tag, "lines": b, "own_tag": None})
    return out


def structured(text: str) -> str:
    """The same words in the engine's own format: `[tag]\\n lines \\n\\n`, one block per section."""
    parts = []
    for s in sections(text):
        parts.append(f"[{s['tag']}]\n" + "\n".join(s["lines"]) + "\n\n")
    return "".join(parts)


def seconds_for(tokens: int, sections_count: int = 1) -> float:
    """How much song N tokens per section buys, by the app's own published rate.

    No sections means no song: this returns 0.0 rather than one section's worth, so a plan with no
    lyrics cannot advertise a length it will not deliver.
    """
    per = (max(300.0, float(tokens or DEFAULT_TOKENS)) / 1000.0) * SECONDS_PER_1000_TOKENS
    return round(per * max(0, int(sections_count)), 1)


def sections_for_seconds(seconds: float, tokens: int = DEFAULT_TOKENS) -> int:
    """How many sections buy `seconds` of song at this token count - the inverse of `seconds_for`."""
    per = seconds_for(tokens, 1) or 1.0
    return max(1, min(MAX_SECTIONS, int(round(max(0.0, float(seconds or 0)) / per) or 1)))


def plan(text: str, tokens: int = DEFAULT_TOKENS, want_sections: int = 0) -> dict[str, Any]:
    """Everything the console needs to say about this song before it is rendered.

    `sections` is capped at the app's own maximum and at the number of sections the words actually
    have (the engine does `min(run_n_segments, len(sections))` anyway, so promising more than that
    would be a promise the engine cannot keep).
    """
    secs = sections(text)
    tagged = looks_tagged(text)
    count = len(secs)
    asked = int(want_sections or 0)
    run = min(count, asked) if asked > 0 else min(count, MAX_SECTIONS)
    run = max(0, min(MAX_SECTIONS, run))
    note = ""
    if not (text or "").strip():
        note = "no lyrics yet: this engine sings the words it is given"
    elif not tagged:
        note = (f"your words carried no [verse]/[chorus] tags, so they were grouped by their own "
                f"stanzas into {count} section(s) - add tags to say it your way")
    if count and run < count and asked > 0:
        note = f"{count} sections written, {run} asked for: {count - run} will be left unsung"
    return {
        "tagged": tagged,
        "sections": secs,
        "count": count,
        "run_n_segments": run,
        "tokens": int(tokens or DEFAULT_TOKENS),
        "seconds_per_section": round(seconds_for(tokens, 1) / 1, 1),
        "seconds": seconds_for(tokens, run),
        "note": note,
    }

# ------------------------------------------------------------------------------------------------
# the exact template YuE needs, and the writer that fills it in
# ------------------------------------------------------------------------------------------------
#: YuE's OWN contract, from `inference/gradio_server.py`:
#:
#:     def split_lyrics(lyrics):
#:         pattern = r"\[(\w+)\](.*?)\n(?=\[|\Z)"
#:         segments = re.findall(pattern, lyrics, re.DOTALL)
#:
#: Three consequences, and they decide this template's whole shape:
#:   * ONE SECTION PER `[tag]`, and the tag must be a single word (`\w+`). `[VERSE 1]` does not
#:     match at all - it is not a section, it is invisible. `[verse]` is a section.
#:   * Text before the first marker is discarded by the engine. It is safe to write a header there,
#:     and it is *read-only* to the singer - which is why the header below is written as `#` comments:
#:     the console strips comments before planning, so nothing in the header is ever sung.
#:   * The genre/mood/instrument/gender/timbre do NOT go here. They go in the genre text beside the
#:     lyrics ("e.g., instrumental, genre, mood, vocal timbre, vocal gender" - the app's own words).
#:     A STYLE: line in this header is read out by the console and offered for that box.
TEMPLATE = """# SONG: (untitled - the file name comes from your first line)
# ARTIST: your name
# STYLE: emo grunge / industrial metal / dubstep hybrid - genre, mood, instruments, vocal
# BEAT: 70 BPM, D minor - tempo, key, and what the track is built on
# VOICE: baritone, clear and gravelly, minimal ad-libs - who sings it and how
# NOTE: lines starting with # are read, never sung. Keep the [tags] as single words.

# the intro is a wordless vocal here. Delete these three lines if the song should start straight in.
[intro]
ooh - ooh - ooh

[verse]
line one of the first verse
line two of the first verse
line three of the first verse
line four of the first verse

[chorus]
the line everyone remembers
the line that answers it

[verse]
line one of the second verse
line two of the second verse
line three of the second verse
line four of the second verse

[chorus]
the line everyone remembers
the line that answers it

[outro]
the last thing you want them to hear
"""

#: A single-word `[tag]` is the only thing the engine sees as a section. This is the shape that
#: survives, in the order YuE was trained on.
SECTION_TAGS = ("intro", "verse", "prechorus", "chorus", "postchorus", "bridge", "instrumental",
                "break", "solo", "bgv", "outro")

#: Curated from the engine's own `wav_top_200_tags.json` (five axes, ~200 tags each). The full file
#: is read at call time when it is on this machine; this is the honest fallback, not a guess at names.
FALLBACK_VOCAB: dict[str, list[str]] = {
    "genre": ["pop", "rock", "indie rock", "classic rock", "hard rock", "grunge", "emo", "punk",
              "metal", "death metal", "metalcore", "industrial", "electronic", "dubstep", "techno",
              "house", "trap", "hip hop", "r&b", "soul", "funk", "blues", "jazz", "country", "folk",
              "ambient", "classical", "soundtrack", "reggae", "latin", "shoegaze", "post-rock",
              "synthpop", "new wave", "goth rock", "dance", "disco", "lounge", "instrumental"],
    "mood": ["dark", "melancholic", "sad", "emotional", "calm", "dreamy", "atmospheric", "epic",
             "intense", "aggressive", "rebellious", "introspective", "nostalgic", "hopeful",
             "uplifting", "romantic", "mysterious", "heavy", "gloomy", "meditative", "cinematic"],
    "instrument": ["guitar", "electric guitar", "acoustic guitar", "bass", "808 bass", "synth bass",
                   "drums", "drum machine", "piano", "synthesizer", "strings", "violin", "cello",
                   "organ", "saxophone", "brass", "flute", "harp", "vocals", "vocal samples", "choir"],
    "gender": ["male", "female", "human voice", "soprano", "mezzo-soprano", "tenor", "baritone",
               "child", "duet", "male and female", "unspecified"],
    "timbre": ["clear", "gravelly", "deep", "breathy", "husky", "raspy", "airy", "bright", "dark",
               "warm", "smooth", "rich", "powerful", "soft", "whispery", "gritty", "reverb", "raw",
               "mellow", "resonant", "shimmering"],
}


def vocabulary(engine_dir: str = "") -> dict[str, list[str]]:
    """The engine's own five tag axes, read from its `wav_top_200_tags.json` when it is here.

    `engine_dir` is the app folder the raw engine lives in. Best effort by design: a console without
    the file still gets the curated fallback rather than an empty picker.
    """
    import json as _json
    from pathlib import Path as _Path

    out = {k: list(v) for k, v in FALLBACK_VOCAB.items()}
    if not engine_dir:
        return out
    for name in ("wav_top_200_tags.json",):
        p = _Path(engine_dir) / name
        if not p.is_file():
            continue
        try:
            raw = _json.loads(p.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        if not isinstance(raw, dict):
            continue
        for axis, tags in raw.items():
            key = str(axis).strip().lower()
            if key not in out or not isinstance(tags, list):
                continue
            clean: list[str] = []
            for t in tags:
                t = " ".join(str(t).split()).strip().lower()
                # the file carries bilingual duplicates and a few junk rows; keep it readable
                if t and t != "none" and not any("\u4e00" <= ch <= "\u9fff" for ch in t) and t not in clean:
                    clean.append(t)
            if len(clean) >= 5:
                out[key] = clean
    return out


def convert(text: str) -> dict[str, Any]:
    """The operator's own template, re-cut into the shape the engine can actually sing.

    Answers the question "will YuE take the template I already use?" with the truth: not as written.
    `[VERSE 1]` is not a section to YuE (the tag must be one word), and lines like `ARTIST:` or
    `BEAT DIRECTION:` sitting among the words would be SUNG. So: markers are folded to single-word
    tags, and any `KEY: value` line outside a section is re-written as a `#` comment, which is read
    by the console (STYLE: can be lifted straight into the genre box) and never sung.
    """
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    looks_like_header = re.compile(r"^\s*([A-Z][A-Z0-9 _/&-]{2,24})\s*:(.*)$")
    out_lines: list[str] = []
    moved = 0
    in_section = False
    for ln in raw.split("\n"):
        if _MARKER.match(ln):
            in_section = True
            tag = _norm_tag(_MARKER.match(ln).group(1))
            out_lines.append(f"[{tag}]")
            continue
        m = looks_like_header.match(ln)
        if m and (not in_section or not ln.strip()):
            # Only outside a section: inside one, "I still love you" has no colon and a real title
            # might - so a header is only lifted when it looks like one AND carries no lyric shape.
            out_lines.append("# " + ln.strip())
            moved += 1
            continue
        out_lines.append(ln)
    body = "\n".join(out_lines)
    body = re.sub(r"\n{3,}", "\n\n", body).strip() + "\n"
    plan_after = plan(body)
    return {"ok": True, "text": body, "headers_moved": moved,
            "sections": plan_after["count"], "tags": [s2["tag"] for s2 in plan_after["sections"]],
            "style": header_fields(body).get("STYLE", ""), "plan": plan_after}


def validate(text: str) -> dict[str, Any]:
    """What is wrong with this sheet, before a render is spent on it. No engine call, no guessing."""
    problems: list[str] = []
    fixed: list[str] = []
    clean = strip_comments(text)
    words = clean.strip()
    if not words:
        problems.append("no lyrics: this engine sings the words it is given, so there is nothing to sing")
    for m in re.finditer(r"^\s*\[([^\]]{1,32})\]\s*$", text or "", re.MULTILINE):
        raw_tag = m.group(1)
        if not re.fullmatch(r"\w+", raw_tag.strip()):
            fixed.append(f"[{raw_tag}] -> [{_norm_tag(raw_tag)}] (the engine only sees a one-word tag)")
    stray = [ln.strip() for ln in clean.split("\n")
             if re.match(r"^\s*[A-Z][A-Z0-9 _/&-]{2,24}\s*:", ln) and ln.strip()]
    if stray:
        problems.append("these lines would be SUNG, not read: " + "; ".join(stray[:3])
                        + " - prefix them with # or press Convert")
    secs = sections(text)
    if words and not secs:
        problems.append("no sections: give the words a blank line between stanzas, or add [verse]/[chorus]")
    if len(secs) > MAX_SECTIONS:
        problems.append(f"{len(secs)} sections, but this engine sings at most {MAX_SECTIONS}")
    empty = [s["tag"] for s in secs if not s["lines"]]
    if empty:
        problems.append("section(s) with no lines: " + ", ".join(sorted(set(empty))))
    return {"ok": not problems, "problems": problems, "would_fix": sorted(set(fixed)),
            "sections": len(secs), "tags": [s2["tag"] for s2 in secs],
            "style": header_fields(text).get("STYLE", ""), "plan": plan(text)}


#: The writer's instruction with four fill-ins left open ({idea} {genre} {sections} {language}), so
#: the console's own page can compose it without restating the rules - the rules live here, once, next
#: to the engine contract they come from.
WRITE_FORMAT = """You are writing SONG LYRICS for a local music engine (YuE). Return ONLY the lyrics, in the exact shape below. No commentary, no markdown fences, no explanation, no title line.

Genre and production direction: {genre}
Language: {language}
Number of sections: exactly {sections}
Brief: {idea}

HARD RULES (the engine cannot do anything else):
1. Every section starts with a bracket tag on its own line, and the tag is ONE word, lower case: [intro] [verse] [prechorus] [chorus] [bridge] [instrumental] [outro].
2. Write [verse] and [chorus], and repeat the SAME chorus words every time the chorus comes back - a chorus that changes is not a chorus.
3. 4 to 8 short lines per section, one clause per line, singable in one breath.
4. No text before the first tag, no key: value labels, no stage directions, no notes about the music. Only tags and the words to be sung.
5. Concrete images, not adjectives. Keep one idea per line.

Shape to follow exactly (the number of sections is the one above):
[verse]
first verse line
first verse second line

[chorus]
chorus line repeated later
chorus second line"""

#: What the brief becomes when the operator gave the writer nothing to work from.
BRIEF_FALLBACK = "no idea given: invent the song yourself"


def write_instruction(idea: str = "", genre: str = "emo grunge / industrial metal", sections: int = 6,
                      language: str = "English") -> str:
    """The whole instruction, filled in. The very string a page sends to the model, no edits needed."""
    idea = " ".join(str(idea or "").split())
    genre = " ".join(str(genre or "").split())
    return WRITE_FORMAT.format(
        idea=f"the operator's idea: {idea}" if idea else BRIEF_FALLBACK,
        genre=genre or "the operator's own style",
        sections=max(2, min(MAX_SECTIONS, int(sections or 6))),
        language=" ".join(str(language or "English").split()) or "English",
    )


def singable(text: str) -> str:
    r"""Exactly what should be handed to the engine, built from the plan and nothing else.

    One source of truth, deliberately. The plan is what the estimate line counts, so building the
    payload anywhere else is how the two drift apart - and an earlier payload builder did drift: it
    recognised only bare `[verse]`-style tags, so a sheet written `[VERSE 1]` fell through to the
    untagged path and those tags would have been SUNG as lyrics. Comments are stripped, numbered
    tags fold onto their plain form, and the text ends in one newline so the closing section is not
    swallowed by the engine's own `(?=\[|\Z)` lookahead.
    """
    body = (text or "").strip()
    if not body:
        return ""
    out: list[str] = []
    for sec in plan(body)["sections"]:
        out.append(f"[{sec['tag']}]")
        out.extend(sec["lines"])
        out.append("")
    return "\n".join(out).strip("\n") + "\n"


def template_text() -> str:
    """The default sheet the console shows in the box: comment header, then singable sections."""
    return TEMPLATE
