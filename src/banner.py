"""The boot banner - LYGO, in the terminal, telling the truth about the box.

A boot used to be three plain lines. The operator asked for a logo: big, ASCII, futuristic, fully
branded, printed on the PowerShell the engine boots in - and "while it runs" it should say what is
actually serving: the card, its free memory, the engine build, the ports, the model.

Rules this module holds to:

* **The artwork is fixed, the facts are live.** Card and RAM come from `hardware.py` at print time, so a
  banner is a reading, not a version string typed once.
* **Nothing here may raise.** A banner that can stop a boot is worse than no banner: every lookup is
  guarded and a missing answer is printed as a blank with the reason.
* **Colour is optional.** `NO_COLOR`, a non-tty, or `--no-color` gives the same art in plain text -
  PowerShell is not always Windows Terminal and a piped log is not a terminal at all.
* **It fits.** No line is wider than the terminal, and a narrow one gets the name and the signature
  rather than a wrapped, unreadable mess.
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import Any

SIGNATURE = "Δ9Φ963-LYGO-BANNER-v1"

# ANSI Shadow letterforms, composed by hand: L Y G O.
_GLYPHS = {
    "L": ("██╗     ", "██║     ", "██║     ", "██║     ", "███████╗", "╚══════╝"),
    "Y": ("██╗   ██╗", "╚██╗ ██╔╝", " ╚████╔╝ ", "  ╚██╔╝  ", "   ██║   ", "   ╚═╝   "),
    "G": (" ██████╗ ", "██╔════╝ ", "██║  ███╗", "██║   ██║", "╚██████╔╝", " ╚═════╝ "),
    "O": (" ██████╗ ", "██╔═══██╗", "██║   ██║", "██║   ██║", "╚██████╔╝", " ╚═════╝ "),
}

# One hue per row of the artwork: cyan into violet, so a running console reads as "live".
_ROWS = (51, 45, 39, 99, 129, 165)

TAGLINE = "SOVEREIGN LOCAL INTELLIGENCE  ·  RUNS ON THIS MACHINE  ·  NO CLOUD REQUIRED TO ANSWER"

_LOGO = tuple("".join(_GLYPHS[ch][row] for ch in "LYGO").rstrip() for row in range(6))

DELTA = "Δ9Φ963  ·  LYGO PROTOCOL STACK  ·  STEWARD: LIGHTFATHER"


def wants_color(stream: Any = None, flag: bool | None = None) -> bool:
    """Colour unless it is refused: an explicit flag, NO_COLOR, or a stream that is not a terminal."""
    if flag is not None:
        return bool(flag)
    if os.environ.get("NO_COLOR"):
        return False
    # TERM is usually *unset* in a PowerShell window, and PowerShell 5.1+ on Windows 11 renders ANSI
    # fine - so an empty TERM is not a reason to refuse colour. Only a terminal that says it is dumb is.
    if str(os.environ.get("TERM") or "").lower() == "dumb":
        return False
    target = stream if stream is not None else sys.stdout
    try:
        return bool(target.isatty())
    except Exception:
        return False


def _paint(text: str, code: int, color: bool) -> str:
    return "\x1b[38;5;%dm%s\x1b[0m" % (code, text) if color else text


def _rule(width: int, color: bool) -> str:
    line = "═" * max(8, min(width, 74))
    return _paint(line, 45, color)


def live_facts(*, model: str = "", title: str = "", url: str = "", kit: str = "",
               console_port: int | None = None, engine_port: int | None = None,
               backend: str = "", engine: str = "", ngl: Any = None) -> dict[str, Any]:
    """What the box can say about itself right now. Every part of it optional, none of it fatal.

    The keyword set is the boot site's: it passed `kit` and the two ports on the first real boot and the
    whole banner degraded on "unexpected keyword argument 'kit'" - see the test that calls this exactly
    as the boot does.
    """
    facts: dict[str, Any] = {"model": model, "title": title, "url": url, "kit": kit,
                             "backend": backend, "engine": engine}
    if console_port:
        facts["console_port"] = console_port
    if engine_port:
        facts["engine_port"] = engine_port
    if ngl is not None:
        facts["ngl"] = ngl
    try:
        facts["hardware"] = hardware.snapshot()
    except Exception as exc:  # noqa: BLE001 - a banner must not be able to stop a boot
        facts["hardware"] = {"available": False, "why": "%s: %s" % (type(exc).__name__, exc), "gpus": [], "ram": {}, "cpu": {}}
    try:
        import os

        # The port this console is actually bound to (the boot publishes it), then the configured
        # default. Measured 2026-09-21: bound to 9651, the agent told the operator 9641.
        facts.setdefault("console_port", int(os.environ.get("LYGO_CONSOLE_PORT") or 9641))
    except (TypeError, ValueError):
        facts.setdefault("console_port", 9641)
    facts.setdefault("engine_port", 11441)
    if not facts.get("backend"):
        try:
            import backends as _backends

            facts["backend"] = str(_backends.report().get("active") or "")
        except Exception:  # noqa: BLE001
            pass
    return facts


def _wrap_line(text: str, width: int) -> list[str]:
    """Wrap a fact line on its ` · ` separators. Never cut a fact in half.

    Measured while writing this: the ports line was truncated to "ports 9641 console " at 80 columns -
    a banner quietly dropping half of what it was saying is worse than a banner that wraps.
    """
    if len(text) <= width:
        return [text]
    out: list[str] = []
    cur = ""
    for part in text.split(" · "):
        cand = (cur + " · " + part) if cur else part
        if len(cand) <= width:
            cur = cand
            continue
        if cur:
            out.append(cur)
        cur = part if len(part) <= width else part[: max(4, width - 1)] + "…"
    if cur:
        out.append(cur)
    return out


def _facts_lines(facts: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if facts.get("title"):
        line = str(facts["title"])
        if facts.get("url"):
            line += "   " + str(facts["url"])
        out.append(line)
    hw = facts.get("hardware") or {}
    gpus = hw.get("gpus") or []
    if gpus:
        g = gpus[0]
        out.append("GPU   %s   %s MiB free of %s   %s%% busy" % (
            str(g.get("name") or "card")[:28], g.get("free_mib"), g.get("total_mib"), g.get("util_pct")))
    else:
        out.append("GPU   %s" % (str(hw.get("why") or "not readable")))
    ram = hw.get("ram") or {}
    cpu = hw.get("cpu") or {}
    if ram.get("available_mib"):
        out.append("RAM   %s MiB free of %s        CPU   %s threads%s" % (
            ram.get("available_mib"), ram.get("total_mib"), cpu.get("cores") or "?",
            ("  %s%% busy" % cpu.get("pct_busy")) if cpu.get("pct_busy") is not None else ""))
    bits = []
    if facts.get("model"):
        bits.append("model %s" % facts["model"])
    if facts.get("backend"):
        bits.append("backend %s" % facts["backend"])
    if facts.get("engine"):
        bits.append("engine %s" % facts["engine"])
    if facts.get("ngl") is not None:
        bits.append("ngl %s" % facts["ngl"])
    if facts.get("console_port") and facts.get("engine_port"):
        bits.append("ports %s/%s" % (facts["console_port"], facts["engine_port"]))
    if bits:
        out.append(" · ".join(str(b) for b in bits))
    if facts.get("kit"):
        out.append(str(facts["kit"]))
    return out


def render(facts: dict[str, Any] | None = None, *, color: bool | None = None, width: int = 0) -> str:
    """The whole banner as text. `width` 0 means the terminal's own width (or 80 when there is none)."""
    use_color = wants_color(flag=color)
    if not width:
        try:
            width = shutil.get_terminal_size((80, 24)).columns or 80
        except Exception:
            width = 80
    width = max(24, int(width))
    facts = dict(facts or {})
    lines: list[str] = [""]
    art_widest = max(len(row) for row in _LOGO)
    if width - 6 >= art_widest:
        for i, row in enumerate(_LOGO):
            lines.append("  " + _paint(row, _ROWS[i % len(_ROWS)], use_color))
        lines.append("  " + _paint(DELTA, 51, use_color))
    else:
        # A narrow window gets the name and the mark, not a wrapped logo.
        lines.append("  " + _paint("Δ9Φ963  L Y G O", 51, use_color))
        lines.append("  " + _paint("LYGO LLM CONSOLE", 45, use_color))
    if width - 4 >= len(TAGLINE):
        lines.append("  " + _paint(TAGLINE, 39, use_color))
    lines.append("  " + _rule(width - 4, use_color))
    for line in _facts_lines(facts):
        for piece in _wrap_line(line, max(8, width - 2)):
            lines.append("  " + piece)
    lines.append("  " + _paint("%s  ·  LYGO Sovereign License v3.0" % SIGNATURE, 99, use_color))
    lines.append("")
    text = "\n".join(line.rstrip() for line in lines)
    return "\n".join(line[:width] for line in text.splitlines())



