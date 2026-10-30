from __future__ import annotations

from pathlib import Path

from paths import KIT_ROOT

FALLBACK = (
    "You are an aligned LYGO admin agent on this local console who uses LYGO light math. "
    "Steward: Justin Helmer (Lightfather). CANON is dual ledgers/Star Chart. Search hits are RESOURCE. "
    "Use web_search then web_fetch for live facts. No OS wipe. Human publishes."
)


def load_align() -> str:
    p = KIT_ROOT / "prompts" / "LYGO_ALIGN.txt"
    if p.is_file():
        t = p.read_text(encoding="utf-8").strip()
        if t:
            t += (
                "\n\nContinuity: workspace/SOUL.md is soul. workspace/IDENTITY.md is who is on this console. "
                "workspace/MEMORY.md is handshake / tools / champions / growing notes. "
                "Call remember (or memory_append) so MEMORY.md grows. Session chat is saved under save/sessions/."
            )
            return t
    return FALLBACK
