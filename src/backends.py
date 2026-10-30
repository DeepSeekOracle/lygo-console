"""LYGO Backends — which engine build actually runs on this host.

The shipped engine is CPU-only on purpose: the stick must boot on every PC. A GPU
backend is an *added* engine build kept in ``engine/backends/<name>/`` and it is only
ever applied after it has been proven on this host by a real model load — because a
broken GPU stack fails inside the driver itself and takes the whole engine process down
(exit 0xC0000005, at any ngl, 0 included). No proof, no activation.

Every verdict is remembered per host and keyed by the backend's own files, so re-fetching
a backend or moving the stick to another PC is retried on its own.

Store layout (git-ignored, never in the public SKU)::

    engine/backends/vulkan/ggml-vulkan.dll     overlay: copied onto engine/
    engine/backends/cuda/llama-server.exe ...  engine : a complete engine build
    engine/backends/<name>/backend.json        manifest: kind, tag, files, sha256

Two kinds, detected from the files present:
    overlay — ``ggml-<name>*.dll`` and no ``llama-server.exe``; applied onto ``engine/``
    engine  — a full engine build; used as the engine dir, nothing is copied

Nothing here changes weights, precision or semantics: only where they are computed.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any

import atomicio
from paths import DATA, ENGINE_DIR, LOGS

BACKENDS_DIR = ENGINE_DIR / "backends"
MANIFEST_NAME = "backend.json"
ACTIVE_JSON = DATA / "perf_active.json"
SIGNATURE = "Δ9Φ963-LYGO-BACKENDS-v1"
PREFERENCE = ("cuda", "vulkan", "sycl", "hip", "opencl", "kompute", "metal")
NVIDIA_HINTS = ("nvidia", "geforce", "rtx", "quadro", "tesla", "cuda")
ENGINE_MARKERS = ("llama-server.exe", "llama.dll", "ggml.dll")
SELFTEST_PORT = int(os.environ.get("LYGO_SELFTEST_PORT") or 11471)
SELFTEST_TIMEOUT_S = 90
SELFTEST_CTX = 2048
SELFTEST_MAX_MODEL_BYTES = 4 * 1024**3  # never self-test with a giant model
# A chat model is required to prove a GPU: an embedding model at -ngl 99 with a chat
# alias dies with 0xC0000409 (nomic-embed-text, 2026-09-18), and a crash is a remembered
# verdict — one bad probe model would disable a working GPU for good.
PROBE_SKIP_TOKENS = ("embed", "rerank", "bge-", "clip", "whisper", "mmproj", "projector", "draft")
FULL_LAYERS = 99

# Crash codes worth naming in a receipt instead of a raw number.
# A remembered verdict is evidence about a MOMENT, not a life sentence. Measured 2026-09-20: both GPU
# backends were self-tested at 21:08-21:09 while a sweep of ours held the card - cuda died "llama-server
# exited 1" after 41.9 s and vulkan crashed three probe loads with 0xC0000005 - and a verdict keyed by the
# backend's own files is remembered until those files change. A busy card must never be able to lock a
# working GPU build out of the console for good.
VERDICT_RETRY_AFTER_S = 6 * 3600
# A self-test loads a small chat model at FULL_LAYERS plus its cache. Below this much free VRAM the test
# would measure contention rather than the backend, so it is not run: nothing is proved, nothing condemned.
SELFTEST_MIN_FREE_MIB = 3072

CRASH_NAMES = {
    3221225477: "0xC0000005 access violation",
    3221225725: "0xC00000FD stack overflow",
    3221225781: "0xC0000135 missing dependency",
    3221225794: "0xC0000142 dll init failure",
    3221226505: "0xC0000409 stack buffer overrun",
}
_MEMO: dict[str, Any] = {}


# ---------------------------------------------------------------- store

def base_engine_dir() -> Path:
    return Path(ENGINE_DIR)


def backend_dirs() -> dict[str, Path]:
    """Installed backend directories, name -> path. Unreadable store means no backends."""
    root = Path(BACKENDS_DIR)
    out: dict[str, Path] = {}
    try:
        if not root.is_dir():
            return out
        for child in sorted(root.iterdir()):
            if child.is_dir() and not child.name.startswith((".", "_")):
                out[child.name.lower()] = child
    except OSError:
        return out
    return out


def backend_kind(where: Any = None, name: str = "") -> str:
    """'engine' when the dir is a complete engine build, 'overlay' when it is DLLs for one."""
    d = Path(where) if where else None
    if d is None or not d.is_dir():
        return ""
    try:
        names = {f.name.lower() for f in d.iterdir() if f.is_file()}
    except OSError:
        return ""
    if all(m in names for m in ("llama-server.exe", "llama.dll")):
        return "engine"
    key = (name or d.name).lower()
    if any(n.startswith(f"ggml-{key}") for n in names):
        return "overlay"
    if any(n.startswith("ggml-") and n.endswith(".dll") for n in names):
        return "overlay"
    return ""


def _probe_label(model: Any) -> str:
    """The operator's name for a probe model - never its filename on disk.

    A CAS blob is named sha256-<64 hex>, which tells the reader nothing about WHICH model crashed and
    is indistinguishable from a leaked hash (the envwatch panel forbids that shape on principle). The
    registry knows the name; when it does not, a redacted short form is still honest.
    """
    p = str(model or "")
    try:
        import atomicio  # noqa: PLC0415

        for base in (Path(DATA).parent / "save", Path(DATA)):
            reg = base / "registry.json"
            if not reg.is_file():
                continue
            doc = json.loads(atomicio.read_text(reg, errors="replace") or "{}")
            for rec in (doc.get("models") if isinstance(doc, dict) else doc) or []:
                if str(rec.get("path") or "") == p:
                    return str(rec.get("id") or "") or Path(p).name
    except Exception:  # noqa: BLE001 - the label is a courtesy; never fail the probe over it
        pass
    name = Path(p).name
    return re.sub(r"[0-9A-Fa-f]{16,}", "[hash]", name) or "a model file"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def _manifest(where: Path) -> dict[str, Any]:
    try:
        # PowerShell's Set-Content writes a BOM by default: strip it rather than
        # discard the whole manifest (which would silently lose the verdict key).
        raw = atomicio.read_text(where / MANIFEST_NAME, errors="replace").lstrip("\ufeff")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def backend_files(where: Path, kind: str = "") -> list[str]:
    """The files this backend owns — overlay: its backend DLLs; engine: its executables."""
    try:
        names = sorted(f.name for f in where.iterdir() if f.is_file())
    except OSError:
        return []
    if kind == "engine":
        return [n for n in names if not n.lower().endswith(".gguf")]
    dlls = [n for n in names if n.lower().startswith("ggml-") and n.lower().endswith(".dll")]
    if dlls:
        return dlls
    return [n for n in names if n.lower().endswith(".dll") and n.lower() != "ggml-cpu-x64.dll"]


def backend_info(name: str, *, deep: bool = False) -> dict[str, Any]:
    """Everything the planner needs about one backend: kind, files, size, integrity."""
    d = backend_dirs().get(name.lower())
    if d is None:
        return {"name": name, "present": False, "kind": "", "bytes": 0, "files": {}}
    kind = backend_kind(d, name)
    man = _manifest(d)
    files = backend_files(d, kind)
    total = 0
    hashes: dict[str, str] = {}
    verified = True
    for f in files:
        p = d / f
        try:
            st = p.stat()
        except OSError:
            verified = False
            continue
        total += st.st_size
        hashes[f] = f"{st.st_size}:{st.st_mtime_ns}"
        if deep:
            want = (man.get("files") or {}).get(f)
            got = _sha256(p)
            hashes[f] = got
            if want and want != got:
                verified = False
    return {
        "name": name.lower(),
        "present": kind != "",
        "kind": kind,
        "dir": str(d),
        "files": hashes,
        "bytes": total,
        "tag": str(man.get("tag") or ""),
        "created": str(man.get("created") or ""),
        "verified": verified and kind != "",
        "manifest": bool(man),
    }


def backend_key(name: str, info: dict[str, Any]) -> str:
    """Verdict identity: changes when the backend's files, size or tag change."""
    # created is in the key on purpose: a re-fetch is a new backend and earns a new test.
    parts = [name.lower(), str(info.get("kind") or ""), str(info.get("tag") or ""),
             str(info.get("created") or ""), str(info.get("bytes") or 0)]
    parts += [f"{k}={v}" for k, v in sorted((info.get("files") or {}).items())]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def installed(*, deep: bool = False) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name in backend_dirs():
        info = backend_info(name, deep=deep)
        if info.get("present") and info.get("verified"):
            out[name] = info
    return out


