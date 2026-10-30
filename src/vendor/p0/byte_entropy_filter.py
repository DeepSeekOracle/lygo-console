# byte_entropy_filter.py — formerly lygo_p0.py ("Nano Kernel" / "Φ-gate")
#
# HONEST SCOPE: this module measures two things about a raw byte string —
# Shannon entropy and zlib compressibility — and buckets the result into
# AMPLIFY / SOFTEN / QUARANTINE. It does NOT assess meaning, intent, safety,
# or ethics.

from __future__ import annotations

import hashlib
import json
import math
import struct
import sys
import zlib
from pathlib import Path
from typing import Any

MAX_BYTES = 8192
PHI_MIN = 0.618
PHI_MAX = 1.618
ENTROPY_LOW = 0.25
ENTROPY_HIGH = 0.90
COMP_MIN_LEN = 64
COMP_POOR = 0.90

VERDICTS = ("AMPLIFY", "SOFTEN", "QUARANTINE")


def f32(x: float) -> float:
    return struct.unpack("<f", struct.pack("<f", float(x)))[0]


def round4(x: float) -> float:
    v = f32(x) * 10000.0
    add = 0.5 if v >= 0.0 else -0.5
    return int(v + add) / 10000.0


def entropy_norm(data: bytes) -> float:
    if not data:
        return 0.0
    freq = [0] * 256
    for b in data:
        freq[b] += 1
    length = f32(float(len(data)))
    ent = f32(0.0)
    for c in freq:
        if c:
            p = f32(f32(float(c)) / length)
            ent = f32(ent - f32(p * f32(math.log2(p))))
    denom = f32(math.log2(length)) if len(data) > 1 else f32(1.0)
    return f32(min(f32(ent / denom), f32(1.0)))


def compression_ratio(data: bytes) -> float:
    original_size = len(data)
    if original_size < COMP_MIN_LEN:
        return 0.0
    compressed_size = len(zlib.compress(data, level=9))
    ratio = 1.0 - (compressed_size / original_size)
    return f32(max(0.0, min(ratio, 1.0)))


def compute_phi_risk(risk: float, length: int) -> float:
    size_damp = f32(f32(float(length)) / f32(128.0)) if length < 128 else f32(1.0)
    r = f32(min(f32(risk), f32(1.0)))
    return f32(r * f32(PHI_MAX) * size_damp)


def verdict_from_bucket(phi_risk: float, ent: float, preliminary: str) -> str:
    verdict = preliminary
    if ent < f32(ENTROPY_LOW) and verdict == "AMPLIFY":
        verdict = "SOFTEN"
    return verdict


def build_reasoning(
    length: int, ent: float, comp: float, risk: float, phi_risk: float, verdict: str
) -> str:
    if length > MAX_BYTES:
        return f"length {length} > MAX_BYTES {MAX_BYTES} → QUARANTINE (hard cap)"

    parts: list[str] = []
    if ent > ENTROPY_HIGH:
        parts.append(f"entropy {ent:.4f} > {ENTROPY_HIGH} (+0.30 risk)")
    elif ent < ENTROPY_LOW:
        parts.append(f"entropy {ent:.4f} < {ENTROPY_LOW} (+0.15 risk)")
    else:
        parts.append(f"entropy {ent:.4f} in band")

    if comp > COMP_POOR:
        parts.append(f"zlib compressibility {comp:.4f} > {COMP_POOR} (+0.25 risk, highly redundant)")
    else:
        parts.append(f"zlib compressibility {comp:.4f} acceptable")

    damp = length / 128.0 if length < 128 else 1.0
    parts.append(f"size_damp={damp:.4f} (len={length})")
    parts.append(f"bucket_score=risk×{PHI_MAX}×damp={risk:.4f}×{PHI_MAX}×{damp:.4f}={phi_risk:.4f}")

    if phi_risk < PHI_MIN:
        gate = f"bucket_score < {PHI_MIN} → AMPLIFY"
    elif phi_risk <= PHI_MAX:
        gate = f"{PHI_MIN} ≤ bucket_score ≤ {PHI_MAX} → SOFTEN"
    else:
        gate = "bucket_score > threshold → QUARANTINE"

    parts.append(gate)
    if ent < f32(ENTROPY_LOW) and verdict == "SOFTEN" and phi_risk < f32(PHI_MIN):
        parts.append("low-entropy guard: AMPLIFY blocked → SOFTEN")
    return "; ".join(parts)


def validate_bytes(data: bytes) -> dict[str, Any]:
    if len(data) > MAX_BYTES:
        score = round4(PHI_MAX)
        return {
            "verdict": "QUARANTINE",
            "risk": 1.0,
            "entropy": 0.0,
            "compression": 0.0,
            "score": score,
            "phi_risk": score,
            "hash": hashlib.sha256(data).hexdigest()[:16],
            "reasoning": build_reasoning(len(data), 0.0, 0.0, 1.0, PHI_MAX, "QUARANTINE"),
        }

    ent = entropy_norm(data)
    comp = compression_ratio(data)
    risk = f32(0.0)

    if ent > f32(ENTROPY_HIGH):
        risk = f32(risk + f32(0.30))
    elif ent < f32(ENTROPY_LOW):
        risk = f32(risk + f32(0.15))

    if comp > f32(COMP_POOR):
        risk = f32(risk + f32(0.25))

    risk = f32(min(risk, f32(1.0)))
    phi_risk = compute_phi_risk(risk, len(data))

    if phi_risk < f32(PHI_MIN):
        pre = "AMPLIFY"
    elif phi_risk <= f32(PHI_MAX):
        pre = "SOFTEN"
    else:
        pre = "QUARANTINE"

    verdict = verdict_from_bucket(phi_risk, ent, pre)
    reasoning = build_reasoning(len(data), ent, comp, risk, phi_risk, verdict)
    score = round4(phi_risk)

    return {
        "verdict": verdict,
        "risk": round4(risk),
        "entropy": round4(ent),
        "compression": round4(comp),
        "score": score,
        "phi_risk": score,
        "hash": hashlib.sha256(data).hexdigest()[:16],
        "reasoning": reasoning,
    }


def canonical_line(vector_id: str, result: dict[str, Any]) -> str:
    s = result.get("score", result.get("phi_risk", 0.0))
    return (
        f"{vector_id}|{result['verdict']}|{result['risk']:.4f}|"
        f"{result['entropy']:.4f}|{result['compression']:.4f}|{float(s):.4f}"
    )


def fixtures_path() -> Path:
    return Path(__file__).resolve().parents[2] / "fixtures" / "p0_vectors.json"


def load_vectors() -> list[dict[str, Any]]:
    path = fixtures_path()
    with path.open(encoding="utf-8") as f:
        payload = json.load(f)
    return payload["vectors"]


def run_vector_suite() -> str:
    lines: list[str] = []
    for entry in load_vectors():
        data = bytes.fromhex(entry["hex"])
        res = validate_bytes(data)
        lines.append(canonical_line(entry["id"], res))
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--canonical":
        sys.stdout.write(run_vector_suite())
        raise SystemExit(0)
    print("byte_entropy_filter — anomaly filter over raw bytes (entropy + zlib compressibility)")