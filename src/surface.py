"""One console, three systems - named, and measured rather than assumed.

The console is the same code in all three; what differs is where the kit lives and what answers:

    usb_local   "USB LOCAL"               kit on removable media - the stick's own CPU-proven engine
                                          answers, and the API boosts it when a host or a question
                                          needs more. Boots on any PC it is plugged into.
    pc_local    "PC LOCAL"                kit on a fixed disk - the host's GPU/CPU engine answers
                                          (much faster), with the same API boost on demand.
    api_only    "WEB PORTAL (API ONLY)"   nothing local answers: the online API does, through the
                                          public web portal. No engine, no disks, no local models.

What each one *is* - the operator's own definition, quoted on every surface:

    USB LOCAL   stand-alone agent - everything onboard the stick: plug it in and go, and it is
                mobile (one stick, carried between PCs)
    PC LOCAL    the full admin console on this PC - this is the build that becomes the public
                version
    WEB PORTAL  the internet portal, API-only: already built, an easy API agent page anyone can
                use online

The label is read from the machine, never guessed:
  * media comes from the Windows volume type of the kit's drive (REMOVABLE vs FIXED). A stick whose
    bridge reports itself as a fixed disk can be declared with LYGO_SURFACE=usb - a declaration
    always wins over the probe.
  * which system is answering *now* comes from what actually booted: a local engine that is ready
    means a LOCAL system belongs to usb/pc; no local engine means the API-only portal system.
"""
from __future__ import annotations

import os
from pathlib import Path

from paths import KIT_ROOT

USB_LOCAL = "usb_local"
PC_LOCAL = "pc_local"
API_ONLY = "api_only"
ORDER = (USB_LOCAL, PC_LOCAL, API_ONLY)

LABELS = {
    USB_LOCAL: "USB LOCAL",
    PC_LOCAL: "PC LOCAL",
    API_ONLY: "WEB PORTAL (API ONLY)",
}
ANSWERS = {
    USB_LOCAL: "the stick's own engine (CPU-proven, boots on any PC) - API boosts on demand",
    PC_LOCAL: "this PC's engine (GPU when present, CPU otherwise) - API boosts on demand",
    API_ONLY: "the online API through the public web portal - no local engine involved",
}
ROLES = {
    USB_LOCAL: "stand-alone agent: everything onboard the stick - plug it in and go, mobile",
    PC_LOCAL: "full admin console on this PC - this is the build that becomes the public version",
    API_ONLY: "online API-only agent portal - already built, anyone can use it from a web page",
}


# Compact form of the same three roles, for the system prompt only: the engine window is 8192 tokens
# and the identity block has to leave room for history and the answer. Human surfaces (launchers,
# pages, /api/health) use ROLES - the operator's own wording - never this.
ROLES_BRIEF = {
    USB_LOCAL: "stand-alone: onboard, plug and play, mobile",
    PC_LOCAL: "admin console on this PC; becomes the public version",
    API_ONLY: "online, API-only; already built for the web",
}
LAUNCHERS = {
    USB_LOCAL: "LYGO_AGENT_STICK.bat on the stick",
    PC_LOCAL: "LYGO_LLM_CONSOLE.bat on this PC",
    API_ONLY: "PUBLIC_GATEWAY.bat + https://chatagent.ca/portal/",
}
DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3


def drive_type(drive: str) -> int:
    """Windows volume type for a drive letter ("E:"), 0 when it cannot be read."""
    try:
        import ctypes

        d = drive if drive.endswith("\\") else drive + "\\"
        return int(ctypes.windll.kernel32.GetDriveTypeW(d))
    except Exception:
        return 0


def media(root: Path | None = None) -> tuple[str, str]:
    """Return ("usb"|"pc", why). Declaration wins, then the volume type, then the launchers."""
    root = Path(root or KIT_ROOT)
    declared = (os.environ.get("LYGO_SURFACE") or "").strip().lower()
    if declared in ("usb", "stick", "portable"):
        return "usb", "declared by LYGO_SURFACE"
    if declared in ("pc", "desktop", "local"):
        return "pc", "declared by LYGO_SURFACE"
    t = drive_type(root.drive)
    if t == DRIVE_REMOVABLE:
        return "usb", "GetDriveTypeW(" + str(root.drive) + ") = REMOVABLE"
    if t == DRIVE_FIXED:
        return "pc", "GetDriveTypeW(" + str(root.drive) + ") = FIXED"
    inferred = "usb" if (root / "LYGO_AGENT_STICK.bat").is_file() else "pc"
    return inferred, "volume type unreadable - inferred from the kit's launchers"


def here(local_ready: bool = True, root: Path | None = None) -> str:
    """The system answering now: a LOCAL one while a local engine is ready, else the API-only portal."""
    if not local_ready:
        return API_ONLY
    kind, _ = media(root)
    return USB_LOCAL if kind == "usb" else PC_LOCAL


def report(local_ready: bool = True, root: Path | None = None) -> dict:
    """The label block: which system this is, what answers, and all three with one flagged."""
    kind, why = media(root)
    mid = USB_LOCAL if kind == "usb" else PC_LOCAL
    cur = here(local_ready, root)
    return {
        "id": cur,
        "label": LABELS[cur],
        "answers": ANSWERS[cur],
        "role": ROLES[cur],
        "media": kind,
        "media_why": why,
        "local_ready": bool(local_ready),
        "local_system": LABELS[mid],
        "detail": (
            "kit on " + ("removable media" if kind == "usb" else "a fixed disk")
            + " (" + why + "); local system here is " + LABELS[mid]
        ),
        "systems": [
            {
                "id": i,
                "label": LABELS[i],
                "here": i == cur,
                "answers": ANSWERS[i],
                "role": ROLES[i],
                "launcher": LAUNCHERS[i],
            }
            for i in ORDER
        ],
    }


def lines(rep: dict | None = None) -> list[str]:
    """Human-readable labels for a banner or a status screen: who this is, and all three."""
    r = rep or report()
    out = [
        "SYSTEM: " + str(r["label"]) + "  (" + str(r["id"]) + ")",
        "  role   : " + str(r["role"]),
        "  answers: " + str(r["answers"]),
        "  the three systems, this one marked > :",
    ]
    for s in r["systems"]:
        out.append(
            ("  > " if s["here"] else "    ") + str(s["label"]).ljust(24) + " " + str(s["role"])
        )
    return out
