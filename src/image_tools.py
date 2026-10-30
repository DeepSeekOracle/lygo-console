"""Image limbs: save, inspect, page thumbnail. No extra pip."""
from __future__ import annotations

import base64
import struct
from pathlib import Path
from typing import Any

from admin_map import read_roots
from registry import mmproj_for  # one rule for 'the projector that belongs to this record'

from paths import WORKSPACE, under_workspace
from web_tools import _blocked, _get


def _in_ws(p: Path) -> Path:
    """Keep a path inside the workspace, for uploads (name-only, never a disk path)."""
    try:
        p.resolve().relative_to(WORKSPACE.resolve())
        return p
    except ValueError:
        return WORKSPACE / p.name


def _allowed_read(p: Path) -> tuple[Path, str | None]:
    """(real_path, refusal) for a read target: the workspace plus the configured read roots.

    Absolute paths elsewhere on the PC are allowed when they sit under a mapped read root
    (config/admin.json -> read_roots) and are not a denied location, which is the same policy the
    other limbs use. Anything else is REFUSED with a reason that names the rule.

    This replaces a silent rewrite: image_info used to bounce an outside path into
    WORKSPACE/<name> and then report "missing", so a real picture on another drive looked absent
    and the model could only answer that the file does not exist.
    """
    from tools import _denied, _under, real_path  # lazy: tools.py is heavy and imports paths
    try:
        rp = real_path(p)
    except Exception:
        return p, "bad_path"
    if _denied(rp):
        return rp, "denied_path"
    roots: list[Path] = [WORKSPACE]
    for extra in tuple(read_roots() or ()):
        try:
            roots.append(Path(extra))
        except (TypeError, ValueError):
            continue
    if _under(rp, tuple(roots)):
        return rp, None
    return rp, "outside_read_roots"


def image_info(path: str) -> dict[str, Any]:
    p, why = _allowed_read(under_workspace(path))
    if why:
        return {"ok": False, "error": why, "path": str(p),
                "read_roots": [str(x) for x in (read_roots() or ())]}
    if not p.is_file():
        return {"ok": False, "error": "missing", "path": str(p)}
    raw = p.read_bytes()[:32]
    kind = "unknown"
    w = h = 0
    if raw[:8] == b"\x89PNG\r\n\x1a\n" and len(raw) >= 24:
        kind = "png"
        w, h = struct.unpack(">II", raw[16:24])
    elif raw[:2] == b"\xff\xd8":
        kind = "jpeg"
    elif raw[:6] in (b"GIF87a", b"GIF89a"):
        kind = "gif"
        if len(raw) >= 10:
            w, h = struct.unpack("<HH", raw[6:10])
    return {"ok": True, "path": str(p), "kind": kind, "bytes": p.stat().st_size, "width": w, "height": h}


VISION_PORT_DEFAULT = 11461  # a spare port: our own runner, so the chat engine is never disturbed


def vision_record() -> dict[str, Any] | None:
    """The registered model that carries a projector (mmproj): OUR OWN engine is what runs it.

    Zero foreign daemons. The kit boots llama-server itself (engine.spawn_runner -> --mmproj) and this machine's
    registry already carries gemma4:12b with its mmproj blob, so vision needs no daemon, no keep-alive
    setting and no second vendor: the projector GGUF is just another file the engine is pointed at.
    """
    import json

    reg = Path(__file__).resolve().parents[1] / "save" / "registry.json"
    try:
        data = json.loads(reg.read_text(encoding="utf-8"))
    except Exception:
        return None
    import registry as _registry

    for rec in data.get("models") or []:
        gguf = rec.get("path") or rec.get("gguf") or rec.get("file")
        if not gguf:
            continue
        # registry.mmproj_for finds a projector the record does not name but which sits beside the model,
        # so a vision GGUF scanned straight off a disk is not read as text-only.
        mm = _registry.mmproj_for(rec)
        if not mm:
            continue
        try:
            if mm.is_file() and Path(str(gguf)).is_file():
                return rec
        except OSError:
            continue
    return None


def vision_port() -> int:
    import os

    try:
        return int(os.environ.get("LYGO_VISION_PORT") or VISION_PORT_DEFAULT)
    except ValueError:
        return VISION_PORT_DEFAULT


def chat_port() -> int:
    import os

    try:
        from paths import LLAMA_PORT

        return int(os.environ.get("LYGO_LLAMA_PORT") or LLAMA_PORT)
    except Exception:
        try:
            return int(os.environ.get("LYGO_LLAMA_PORT") or 11441)
        except ValueError:
            return 11441


