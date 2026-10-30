#!/usr/bin/env python3
"""Read the console's forever history - what it filed, where it is, and what was said about a thing.

    python scripts/read_history.py --where          where the history lives, how big it is
    python scripts/read_history.py --list           every filed conversation, newest last
    python scripts/read_history.py --tail 6         the last 6 filed messages
    python scripts/read_history.py --grep "dock"    every filed line mentioning a word
    python scripts/read_history.py --ask "the dock" the passages that match, as the console recalls them

Nothing here needs the engine, a key or the network: it reads the files the console writes.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT / "src"))

import lygo_rag  # noqa: E402
import transcript_archive as archive  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="read the console's filed conversations")
    ap.add_argument("--where", action="store_true", help="where the forever history lives")
    ap.add_argument("--list", action="store_true", help="list filed conversations")
    ap.add_argument("--tail", type=int, default=0, help="show the last N filed messages")
    ap.add_argument("--grep", default="", help="search the filed text")
    ap.add_argument("--ask", default="", help="recall the passages that match this question")
    ap.add_argument("--budget", type=int, default=1200, help="characters of recall for --ask")
    args = ap.parse_args()

    st = archive.status()
    if args.where or not any([args.list, args.tail, args.grep, args.ask]):
        print(f"{archive.SIGNATURE}")
        print(f"history root : {st['root']}")
        print(f"conversations: {st['conversations']}   bytes: {st['bytes']:,}   queued: {st['queued']}")
        print(f"index        : {st['index'] or '(will appear after the next turn)'}")
        print(f"current file : {st['current_file'] or '(nothing filed yet)'}")
        rag = lygo_rag.status()
        print(f"{rag.get('signature')}  blocks indexed: {rag.get('blocks')}  terms: {rag.get('terms')}")
        return 0

    if args.list:
        for p in archive.files():
            print(f"{p.stat().st_size:>9,}  {p}")
        return 0

    if args.grep:
        hits = archive.search(args.grep, limit=60)
        for h in hits:
            print(f"{Path(h['file']).name}:{h['line']}: {h['text'][:160]}")
        print(f"\n{len(hits)} line(s).")
        return 0

    if args.ask:
        got = lygo_rag.recall(args.ask, budget_chars=args.budget)
        print(got or "(nothing in the filed history matches that)")
        return 0

    if args.tail:
        files = archive.files()
        if not files:
            print("(nothing filed yet)")
            return 0
        lines = files[-1].read_text(encoding="utf-8", errors="replace").splitlines()
        blocks: list[list[str]] = []
        for line in lines:
            if line.startswith("## "):
                blocks.append([line])
            elif blocks:
                blocks[-1].append(line)
        for b in blocks[-args.tail:]:
            print("\n".join(b).rstrip())
            print()
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
