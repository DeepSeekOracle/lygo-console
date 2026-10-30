from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

GGUF_MAGIC = b"GGUF"
MAX_READ = 1_048_576
UINT8, INT8, UINT16, INT16, UINT32, INT32, FLOAT32, BOOL, STRING, ARRAY = range(10)
UINT64, INT64, FLOAT64 = 10, 11, 12

_SCALAR = {
    UINT8: ("<B", 1),
    INT8: ("<b", 1),
    UINT16: ("<H", 2),
    INT16: ("<h", 2),
    UINT32: ("<I", 4),
    INT32: ("<i", 4),
    FLOAT32: ("<f", 4),
    BOOL: ("<?", 1),
    UINT64: ("<Q", 8),
    INT64: ("<q", 8),
    FLOAT64: ("<d", 8),
}


class Truncated(Exception):
    pass


class Cursor:
    def __init__(self, buf: bytes):
        self.buf = buf
        self.pos = 0
        self.truncated = False

    def need(self, n: int) -> bytes:
        if self.pos + n > len(self.buf):
            self.truncated = True
            raise Truncated()
        sl = self.buf[self.pos : self.pos + n]
        self.pos += n
        return sl

    def u32(self) -> int:
        return struct.unpack("<I", self.need(4))[0]

    def u64(self) -> int:
        return struct.unpack("<Q", self.need(8))[0]

    def string(self) -> str:
        n = self.u64()
        if n > MAX_READ:
            self.truncated = True
            raise Truncated()
        raw = self.need(int(n))
        return raw.decode("utf-8", errors="replace")


def _skip_value(c: Cursor, typ: int) -> Any:
    if typ == STRING:
        return c.string()
    if typ in _SCALAR:
        fmt, sz = _SCALAR[typ]
        raw = c.need(sz)
        return struct.unpack(fmt, raw)[0]
    if typ == ARRAY:
        etype = c.u32()
        count = c.u64()
        # packed elements; do not 32-byte-align between keys or array items
        if etype == STRING:
            out = []
            for _ in range(int(count)):
                out.append(c.string())
            return out
        if etype in _SCALAR:
            _, sz = _SCALAR[etype]
            # skip body without storing
            total = int(count) * sz
            c.need(total)
            return f"<array {count}>"
        # nested arrays rare in header keys we care about
        for _ in range(int(count)):
            _skip_value(c, etype)
        return f"<array {count}>"
    c.truncated = True
    raise Truncated()


# Metadata a VRAM plan needs: architecture dimensions drive the KV cache size, and the KV
# cache is what decides between a full GPU offload and a partial one.
DIM_SUFFIXES = (
    ".context_length",
    ".block_count",
    ".attention.head_count",
    ".attention.head_count_kv",
    ".attention.key_length",
    ".attention.value_length",
    ".embedding_length",
)


def parse_gguf_header(path: Path | str) -> dict[str, Any]:
    p = Path(path)
    with p.open("rb") as f:
        buf = f.read(MAX_READ)
    out: dict[str, Any] = {
        "path": str(p),
        "ok": False,
        "kind": "chat",
        "meta_truncated": False,
        "status": "ok",
    }
    if buf[:4] != GGUF_MAGIC:
        out["status"] = "not_gguf"
        out["kind"] = "archive"
        return out
    if len(buf) < 24:
        out["status"] = "truncated"
        out["meta_truncated"] = True
        return out
    ver = struct.unpack("<I", buf[4:8])[0]
    if ver not in (1, 2, 3):
        out["status"] = "UNSUPPORTED_FORMAT"
        return out
    n_tensors = struct.unpack("<Q", buf[8:16])[0]
    n_kv = struct.unpack("<Q", buf[16:24])[0]
    c = Cursor(buf)
    c.pos = 24
    found: dict[str, Any] = {}
    try:
        for _ in range(int(n_kv)):
            key = c.string()
            typ = c.u32()
            val = _skip_value(c, typ)
            if (
                key in {"general.name", "general.architecture", "general.file_type"}
                or key.endswith(DIM_SUFFIXES)
            ):
                found[key] = val
            # Stop once the header has said everything a VRAM plan needs. Stopping at
            # context_length alone was not enough: converters write the attention.head_count*
            # keys AFTER it, so the KV cache size stayed unknown and every model was planned
            # against a flat allowance instead of its real cache.
            if (
                "general.name" in found
                and "general.architecture" in found
                and any(k.endswith(".context_length") for k in found)
                and any(k.endswith(".attention.head_count_kv") for k in found)
            ):
                break
    except Truncated:
        c.truncated = True
    name = str(found.get("general.name") or p.stem)
    arch = str(found.get("general.architecture") or "")
    ctx = None
    for k, v in found.items():
        if k.endswith(".context_length"):
            try:
                ctx = int(v)
            except (TypeError, ValueError):
                ctx = None
    kind = "chat"
    blob = f"{name} {arch}".lower()
    if arch.lower() in {"nomic-bert", "bert", "jina-bert"} or "embed" in blob:
        kind = "embed"
    if "mmproj" in blob or "clip" in blob:
        kind = "mmproj"
    out.update(
        {
            "ok": bool(name or arch),
            "version": ver,
            "tensor_count": n_tensors,
            "kv_count": n_kv,
            "name": name,
            "architecture": arch,
            "ctx": ctx,
            "kind": kind,
            "meta_truncated": c.truncated,
            "status": "ok" if (name or arch) else "truncated",
            "found": found,
        }
    )
    return out


def write_tiny_gguf(dest: Path) -> None:
    """GGUF v3, tensor_count=0, kv general.name=tiny, general.architecture=llama."""

    def wstr(s: str) -> bytes:
        b = s.encode("utf-8")
        return struct.pack("<Q", len(b)) + b

    kv = wstr("general.name") + struct.pack("<I", STRING) + wstr("tiny")
    kv += wstr("general.architecture") + struct.pack("<I", STRING) + wstr("llama")
    header = b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 0) + struct.pack("<Q", 2)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(header + kv)