def _engine_key() -> str:
    """The key our own engine runs with (data/.llama_api_key). Read, never echoed."""
    try:
        from paths import LLAMA_KEY_PATH

        return LLAMA_KEY_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _engine_up(port: int) -> bool:
    import urllib.request

    try:
        req = urllib.request.Request("http://127.0.0.1:" + str(port) + "/v1/models")
        key = _engine_key()
        if key:
            req.add_header("Authorization", "Bearer " + key)
        with urllib.request.urlopen(req, timeout=5):
            return True
    except Exception:
        return False


def _ask_engine(port: int, alias: str, image: Path, prompt: str, timeout: int) -> dict[str, Any]:
    """One picture, one question, straight at a llama-server booted with --mmproj (our own binary)."""
    import json
    import time
    import urllib.error
    import urllib.request

    # The picture is FITTED before it is sent. This limb boots its own engine with ubatch 4096, and
    # a projector is charged per patch of pixels, so a raw 1400-pixel photo is both ~18,000 tokens of
    # window and past the batch the runner was booted with - measured 2026-09-19: a 1024x1536 photo
    # crashed the runner mid-request. `vision` owns the rule; this cap is what that ubatch carries.
    import vision

    data_url = vision.data_url_for_file(image, max_side=vision.LIMB_IMAGE_SIDE)
    if not data_url:
        return {"ok": False, "error": "image_unreadable", "path": str(image),
                "hint": "the file could not be read as a picture (or resized without Pillow/ffmpeg); "
                        "nothing was sent to the engine"}
    heads = {"Content-Type": "application/json"}
    key = _engine_key()
    if key:
        heads["Authorization"] = "Bearer " + key
    # llama.cpp's server takes the dict shape only. The plain-string form comes back 500
    # "Invalid base64 value", and because that second attempt overwrote the first result it threw away a
    # good description - measured 2026-09-19: 300 generated tokens discarded by the retry. One shape.
    shapes = ({"type": "image_url", "image_url": {"url": data_url}},)
    t0 = time.time()
    last: dict[str, Any] = {}
    for shape in shapes:
        body = {
            "model": alias,
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}, shape]}],
            "max_tokens": 420,
            "temperature": 0.2,
            # gemma4 answers a photo request in its REASONING channel, so with the template default the
            # whole budget goes there and `content` comes back empty (finish_reason "length", 26 s wasted).
            # Measured 2026-09-19 on our own runner: the same question with thinking disabled returns the
            # description in 9.7 s, finish_reason "stop". This is llama.cpp's own request knob - no daemon.
            "chat_template_kwargs": {"enable_thinking": False},
        }
        req = urllib.request.Request(
            "http://127.0.0.1:" + str(port) + "/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers=heads,
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as fh:
                out = json.loads(fh.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            last = {"ok": False, "error": "vision_http", "http": exc.code, "port": port}
            continue
        except Exception as exc:
            err = "vision_timeout" if "timeout" in type(exc).__name__.lower() else "engine_unreachable"
            return {"ok": False, "error": err, "port": port, "why": type(exc).__name__}
        try:
            text = (out["choices"][0]["message"]["content"] or "").strip()
        except Exception:
            text = ""
        if text:
            return {"ok": True, "path": str(image), "model": alias, "port": port,
                    "seconds": round(time.time() - t0, 1), "text": text, "bytes": image.stat().st_size}
        reason = str((out.get("choices") or [{}])[0].get("message", {}).get("reasoning_content") or "")
        last = {"ok": False, "error": "vision_thinking_only" if reason else "vision_empty",
                "port": port, "thinking_chars": len(reason),
                "finish": (out.get("choices") or [{}])[0].get("finish_reason"),
                "hint": "the model spent its budget in the reasoning channel; keep enable_thinking false "
                        "or raise max_tokens"}
    return last


def _selected_id() -> str:
    import json

    reg = Path(__file__).resolve().parents[1] / "save" / "registry.json"
    try:
        return str(json.loads(reg.read_text(encoding="utf-8")).get("selected") or "")
    except Exception:
        return ""


def image_see(path: str, prompt: str | None = None, timeout: int = 180) -> dict[str, Any]:
    """Describe a picture with OUR OWN engine - llama-server booted with --mmproj. No daemon, no vendor.

    image_info only reports kind/size/dimensions, so "check this photo" cannot be answered from it.
    Order of preference:
      1. the console's own engine when the model it runs IS the registered vision model - no extra
         process and no extra VRAM;
      2. otherwise a runner on LYGO_VISION_PORT (default 11461) via engine.spawn_runner, stopped again
         the moment the answer is in, because one engine at a time is the rule on an 8 GB card;
      3. otherwise an honest refusal naming the knob - never an invented description.
    """
    import os
    import time

    q = (
        prompt
        or "Describe this image in 3-5 sentences: what it shows, what is visible, and any text you can read."
    ).strip()
    p, why = _allowed_read(under_workspace(path))
    if why:
        return {"ok": False, "error": why, "path": str(p),
                "read_roots": [str(x) for x in (read_roots() or ())]}
    if not p.is_file():
        return {"ok": False, "error": "missing", "path": str(p)}
    rec = vision_record()
    if not rec:
        return {"ok": False, "error": "no_vision_model", "path": str(p),
                "hint": "no registered model carries an mmproj projector; register a vision GGUF with its mmproj"}
    alias = str(rec.get("id") or rec.get("alias") or "vision")
    cp = chat_port()
    if _engine_up(cp):
        if _selected_id() == str(rec.get("id") or ""):
            return _ask_engine(cp, alias, p, q, timeout)
        return {"ok": False, "error": "vision_engine_busy", "path": str(p), "model": alias, "port": cp,
                "hint": "the chat engine is holding the card with a model that has no projector; boot "
                        + alias + " as the console brain, then ask again"}

    try:
        from lygo_engine import plan as _plan

        pl = _plan(rec) or {}
    except Exception:
        pl = {}
    lg = pl.get("llama") if isinstance(pl.get("llama"), dict) else {}
    ctx = min(int(lg.get("ctx") or 8192), 8192)  # one picture + a few sentences: no chat-sized window
    ngl = int(lg.get("ngl") or 99)
    threads = lg.get("threads")
    kv_type = str(lg.get("kv_type") or "")
    flash_attn = bool(lg.get("flash_attn"))
    mmap = bool(lg.get("mmap", True))
    port = vision_port()
    from engine import spawn_runner, stop_port  # our own binary: engine/llama-server.exe

    try:
        spawn_runner(
            port=port,
            gguf=Path(str(rec.get("path") or rec.get("gguf") or rec.get("file"))),
            kind="chat",
            mmproj=mmproj_for(rec) or Path(str(rec.get("mmproj"))),
            ctx=ctx,
            ngl=ngl,
            alias=alias,
            api_key=_engine_key(),
            threads=threads,
            mmap=mmap,
            flash_attn=flash_attn,
            kv_type=kv_type,
            ctx_max=8192,
            # A projector is processed with NON-CAUSAL attention, which requires n_ubatch >= the whole
            # image-token batch. The text defaults are too small and llama.cpp dies with
            # "non-causal attention requires n_ubatch >= n_tokens" (llama-context.cpp assert) - measured
            # 2026-09-19 on a 1024x1536 photo, which crashed the runner mid-request.
            batch=4096,
            ubatch=4096,
        )
    except MemoryError as exc:
        return {"ok": False, "error": "vision_needs_ram", "model": alias, "why": str(exc)[:300],
                "hint": "the RAM gate refused the vision model; boot it as the console brain instead"}
    except Exception as exc:
        return {"ok": False, "error": "vision_boot_failed", "model": alias,
                "why": type(exc).__name__ + ": " + str(exc)[:300]}
    try:
        t0 = time.time()
        ready = False
        while time.time() - t0 < 180:
            if _engine_up(port):
                ready = True
                break
            time.sleep(3)
        if not ready:
            return {"ok": False, "error": "vision_boot_timeout", "model": alias, "port": port,
                    "seconds": round(time.time() - t0, 1)}
        out = _ask_engine(port, alias, p, q, timeout)
        out["booted"] = True
        return out
    finally:
        try:
            stop_port(port)
        except Exception:
            pass


def image_save(b64: str, path: str | None = None) -> dict[str, Any]:
    s = (b64 or "").strip()
    if "," in s and s.lower().startswith("data:"):
        s = s.split(",", 1)[1]
    try:
        data = base64.b64decode(s)
    except Exception:
        return {"ok": False, "error": "bad_base64"}
    if len(data) > 8_000_000:
        return {"ok": False, "error": "too_large"}
    dest = WORKSPACE / "images" / (path or "upload.bin")
    dest = _in_ws(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    info = image_info(str(dest))
    info["saved"] = True
    return info


def image_list() -> dict[str, Any]:
    root = WORKSPACE / "images"
    root.mkdir(parents=True, exist_ok=True)
    names = [p.name for p in root.iterdir() if p.is_file()][:80]
    return {"ok": True, "dir": str(root), "files": names}


def page_thumbnail(url: str) -> dict[str, Any]:
    why = _blocked(url)
    if why:
        return {"ok": False, "error": why}
    shot = "https://image.thum.io/get/width/1024/noanimate/" + url
    code, raw, ctype = _get(shot)
    if code != 200 or not raw or len(raw) < 80:
        return {"ok": False, "error": f"thumb_{code}", "ctype": ctype}
    dest = WORKSPACE / "images" / "page-thumb.jpg"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)
    return {"ok": True, "path": str(dest), "bytes": len(raw), "source": shot, "class": "RESOURCE"}