def host_looks_nvidia() -> bool:
    """Cheap vendor hint for ordering: the NVIDIA tools ship with the driver."""
    try:
        return bool(shutil.which("nvidia-smi"))
    except Exception:
        return False


def order_candidates(store: dict[str, dict[str, Any]], devices: Any = None) -> list[str]:
    """Which backend to try first here: CUDA on an NVIDIA device, else the usual order."""
    names = list(store)
    blob = " ".join(str((d or {}).get("name") or "") for d in (devices or [])).lower()
    nvidia = any(h in blob for h in NVIDIA_HINTS) or host_looks_nvidia()
    rank = {n: i for i, n in enumerate(PREFERENCE)}
    if nvidia:
        rank["cuda"] = -1
    else:
        rank["cuda"] = len(PREFERENCE)  # no NVIDIA device in sight: try it last
    return sorted(names, key=lambda n: (rank.get(n, len(PREFERENCE)), n))


# ---------------------------------------------------------------- activation

def _active_read() -> dict[str, Any]:
    try:
        data = json.loads(atomicio.read_text(ACTIVE_JSON, errors="replace"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def active_record() -> dict[str, Any]:
    """The backend currently applied to the engine dir, as last written by ensure()."""
    return _active_read()


def set_active(backend: str, engine_dir: Any, *, host: str = "", reason: str = "",
               detail: str = "") -> dict[str, Any]:
    rec = {
        "backend": str(backend),
        "engine_dir": str(engine_dir),
        "host": host,
        "reason": reason,
        "detail": detail,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "signature": SIGNATURE,
    }
    try:
        ACTIVE_JSON.parent.mkdir(parents=True, exist_ok=True)
        atomicio.atomic_write_text(ACTIVE_JSON, json.dumps(rec, indent=2, sort_keys=True) + "\n")
    except OSError:
        pass
    return rec


def clear_active() -> None:
    try:
        if ACTIVE_JSON.exists():
            ACTIVE_JSON.unlink()
    except OSError:
        pass


def activate(name: str, info: dict[str, Any]) -> dict[str, Any]:
    """Make a backend usable. Overlay: copy its DLLs into engine/. Engine: nothing to copy."""
    out = {"backend": name, "activated": False, "detail": ""}
    if not info.get("present"):
        out["detail"] = "not_installed"
        return out
    if info.get("kind") == "engine":
        out["activated"] = True
        out["detail"] = "engine_build_in_place"
        return out
    src = Path(info.get("dir") or "")
    dst = base_engine_dir()
    try:
        dst.mkdir(parents=True, exist_ok=True)
        for fname in backend_files(src, "overlay"):
            src_f = src / fname
            dst_f = dst / fname
            if dst_f.exists() and dst_f.stat().st_size == src_f.stat().st_size:
                continue
            tmp = dst_f.with_suffix(dst_f.suffix + ".incoming")
            shutil.copy2(src_f, tmp)
            os.replace(tmp, dst_f)
        out["activated"] = True
    except OSError as exc:
        out["detail"] = f"{type(exc).__name__}: {exc}"
    return out


def deactivate(name: str, info: dict[str, Any] | None = None) -> dict[str, Any]:
    """Undo activate() for an overlay: remove exactly the files the backend owns.

    Falls back to the file-name pattern when the store entry is gone, because a leftover
    ggml-<name>*.dll in engine/ outliving its backend is precisely the state that crashes
    the engine on a bad driver (nvoglv64.dll, 2026-09-18, at ngl 0 included).
    """
    info = info or backend_info(name)
    out: dict[str, Any] = {"backend": name, "deactivated": False, "detail": "", "removed": []}
    src = Path(info.get("dir") or "")
    names = backend_files(src, "overlay") if info.get("kind") == "overlay" else []
    if not names:
        try:
            names = [p.name for p in sorted(base_engine_dir().glob(f"ggml-{str(name).lower()}*.dll"))]
        except OSError:
            names = []
    if not names:
        out["deactivated"] = True
        out["detail"] = "nothing_to_remove"
        return out
    for fname in names:
        f = base_engine_dir() / fname
        try:
            if f.is_file():
                f.unlink()
                out["removed"].append(fname)
        except OSError as exc:
            out["detail"] = f"{type(exc).__name__}: {exc}"
    out["deactivated"] = True
    return out


SHIPPED_ENGINE_FILES = ("ggml.dll", "ggml-base.dll")
SHIPPED_ENGINE_PREFIXES = ("ggml-cpu", "ggml-rpc")


def applied_overlays() -> list[str]:
    """Backends with files sitting in engine/ — by store manifest and by file pattern.

    The pattern branch matters: a GPU DLL whose store entry was deleted is exactly the
    orphan that crashes the engine on a bad driver, and it must still be findable so it
    can be removed. Only known backend names are claimed; the shipped engine files are not.
    """
    out: list[str] = []
    for name, d in backend_dirs().items():
        kind = backend_kind(d, name)
        if kind != "overlay":
            continue
        try:
            if all((base_engine_dir() / f).is_file() for f in backend_files(d, "overlay")):
                out.append(name)
        except OSError:
            continue
    try:
        for f in base_engine_dir().glob("ggml-*.dll"):
            low = f.name.lower()
            if low in SHIPPED_ENGINE_FILES or low.startswith(SHIPPED_ENGINE_PREFIXES):
                continue
            token = low[len("ggml-"):].split("-")[0].replace(".dll", "")
            if token in PREFERENCE and token not in out:
                out.append(token)
    except OSError:
        pass
    return sorted(set(out))


# ---------------------------------------------------------------- verdicts

def verdict_store_path() -> Path:
    from perf import PERF_JSON  # shared store: one file to inspect, delete or ship

    return Path(PERF_JSON)


def backend_verdict(host: str, name: str) -> dict[str, Any] | None:
    try:
        data = json.loads(atomicio.read_text(verdict_store_path(), errors="replace"))
        rec = ((data.get("backends") or {}).get(str(host)) or {}).get(name.lower())
        return rec if isinstance(rec, dict) else None
    except (OSError, ValueError, ImportError):
        return None


def remember_backend(host: str, name: str, **fields: Any) -> dict[str, Any]:
    """Record one backend's verdict for one host. Atomic, bounded, never raises."""
    path = verdict_store_path()
    try:
        data = json.loads(atomicio.read_text(path, errors="replace"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError, ImportError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    hosts = data.get("backends")
    if not isinstance(hosts, dict):
        hosts = {}
    per_host = hosts.get(str(host))
    if not isinstance(per_host, dict):
        per_host = {}
    known = {k: v for k, v in (per_host.get(name.lower()) or {}).items()
             if isinstance(v, (str, int, float, bool))}
    entry = {**known, **fields, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    per_host[name.lower()] = entry
    hosts[str(host)] = per_host
    data["backends"] = hosts
    data["signature"] = "Δ9Φ963-LYGO-BACKENDS-v1"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomicio.atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")
    except OSError:
        pass
    return entry


# ---------------------------------------------------------------- self-test

def _health_ok(port: int, timeout: float = 0.75) -> bool:
    """Did this self-test engine answer?

    MEASURED 2026-09-25: the caller is a wait loop (poll, then sleep 1 s), so a per-attempt budget only
    has to cover a loopback /health answer - at 2.0 s a closed port cost that loop 2,000 ms per
    iteration. How long the loop waits in total is still its own `timeout`.
    """
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as r:
            return int(getattr(r, "status", 0) or 0) == 200
    except Exception:
        return False


def _port_free(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.4):
            return False
    except Exception:
        return True


def self_test(engine_dir: Any, model_path: Any, *, ngl: int = FULL_LAYERS, threads: int = 8,
              timeout: int = SELFTEST_TIMEOUT_S, port: int | None = None,
              backend: str = "") -> dict[str, Any]:
    """Load a real model with this backend and see whether the process survives it.

    The self-test runs in its own process on its own port: a driver crash costs one
    attempt, never the console. Returns {ok, rc, seconds, detail, log}.
    """
    d = Path(engine_dir)
    exe = d / "llama-server.exe"
    model = Path(model_path) if model_path else None
    if not exe.is_file():
        return {"ok": False, "rc": None, "seconds": 0.0, "detail": "engine_binary_missing"}
    if model is None or not model.is_file():
        return {"ok": False, "rc": None, "seconds": 0.0, "detail": "no_probe_model"}
    use_port = int(port or SELFTEST_PORT)
    if not _port_free(use_port):
        use_port = use_port + 7
    log = Path(LOGS) / f"selftest-{backend or d.name}.log"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        fh = open(log, "wb")
    except OSError:
        fh = open(os.devnull, "wb")
    cmd = [str(exe), "-m", str(model), "--host", "127.0.0.1", "--port", str(use_port),
           "-c", str(SELFTEST_CTX), "-ngl", str(int(ngl)), "-t", str(int(threads)),
           "-np", "1", "--jinja", "--alias", "selftest"]
    t0 = time.time()
    try:
        proc = subprocess.Popen(cmd, cwd=str(d), stdout=fh, stderr=subprocess.STDOUT)
    except OSError as exc:
        try:
            fh.close()
        except OSError:
            pass
        return {"ok": False, "rc": None, "seconds": 0.0, "detail": f"spawn_failed: {exc}"}
    ok = False
    try:
        while (time.time() - t0) < timeout:
            if proc.poll() is not None:
                break
            if _health_ok(use_port):
                ok = True
                break
            time.sleep(1.0)
    finally:
        rc = proc.poll()
        if rc is None:
            try:
                proc.kill()
                proc.wait(timeout=15)
            except (OSError, subprocess.SubprocessError):
                pass
            rc = proc.poll()
        try:
            fh.close()
        except OSError:
            pass
    seconds = round(time.time() - t0, 1)
    detail = "model_load_ok" if ok else (CRASH_NAMES.get(int(rc or 0), f"exit {rc}") if rc is not None
                                         else "timed_out")
    return {"ok": ok, "rc": rc, "seconds": seconds, "detail": detail, "log": str(log),
            "port": use_port, "ngl": int(ngl), "threads": int(threads)}


def probe_models(models: Any) -> list[Path]:
    """Smallest-first model candidates a self-test may use, skipping giants."""
    out: list[tuple[int, Path]] = []
    for m in models or []:
        p = Path(m.get("path") if isinstance(m, dict) else m or "")
        size = int((m or {}).get("bytes") or 0) if isinstance(m, dict) else 0
        if not size:
            try:
                size = p.stat().st_size
            except OSError:
                continue
        ident = " ".join(str((m or {}).get(k) or "") for k in ("id", "name", "path")) \
            if isinstance(m, dict) else str(m or "")
        if any(tok in ident.lower() for tok in PROBE_SKIP_TOKENS):
            continue
        if not p.is_file() or size <= 0 or size > SELFTEST_MAX_MODEL_BYTES:
            continue
        out.append((size, p))
    seen: set[str] = set()
    ordered: list[Path] = []
    for _, p in sorted(out, key=lambda t: t[0]):
        key = str(p).lower()
        if key not in seen:
            seen.add(key)
            ordered.append(p)
    return ordered[:3]


# ---------------------------------------------------------------- the decision

def engine_dir_for(name: str, info: dict[str, Any]) -> Path:
    """Where llama-server.exe lives when this backend is active."""
    if info.get("kind") == "engine":
        return Path(info.get("dir") or "")
    return base_engine_dir()


def _verdict_age_s(verdict: dict[str, Any]) -> float:
    """Seconds since a verdict was recorded; inf when the stamp is missing or unreadable."""
    try:
        when = datetime.datetime.fromisoformat(str((verdict or {}).get("at") or ""))
    except (TypeError, ValueError):
        return float("inf")
    return max(0.0, (datetime.datetime.now() - when).total_seconds())


def _foreign_engine_running() -> bool:
    """A llama-server is already up - the console's own, the steward's, or a tool's. The card is in use."""
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq llama-server.exe", "/FO", "CSV", "/NH"],
                           capture_output=True, text=True, timeout=15,
                           creationflags=0x08000000 if os.name == "nt" else 0)
    except (OSError, subprocess.SubprocessError):
        return False
    return "llama-server.exe" in (r.stdout or "").lower()


def card_busy() -> str:
    """Why a GPU self-test must not run right now ("" when it may).

    A test run against a busy card records a failure belonging to the moment rather than to the backend -
    which is exactly how both GPU verdicts on this box were written off on 2026-09-20.
    """
    if _foreign_engine_running():
        return "another llama-server is running and holding the card"
    try:
        import perf  # noqa: PLC0415

        total, free = perf.gpu_vram_mib()
    except Exception:  # noqa: BLE001
        return ""
    if total and free < SELFTEST_MIN_FREE_MIB:
        return (f"only {free} MiB of {total} MiB VRAM free - a self-test now would measure contention, "
                f"not the backend")
    return ""


def _access_violation(test: dict[str, Any]) -> bool:
    """True when a failed launch faulted the way a card being taken away does, rather than refusing to load.

    Measured on this box twice on 2026-09-20: the CUDA build faulted with 0xC0000005 (3221225477) while a
    model was still being torn down, and a working build was written off as ``bad`` - which then withdrew
    the console's proven GPU selection. A loader that cannot use a model says so in words (a key shape, a
    tensor shape); it does not fault.
    """
    detail = str((test or {}).get("detail") or "").lower()
    return any(sig in detail for sig in ("0xc0000005", "3221225477", "access violation"))


# What the engine says when the ACCELERATOR is the problem, and only then. Deliberately narrow: a CUDA
# build names CUDA on every healthy load too ("found 1 CUDA devices"), so a bare "cuda" would condemn a
# working build on any tail it was shown.
_ACCELERATOR_WORDS = (
    "no cuda-capable device", "failed to initialize cuda", "cuda error", "cudart", "cublas",
    "ggml-cuda", "ggml_cuda", "ggml-vulkan", "ggml_vulkan", "no vulkan device", "vulkan error",
    "no usable device", "driver", "failed to load shared library", "cannot open shared object",
)


def boot_condemnation(name: str, *, detail: str = "", log_tail: str = "") -> tuple[bool, str]:
    """May a failed BOOT condemn this backend? Returns (may_condemn, why_not).

    A boot is one attempt in one moment; a verdict is believed for hours. Measured 2026-09-20 22:54: a
    boot wrote `bad` against the cuda build with "launch_failed: RuntimeError: llama-server exited 1"
    while our own checker sweep was still releasing the card - which withdrew the proven selection,
    deleted `data/perf_active.json`, and dropped the console onto the CPU engine with nothing saying so.
    Four ways a boot lies, all of them now refused:

      * the card was in use - a moment, and `ensure()` refuses to test a busy card for the same reason;
      * the launch faulted (0xC0000005 and friends) - a fault is not a verdict;
      * the engine's own words blame the MODEL's metadata - a newer llama.cpp build, or another quant, is
        the fix, and that is not a property of this CUDA build;
      * there are no engine words at all - "exited 1" with no loader sentence condemns nothing.

    What is left - the accelerator itself refusing to come up - is a real condemnation, and it stays.
    """
    if card_busy():
        return False, "the card was in use, which is a moment and not a property of the build"
    if _access_violation({"detail": detail}):
        return False, "the launch faulted, and a fault is not a verdict"
    text = f"{log_tail or ''} {detail or ''}".strip()
    if not text:
        return False, "no engine words to blame the build with"
    try:
        import model_check  # noqa: PLC0415

        cls, _hint = model_check.classify_failure(log_tail, detail)
    except Exception:  # noqa: BLE001 - a missing classifier must not invent a condemnation
        cls = ""
    if cls == "engine":
        return False, "the engine's own words blame the model's metadata, not this backend"
    if not any(w in text.lower() for w in _ACCELERATOR_WORDS):
        return False, "the engine's own words do not name the accelerator"
    return True, ""


def ensure(*, devices: Any = None, models: Any = None, threads: int = 8, host: str = "",
           enabled: bool = True, allow_retest: bool = False, refresh: bool = False) -> dict[str, Any]:
    """Decide the engine dir and backend for this host, proving GPU use before claiming it.

    Cheap after the first call in a process and after the first boot on a host: verdicts
    live in the kit's perf store, keyed by the backend's own files.
    """
    # Keyed by what the decision depends on - the host and the config. Threads change the *test*, not the
    # outcome the test already recorded, and keying on them re-ran real self-tests for every model in a
    # checker sweep (each one another chance to launch beside a live engine).
    memo_key = f"{host}|{enabled}"
    if not refresh and memo_key in _MEMO:
        return dict(_MEMO[memo_key])
    out: dict[str, Any] = {
        "backend": "cpu",
        "engine_dir": str(base_engine_dir()),
        "kind": "base",
        "gpu_ok": False,
        "reason": "engine_cpu_only",
        "detail": "",
        "tested": False,
        "device": "",
        "store": [],
        "signature": SIGNATURE,
    }
    if not enabled:
        out["reason"] = "gpu_disabled_by_config"
        _MEMO[memo_key] = dict(out)
        return dict(out)
    store = installed()
    out["store"] = sorted(store)
    if not store:
        # Nothing installed: make sure no half-applied overlay DLL can break the CPU path.
        for name in applied_overlays():
            deactivate(name)
        clear_active()
        out["reason"] = "engine_cpu_only"
        _MEMO[memo_key] = dict(out)
        return dict(out)

    cands = order_candidates(store, devices)
    probe = probe_models(models)
    last_reason = "backend_not_proven"
    for name in cands:
        info = store[name]
        ed = engine_dir_for(name, info)
        key = backend_key(name, info)
        verdict = backend_verdict(host, name)
        fresh = bool(verdict) and verdict.get("key") == key
        if fresh and verdict.get("verdict") == "ok":
            act = activate(name, info)
            set_active(name, ed, host=host, reason="remembered_ok",
                       detail=f"{name} proven on this host ({verdict.get('detail') or ''})")
            out.update(backend=name, engine_dir=str(ed), kind=info.get("kind") or "",
                       gpu_ok=True, reason="backend_proven_on_this_host",
                       detail=str(verdict.get("detail") or ""), tested=False,
                       device=str(verdict.get("device") or ""))
            _MEMO[memo_key] = dict(out)
            return dict(out)
        # ``verdict`` is None on a host that has never been tested: the old line could not touch it because
        # ``fresh`` short-circuited first. Guard it here too - the store tests caught this immediately.
        stale = (bool(verdict) and verdict.get("verdict") == "bad"
                 and _verdict_age_s(verdict) >= VERDICT_RETRY_AFTER_S)
        if fresh and verdict.get("verdict") == "bad" and not (allow_retest or stale):
            last_reason = "backend_failed_on_this_host"
            # A backend already proven bad must not be left applied to the engine dir.
            if info.get("kind") == "overlay":
                deactivate(name, info)
            last_detail = str(verdict.get("detail") or "")
            out["detail"] = last_detail
            continue
        # Unproven (or explicitly retested): apply, look for a device, then load a model.
        busy = card_busy()
        if busy:
            # Measure nothing rather than measure the moment: no verdict is remembered from a busy card.
            last_reason = "gpu_busy_not_probed"
            out["detail"] = busy
            continue
        act = activate(name, info)
        if not act.get("activated"):
            last_reason = "backend_activation_failed"
            continue
        devs = []
        try:
            import perf

            devs = perf.engine_devices(ed / "llama-server.exe", refresh=True)
        except Exception:
            devs = []
        device = ""
        try:
            import perf

            best = perf.best_device(devs)
            device = str((best or {}).get("name") or "")
        except Exception:
            best, device = None, ""
        if not devs:
            last_reason = "backend_sees_no_device"
            remember_backend(host, name, verdict="bad", key=key, detail=last_reason,
                             device=device, kind=info.get("kind") or "")
            if info.get("kind") == "overlay":
                deactivate(name, info)
            continue
        test = {"ok": False, "detail": "no_probe_model"}
        crashes: list[str] = []
        for model in probe:
            test = self_test(ed, model, ngl=FULL_LAYERS, threads=int(threads), backend=name)
            if test.get("ok"):
                break
            # A model that kills the engine is not automatically a backend verdict: keep
            # trying, and only condemn the backend when every probe model failed.
            if "0x" in str(test.get("detail")) or "exit" in str(test.get("detail")):
                crashes.append(f"{_probe_label(model)}: {test.get('detail')}")
        if not test.get("ok") and crashes:
            test = dict(test, detail="; ".join(crashes[:3]))
        out["tested"] = True
        if not test.get("ok") and _access_violation(test):
            # A fault is not a verdict: remember nothing, apply nothing, and let the next boot reconsider.
            last_reason = "backend_faulted_not_probed"
            out["detail"] = (f"{name}: {test.get('detail')} - the card faulted on launch, which is a "
                             f"moment and not a property of the build; no verdict recorded")
            if info.get("kind") == "overlay":
                deactivate(name, info)
            continue
        remember_backend(host, name, verdict="ok" if test.get("ok") else "bad", key=key,
                         detail=str(test.get("detail") or ""), device=device,
                         seconds=test.get("seconds"), kind=info.get("kind") or "")
        if not test.get("ok"):
            last_reason = "backend_crashed_on_this_host" if test.get("rc") is not None \
                else "backend_self_test_incomplete"
            out["detail"] = str(test.get("detail") or "")
            if info.get("kind") == "overlay":
                deactivate(name, info)
            continue
        set_active(name, ed, host=host, reason="self_test_passed",
                   detail=f"{name} loaded a model on this host in {test.get('seconds')}s")
        out.update(backend=name, engine_dir=str(ed), kind=info.get("kind") or "",
                   gpu_ok=True, reason="self_test_passed",
                   detail=f"loaded in {test.get('seconds')}s", device=device)
        _MEMO[memo_key] = dict(out)
        return dict(out)

    for name in applied_overlays():
        if name not in store:
            deactivate(name)

    def _proven(nm: str) -> bool:
        v = backend_verdict(host, nm) or {}
        return bool(v) and v.get("verdict") == "ok" and v.get("key") == backend_key(nm, store[nm])

    if not any(_proven(nm) for nm in store):
        # Only withdraw the selection when nothing on this host is proven. Clearing here is how the console
        # silently lost its GPU build on 2026-09-20, after a contended self-test wrote a verdict it had no
        # right to write.
        clear_active()
    else:
        out["detail"] = (out.get("detail") or "") + " [a proven backend is still recorded; the selection "                                                    "was left alone]".strip()
    out["reason"] = last_reason
    _MEMO[memo_key] = dict(out)
    return dict(out)


# ---------------------------------------------------------------- CLI (receipts)

def _cli() -> int:
    """python src/backends.py [status|list|test|drop|apply] — for receipts and debugging."""
    import argparse
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    ap = argparse.ArgumentParser(description="LYGO backend store: inspect, prove, retire.")
    ap.add_argument("action", nargs="?", default="status",
                    choices=["status", "list", "test", "drop", "apply"])
    ap.add_argument("name", nargs="?", help="backend name for test/drop/apply")
    ap.add_argument("--threads", type=int, default=8)
    args = ap.parse_args()

    if args.action in ("status", "list"):
        print(json.dumps({"report": report(), "installed": installed()}, indent=2, sort_keys=True))
        return 0

    name = str(args.name or "").lower()
    if not name:
        print("a backend name is required for test/drop/apply")
        return 2
    info = backend_info(name)
    out: dict[str, Any] = {"backend": name, "info": {k: v for k, v in info.items() if k != "files"}}
    if args.action == "apply":
        out["activation"] = activate(name, info)
    elif args.action == "drop":
        out["deactivation"] = deactivate(name, info)
        clear_active()
    else:
        import perf

        import lygo_engine

        models = [m for m in ((lygo_engine.__dict__.get("registry") or __import__("registry"))
                              .load() or {}).get("models") or [] if isinstance(m, dict)]
        probe = probe_models(models)
        out["probe_models"] = [str(p) for p in probe]
        out["results"] = []
        for p in probe:
            res = self_test(engine_dir_for(name, info), p, ngl=FULL_LAYERS,
                            threads=int(args.threads), backend=name)
            remember_backend(perf.host_id(), name,
                             verdict="ok" if res.get("ok") else "bad",
                             key=backend_key(name, info), detail=str(res.get("detail") or ""),
                             seconds=res.get("seconds"))
            out["results"].append(res)
            if res.get("ok"):
                out["activation"] = activate(name, info)
                set_active(name, engine_dir_for(name, info), host=perf.host_id(),
                           reason="self_test_passed_cli", detail=str(res.get("detail") or ""))
                break
    print(json.dumps(out, indent=2, sort_keys=True, default=str))
    return 0



def report() -> dict[str, Any]:
    """Health view of the backend layer. Never raises."""
    try:
        store = installed()
        rec = active_record()
        return {
            "active": str(rec.get("backend") or "cpu"),
            "engine_dir": str(rec.get("engine_dir") or base_engine_dir()),
            "reason": str(rec.get("reason") or ""),
            "detail": str(rec.get("detail") or ""),
            "installed": sorted(store),
            "applied_overlays": applied_overlays(),
            "signature": SIGNATURE,
        }
    except Exception as exc:
        return {"active": "unknown", "error": f"{type(exc).__name__}: {exc}"}


if __name__ == "__main__":
    raise SystemExit(_cli())
