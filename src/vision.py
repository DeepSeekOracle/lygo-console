"""What a picture costs the engine, and how to fit one into the window.

Two jobs, and both are needed because the console had neither:

  1. PRICE a picture. A projector is charged per patch of pixels, so a photo costs thousands of
     tokens and a phone photo tens of thousands. The console billed every image part a flat 900
     (`compaction.content_tokens`), so its own budget could not see a photo at all.
  2. FIT one. The newest picture is downscaled until it fits; older pictures are shed to `[image]`
     text rather than dragging the whole conversation out of the window; and a turn that still cannot
     fit is ANSWERED, in words, instead of being sent to an engine that will refuse it.

MEASURED on this host 2026-09-21, gemma4-12b booted with its projector
(`srv load_model: loaded multimodal model, '<MODELS>\\gemma4-12b-mmproj.gguf'`), 32,768-token
window, the operator's own console - the failure this module exists for:

    console:  [turn] blank answer: ... cur_text=0 chars                       <- an empty bubble
    engine:   E srv send_error: task id = 31, error: request (133868 tokens) exceeds the available
              context size (32768 tokens), try increasing it                 <- refused whole

and the cost of a picture, read off the engine's own prompt count for two turns that differ only in
the photo's size: a 256x256 picture and a 1024x1024 picture were charged
`IMAGE_TOKENS_PER_PIXEL` each (see that constant for the two numbers and the arithmetic).

No third-party import is needed to MEASURE a picture: PNG, JPEG, GIF and WebP headers are parsed here.
None is needed to SHRINK one on this box either - Pillow is used when it is installed and ffmpeg when
it is not, because the kit never installs either. With neither, the picture is not sent: the operator
is told in words, which is still better than an empty bubble.
"""
from __future__ import annotations

import base64
import io
import math
import os
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import Any

# ------------------------------------------------------------------------------------------------
# calibration
# ------------------------------------------------------------------------------------------------
# Text is billed exactly as compaction bills it, so the two estimates cannot drift apart.
CHARS_PER_TOKEN = 3.6

# A projector's cost follows the picture's AREA. Measured on this host 2026-09-21 (gemma4-12b, mmproj
# loaded) by differencing the engine's own prompt counts for two turns identical apart from the
# photo: 1024x1024 cost 8,998 tokens more than 256x256, over 983,040 more pixels = 0.00915 tokens per
# pixel. Rounded UP (0.0092) on purpose: over-estimating evicts a picture early, under-estimating
# sends a request the engine refuses and the operator gets an empty bubble.
IMAGE_TOKENS_PER_PIXEL = 0.0092
MIN_IMAGE_TOKENS = 16            # a projector pads a tiny picture to its patch grid; never charge zero


def image_tokens_for_side(side: int) -> int:
    """What a SQUARE picture of this side costs. One place owns the rule, so a cap, a budget and a
    limb cannot disagree about what fits."""
    n = max(0, int(side or 0))
    if n <= 0:
        return 0
    return max(MIN_IMAGE_TOKENS, int(round(n * n * IMAGE_TOKENS_PER_PIXEL)))

# The side a photo is capped at. The projector discards detail beyond its own working size, so capping
# here costs the model nothing it would have used - but it is what stops one 4000x5000 phone photo
# (about 184,000 tokens on the numbers above) from being sent into a 32,768-token window.
MAX_IMAGE_SIDE = 1024
MIN_IMAGE_SIDE = 64

# The vision LIMB boots its own engine with ubatch 4096 (`image_tools`), and a projector is processed
# with non-causal attention, which needs the whole picture inside one physical batch. So a picture sent
# to that limb is capped where 4096 tokens can carry it - 0.0092 tokens per pixel puts 640x640 at
# 3,768 tokens and 672x672 at 4,154 - rather than at the side the chat engine (measured to take a
# 14,732-token prompt without complaint) can afford.
LIMB_IMAGE_SIDE = 640

# The ladder a picture is walked down when the window is tight. Each step is a real re-encode.
SHRINK_LADDER = (MAX_IMAGE_SIDE, 768, 640, 512, 384, 256, 160, 96)

IMAGE_TYPES = {"image_url", "image"}


# ------------------------------------------------------------------------------------------------
# measuring - no dependency
# ------------------------------------------------------------------------------------------------
def _png_size(b: bytes) -> tuple[int, int] | None:
    if len(b) >= 24 and b[:8] == b"\x89PNG\r\n\x1a\n" and b[12:16] == b"IHDR":
        w, h = struct.unpack(">II", b[16:24])
        return int(w), int(h)
    return None


def _gif_size(b: bytes) -> tuple[int, int] | None:
    if len(b) >= 10 and b[:6] in (b"GIF87a", b"GIF89a"):
        w, h = struct.unpack("<HH", b[6:10])
        return int(w), int(h)
    return None