def set_window_title(text: str) -> bool:
    """Brand the terminal window itself (OSC 0), so a running console says LYGO in the title bar too.

    Only when stdout is a terminal: a piped log must not receive a title escape, and a terminal that
    cannot do it must not be punished for trying. Never raises.
    """
    try:
        if not wants_color(stream=sys.stdout, flag=None) and not getattr(sys.stdout, "isatty", None):
            return False
        if not sys.stdout.isatty():
            return False
        sys.stdout.write("\x1b]0;%s\x07" % str(text).replace("\x07", "")[:120])
        sys.stdout.flush()
        return True
    except Exception:  # noqa: BLE001
        return False


def print_engine_line(model: str = "", *, backend: str = "", tag: str = "", ngl: Any = None,
                      port: int = 0, color: bool | None = None) -> str:
    """One branded line when the engine is about to serve: what loads, on what, and how much of it
    goes to the card. The tag comes from the backend store when the caller does not know it, so the
    line reports the build actually about to run rather than one remembered from a config."""
    use_color = wants_color(flag=color)
    if not backend:
        try:
            import backends as _backends

            backend = str(_backends.report().get("active") or "")
        except Exception:  # noqa: BLE001
            backend = ""
    if not tag and backend:
        try:
            import json as _json
            from pathlib import Path as _Path

            store = _Path(__file__).resolve().parent.parent / "engine" / "backends" / str(backend) / "backend.json"
            if store.is_file():
                tag = str(_json.loads(store.read_text(encoding="utf-8")).get("tag") or "")
        except Exception:  # noqa: BLE001
            tag = ""
    bits = [b for b in (
        "engine %s" % backend if backend else "",
        "build %s" % tag if tag else "",
        "ngl %s" % ngl if ngl is not None else "",
        "port %s" % port if port else "",
    ) if b]
    line = "◆ %s  %s%s" % (_paint("Δ9Φ963", 51, use_color),
                           ("loading %s   " % model) if model else "",
                           _paint(" · ".join(bits), 45, use_color))
    text = line.rstrip()
    try:
        print(text, flush=True)
    except Exception:  # noqa: BLE001
        pass
    return text


def print_banner(facts: dict[str, Any] | None = None, *, color: bool | None = None, width: int = 0) -> str:
    """Print the banner and return what was printed.

    Never raises: a console that cannot print a banner must still boot.
    """
    try:
        text = render(facts, color=color, width=width)
    except Exception as exc:  # noqa: BLE001 - the last line of defence before a boot is affected
        text = "%s  (banner degraded: %s: %s)" % (SIGNATURE, type(exc).__name__, exc)
    try:
        print(text, flush=True)
    except Exception:  # noqa: BLE001 - a closed stdout is not a reason to fail a boot
        pass
    return text


try:
    import hardware  # noqa: E402  (the live reading; optional on a foreign box)
except Exception:  # noqa: BLE001
    hardware = None  # type: ignore[assignment]