def _webp_size(b: bytes) -> tuple[int, int] | None:
    if len(b) < 30 or b[:4] != b"RIFF" or b[8:12] != b"WEBP":
        return None
    form = b[12:16]
    try:
        if form == b"VP8X":
            w = 1 + int.from_bytes(b[24:27], "little")
            h = 1 + int.from_bytes(b[27:30], "little")
            return w, h
        if form == b"VP8 ":
            w = int.from_bytes(b[26:28], "little") & 0x3FFF
            h = int.from_bytes(b[28:30], "little") & 0x3FFF
            return w, h
        if form == b"VP8L":
            bits = int.from_bytes(b[21:25], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    except Exception:
        return None
    return None


def _jpeg_size(b: bytes) -> tuple[int, int] | None:
    """Walk the JPEG segments to the frame header. Never raises on a truncated file."""
    if len(b) < 4 or b[:2] != b"\xff\xd8":
        return None
    i = 2
    n = len(b)
    while i + 9 < n:
        if b[i] != 0xFF:
            i += 1
            continue
        marker = b[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if marker == 0xDA:  # start of scan: the frame header is behind us
            return None
        try:
            seg = struct.unpack(">H", b[i + 2:i + 4])[0]
        except struct.error:
            return None
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            h, w = struct.unpack(">HH", b[i + 5:i + 9])
            return int(w), int(h)
        i += 2 + max(2, seg)
    return None


def image_size(raw: bytes) -> tuple[int, int] | None:
    """(width, height) of a picture from its header, or None when it is not one we can read.

    Deliberately dependency-free: this runs on the budget path, which must work even on a machine
    with no imaging library and no ffmpeg.
    """
    if not raw or len(raw) < 16:
        return None
    for parse in (_png_size, _jpeg_size, _gif_size, _webp_size):
        try:
            got = parse(raw)
        except Exception:
            got = None
        if got:
            return got
    return None


def image_tokens_for_size(size: tuple[int, int] | None) -> int:
    if not size:
        return MIN_IMAGE_TOKENS
    w, h = max(1, int(size[0])), max(1, int(size[1]))
    return max(MIN_IMAGE_TOKENS, int(math.ceil(w * h * IMAGE_TOKENS_PER_PIXEL)))


def data_url_bytes(url: str) -> bytes | None:
    """The bytes behind a data URL, or None when it is a link we would have to fetch (we do not)."""
    s = str(url or "")
    if not s.startswith("data:") or "," not in s:
        return None
    try:
        return base64.b64decode(s.split(",", 1)[1], validate=False)
    except Exception:
        return None


def data_url_mime(url: str, default: str = "image/png") -> str:
    s = str(url or "")
    if s.startswith("data:") and ";" in s:
        mime = s[5:s.index(";")]
        if mime:
            return mime
    return default


def image_parts(content: Any) -> list[dict]:
    if not isinstance(content, list):
        return []
    return [p for p in content if isinstance(p, dict) and p.get("type") in IMAGE_TYPES]


def est_image_tokens(part_or_content: Any) -> int:
    """Cost of ONE image part, or of the image parts in a content list."""
    if isinstance(part_or_content, list):
        return sum(est_image_tokens(p) for p in image_parts(part_or_content))
    if isinstance(part_or_content, dict) and part_or_content.get("type") in IMAGE_TYPES:
        holder = part_or_content.get("image_url") or part_or_content.get("image") or {}
        url = holder.get("url") if isinstance(holder, dict) else holder
        raw = data_url_bytes(str(url or ""))
        if raw is None:
            # A remote URL: we cannot measure it, and it is not our picture to fetch. Charge the cap,
            # which is what a photo of our own would cost at most - never zero.
            return image_tokens_for_size((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
        return image_tokens_for_size(image_size(raw))
    return 0


def est_text_tokens(content: Any) -> int:
    if isinstance(content, str):
        return int(len(content) / CHARS_PER_TOKEN) + 1
    if isinstance(content, list):
        return sum(int(len(str(p.get("text") or "")) / CHARS_PER_TOKEN) + 1
                   for p in content if isinstance(p, dict) and p.get("type") == "text") or 1
    if content is None:
        return 1
    return int(len(str(content)) / CHARS_PER_TOKEN) + 1


def est_message_tokens(message: dict) -> int:
    content = (message or {}).get("content")
    return est_text_tokens(content) + est_image_tokens(content)


def est_prompt_tokens(messages: list[dict], system_tokens: int = 0) -> int:
    """What the engine is about to be asked for, as closely as we can say without tokenizing.

    A picture is priced per patch of pixels instead of being called a 900-token message, which is the
    difference between knowing a turn will not fit and finding out from an empty bubble.
    """
    return int(system_tokens or 0) + sum(est_message_tokens(m) for m in (messages or []))


# ------------------------------------------------------------------------------------------------
# shrinking - Pillow when it is there, ffmpeg when it is not
# ------------------------------------------------------------------------------------------------
def _shrink_pillow(raw: bytes, max_side: int) -> bytes | None:
    try:
        from PIL import Image  # type: ignore
    except Exception:
        return None
    try:
        head = raw[:4]
        with Image.open(io.BytesIO(raw)) as im:
            im.load()
            im.thumbnail((max_side, max_side), Image.LANCZOS)
            out = io.BytesIO()
            if head[:2] == b"\xff\xd8":  # keep a photo a photo
                if im.mode not in ("RGB", "L"):
                    im = im.convert("RGB")
                im.save(out, format="JPEG", quality=88, optimize=True)
            else:
                if im.mode == "P":
                    im = im.convert("RGBA")
                im.save(out, format="PNG", compress_level=6)
            got = out.getvalue()
        return got or None
    except Exception:
        return None


def _shrink_ffmpeg(raw: bytes, max_side: int) -> bytes | None:
    exe = os.environ.get("LYGO_FFMPEG") or shutil.which("ffmpeg")
    if not exe:
        return None
    tmp = None
    try:
        tmp = Path(tempfile.mkdtemp(prefix="lygo_vision_"))
        src = tmp / "in.img"
        dst = tmp / "out.png"
        src.write_bytes(raw)
        cmd = [exe, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
               "-vf", "scale='min(iw,%d)':'min(ih,%d)':force_original_aspect_ratio=decrease" % (max_side, max_side),
               str(dst)]
        subprocess.run(cmd, capture_output=True, timeout=120)
        if dst.is_file() and dst.stat().st_size:
            return dst.read_bytes()
    except Exception:
        return None
    finally:
        if tmp is not None:
            try:
                shutil.rmtree(tmp, ignore_errors=True)
            except Exception:
                pass
    return None


def shrink_bytes(raw: bytes, max_side: int) -> bytes | None:
    """The same picture, no bigger than `max_side` on its longest side. None when we cannot."""
    size = image_size(raw)
    if not size:
        return None
    if max(size) <= max_side:
        return raw
    for shrink in (_shrink_pillow, _shrink_ffmpeg):
        got = shrink(raw, max_side)
        if got:
            new = image_size(got)
            if new and max(new) <= max_side:
                return got
    return None


def shrink_part(part: dict, max_side: int) -> dict | None:
    """A rewritten image part, or None when this picture cannot be made to fit."""
    holder = part.get("image_url") or part.get("image") or {}
    url = holder.get("url") if isinstance(holder, dict) else holder
    raw = data_url_bytes(str(url or ""))
    if raw is None:
        return None
    got = shrink_bytes(raw, max_side)
    if not got or got is raw:
        return None
    mime = "image/jpeg" if got[:2] == b"\xff\xd8" else ("image/gif" if got[:3] == b"GIF" else data_url_mime(str(url)))
    data_url = "data:" + mime + ";base64," + base64.b64encode(got).decode("ascii")
    if part.get("type") == "image":
        return {**part, "image": {"url": data_url}}
    return {**part, "image_url": {**(holder if isinstance(holder, dict) else {}), "url": data_url}}


def data_url_for_file(path: str | Path, max_side: int = MAX_IMAGE_SIDE) -> str | None:
    """Read a picture off disk as a data URL, fitted to what a projector can afford.

    The vision limb used to hand over whatever sat on disk: a 1400-pixel photo costs about 18,000
    tokens of the window before the question is even asked.
    """
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError:
        return None
    got = shrink_bytes(raw, max_side) or raw
    suffix = p.suffix.lower().lstrip(".")
    mime = "image/jpeg" if got[:2] == b"\xff\xd8" else ("image/" + (suffix or "png"))
    return "data:" + mime + ";base64," + base64.b64encode(got).decode("ascii")


# ------------------------------------------------------------------------------------------------
# fitting a whole turn
# ------------------------------------------------------------------------------------------------
def _replaced(messages: list[dict], at: int, content: Any) -> list[dict]:
    out = list(messages)
    out[at] = {**(out[at] or {}), "content": content}
    return out


def _shed(part: dict) -> dict:
    """An image part that was left out of the window, as something the model can still read."""
    why = str(part.get("_why") or "left out of the window")
    return {"type": "text", "text": "[image: " + why + "]"}


def fit_turn(
    messages: list[dict],
    window: int,
    system_tokens: int = 0,
    max_side: int = MAX_IMAGE_SIDE,
    reserve: int = 0,
) -> dict[str, Any]:
    """Fit one turn into `window`, newest picture first and conversation last.

    Order of sacrifice, cheapest first: the OLDEST pictures go before the newest one is touched, and
    a picture is only ever shrunk when it would not fit at its current size. Then oldest messages
    drop until the newest turn fits. Text and the roles of kept messages are preserved - a photo is
    replaced by `[image: ...]`, never by nothing, so the model still knows a picture was shown.

    Returns:
      messages - what to send
      changed  - anything was shed, shrunk, or trimmed
      over     - even the newest message alone does not fit; DO NOT SEND IT
      note     - plain words for the operator, empty when there is nothing to say
      shed / shrunk / trimmed / bytes - what happened, for the receipt
    """
    msgs: list[dict] = [dict(m) for m in (messages or []) if isinstance(m, dict)]
    room = max(1, int(window) - int(system_tokens or 0) - int(reserve or 0))
    shed = shrunk = 0
    shrunk_from: tuple[int, int] | None = None
    shrunk_to: tuple[int, int] | None = None

    # 1. every picture but the newest becomes text. A picture costs thousands of tokens of window;
    #    the words either side of it cost tens, and they are what the conversation is made of.
    spots: list[tuple[int, int]] = []
    for i, m in enumerate(msgs):
        content = m.get("content")
        if isinstance(content, list):
            for j, p in enumerate(content):
                if isinstance(p, dict) and p.get("type") in IMAGE_TYPES:
                    spots.append((i, j))
    for i, j in spots[:-1]:
        content = list(msgs[i].get("content") or [])
        part = content[j]
        size = image_size(data_url_bytes(str(((part.get("image_url") or {}).get("url")) or "")) or b"")
        where = ("%dx%d" % size) if size else "an attached picture"
        content[j] = _shed({**part, "_why": "%s, not re-sent with every later turn" % where})
        msgs = _replaced(msgs, i, content)
        shed += 1

    # 2. the newest picture: cap it, then walk it down until the turn fits.
    if spots:
        i, j = spots[-1]
        content = list(msgs[i].get("content") or [])
        part = content[j]
        holder = part.get("image_url") or part.get("image") or {}
        url = str((holder.get("url") if isinstance(holder, dict) else holder) or "")
        raw = data_url_bytes(url)
        before = image_size(raw) if raw else None
        tries = [max_side] + [s for s in SHRINK_LADDER if s < max_side]
        for side in tries:
            est = est_prompt_tokens(msgs, system_tokens) + int(reserve or 0)
            if est <= int(window):
                break
            new_part = shrink_part(part, side)
            if new_part is None:
                # Cannot make it smaller (no Pillow, no ffmpeg, or not our picture): leave it out.
                content[j] = _shed({**part, "_why": "too large for this model's window"})
                msgs = _replaced(msgs, i, content)
                shed += 1
                break
            content[j] = new_part
            msgs = _replaced(msgs, i, content)
            after = image_size(data_url_bytes(str(((new_part.get("image_url") or {}).get("url")) or "")) or b"")
            if after and after != before:
                shrunk = 1
                shrunk_from, shrunk_to = before, after
            if est_prompt_tokens(msgs, system_tokens) + int(reserve or 0) <= int(window):
                break

    used = est_prompt_tokens(msgs, system_tokens) + int(reserve or 0)
    trimmed = 0
    # 3. drop oldest conversation until the newest turn fits. Refuse only when that one
    #    message (plus identity/reserve) cannot fit any window this size.
    while used > int(window) and len(msgs) > 1:
        msgs = msgs[1:]
        trimmed += 1
        used = est_prompt_tokens(msgs, system_tokens) + int(reserve or 0)
    over = used > int(window)
    note = ""
    if over:
        note = ("This turn needs about %s tokens and this model's window holds %s, so I did not send it - "
                "the engine answers a request that big with nothing at all, which is how a turn comes "
                "back empty. Shorten the message (the attached picture counts), or start a new session "
                "with /new so the history starts from here." % ("{:,}".format(used), "{:,}".format(int(window))))
    elif shrunk and shrunk_from and shrunk_to:
        note = ("The attached picture was resized from %dx%d to %dx%d to fit this model's window."
                % (shrunk_from[0], shrunk_from[1], shrunk_to[0], shrunk_to[1]))
    elif trimmed:
        note = ("Older turns were dropped from this request so it would fit this model's window "
                "(%s of %s tokens)." % ("{:,}".format(used), "{:,}".format(int(window))))
    return {"messages": msgs, "changed": bool(shed or shrunk or trimmed), "over": over, "note": note,
            "shed": shed, "shrunk": shrunk, "trimmed": trimmed, "used_tokens": used, "window": int(window),
            "from": shrunk_from, "to": shrunk_to}
