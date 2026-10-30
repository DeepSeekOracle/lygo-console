---
name: lygo-downloads
description: Download big files fast on THIS machine with the `plexo` limb — several network paths at once, several mirrors, resume, a whole-job speed ceiling, and the hand-off to the operator's own Plexo. Use when a model, installer, dataset or ISO has to come down, when a download died at 90%, or when the operator asks to download something. Not for a small page or a few KB.
version: 1.1.0
metadata: {"openclaw": {"emoji": "⬇️", "homepage": "https://chatagent.ca/lygoskillhub.html"}, "lygo": true, "limb": "plexo", "module": "lygo.plexoctl"}
---

# Downloads — LYGO's own engine, and Plexo only for what is left

**The engine is this kit's own code** (`src/plexo_tools.py`, Python standard library only). It does
everything a download manager does for a direct link, and the multi-network trick as well:

| what | how |
| --- | --- |
| one file over **several connections** | blocks fetched in parallel over HTTP range requests |
| one file over **several network paths** | each stream's socket is BOUND to a local address you name (`bind=[...]`), so Wi-Fi and a tethered phone can carry the same file at once |
| one file from **several mirrors** | `urls=[...]` — every source is probed first and they must agree on size and ETag |
| **resume** | a progress record beside the file, with the server's `ETag`/`Last-Modified` checked — a changed file refuses by name instead of splicing two versions |
| a **speed ceiling** | `limit_mbps=...`, on the whole job, not one stream |
| per-path **accounting** | the job reports the bytes each adapter carried, so "the second connection helped" is a number |

**Plexo** ([github.com/anmolkapil/plexo](https://github.com/anmolkapil/plexo), MIT, © Anmol Kapil) is an
optional external app, and the limb can hand a link to it through its own loopback bridge. What is
still genuinely Plexo-only: **BitTorrent** (magnets, `.torrent`, peers, seeding), a desktop GUI with
per-download graphs, a browser extension, and scheduled downloads. A kit without Plexo is not broken:
LYGO's engine needs no payload at all.

The operator drives both from the **Downloads** and **Download controls** cards.

## When to use which

| situation | do this |
| --- | --- |
| a big file (model, installer, ISO, dataset) | `action=fetch` with `url` |
| two connections available (Wi-Fi + a tethered phone) | `action=adapters` first, then `fetch` with `bind=[...]` |
| the same file on several hosts | `fetch` with `urls=[...]` (mirrors) — the agreement check runs automatically |
| must not saturate the link | `fetch` with `limit_mbps=N` |
| a few KB, or a page you want as text | the plain `download_url` / `web_fetch` limb — the overhead is not worth it |
| a download died at 90% | `fetch` again with the same url: it resumes the blocks it holds |
| you do not know if the server allows this | `action=probe` (or `urls=[...]` for mirrors) |
| a `.torrent` or a magnet | `action=hand_off` — LYGO's engine is HTTP(S) only and says so |

## The calls that matter

```
plexo action=adapters                                       → what network paths exist here, and why
plexo action=adapters check=true                            → asks EACH path its own public address, so
                                                              "two cables, one router" is answered honestly
plexo action=fetch  url=<link> name=<file> streams=8        → starts a job, answers AT ONCE with its id
plexo action=fetch  url=<link> urls=[<mirror>,<mirror>]     → mirrors, after the agreement check
plexo action=fetch  url=<link> bind=["<ip>","<ip>"]         → one stream per named local address
plexo action=fetch  url=<link> limit_mbps=5                 → a whole-job ceiling
plexo action=jobs | job | wait | cancel | cancel_all        → progress, and stopping cleanly
plexo action=probe  url=<link> | urls=[...]                 → ranges? size? ETag? do the mirrors agree?
plexo action=verify path=<file>                             → read the file back off the disk
plexo action=hand_off url=<link> consent=true               → give it to Plexo (torrents, its GUI)
plexo action=bridge | open | stop                           → is Plexo answering, start it, stop ours
plexo action=status | doctor | credit | pin                 → the facts, the gaps, the licence, the pin
```

A job is a worker, not a request: `fetch` returns immediately with a job id. For a small file,
`fetch` then `wait` is fine; for a 4 GB model, start it and tell the operator, then check with `jobs`.

## The rules this limb will not bend

* **Never claim a speed-up you did not measure.** Two adapters that share one router share one public
  address and gain NOTHING: `action=adapters check=true` is how to tell the difference, and every rate
  on the card is that job's own bytes divided by its own seconds.
* **Never mix mirrors that disagree.** The check refuses by name (`mirror_mismatch`); a file stitched
  from two versions is wrong in a way nobody can see.
* **`bind` must name an address this machine holds**, or the fetch refuses before anything starts
  (`bind_unknown_address`) — the OS would refuse the socket anyway (`WinError 10049`).
* **Nothing is downloaded by a click, and the 246 MB Plexo installer is never fetched for you.** If
  Plexo is not installed, `open` and `hand_off` refuse by name and point at
  `scripts/fetch_plexo.ps1` — that script is the only door, and it is the operator's to open.
* **`hand_off` needs `consent=true`.** It starts a download on somebody's own connection — possibly
  metered, possibly a phone tether. Plexo's own model is that a human confirms; the agent must be told
  yes in words, not infer it.
* **A file outside `workspace/downloads` needs `consent=true`.** Without it the download lands inside
  the kit, where a copy of the kit carries it.
* **A resume refuses when the server's file changed** (`ETag` / `Last-Modified` / size). It never
  splices two versions into one file — the refusal names `changed_on_server` and says what to delete.
* **A server that ignores range requests gets one stream**, and the job says `ranges: false` instead
  of pretending to parallelise.
* **Never report a download as finished** because a job record says so — `verify` the file, or read
  the job's own `state: done` with a matching byte count.

## What it cannot do (say so plainly)

* **BitTorrent.** Magnets and `.torrent` files belong to Plexo; LYGO's engine is HTTP(S) only.
* **Create bandwidth.** Parallel streams and a second adapter help when the *server*, or one
  connection, is the limit. Two adapters behind one router are one path however many cables are in the
  wall, and no setting changes that.
* **Beat an expired link.** Plexo can take a fresh link for the same file and carry on; this kit
  restarts that download.
* **Schedule for later.** A job starts when it is asked to.

## Credit and licence

LYGO's engine here is this kit's own writing — a standard-library implementation of the same public
techniques a download manager uses (HTTP range requests, a shared queue of blocks, a bound source
address per stream, a token bucket for a ceiling). **No Plexo source is copied into this kit.** Plexo is
MIT-licensed, © Anmol Kapil, and a fetched install carries its own licence beside its binaries;
`plexo action=credit` prints exactly this, including which capability is ours now and which is still
Plexo's, so the attribution travels with the code instead of living only in a document.

## Installing Plexo (only when the operator asks, or a torrent must run)

```powershell
powershell -ExecutionPolicy Bypass -File scripts/fetch_plexo.ps1 -List     # what is here now
powershell -ExecutionPolicy Bypass -File scripts/fetch_plexo.ps1           # fetch the pinned release, hash-checked
powershell -ExecutionPolicy Bypass -File scripts/fetch_plexo.ps1 -Force    # replace what is there
```

The fetcher verifies every sha256 **before** anything lands, refuses by name on a mismatch, and never
runs an installer for you. Windows will say "Windows protected your PC" on Plexo's first launch —
**More info**, then **Run anyway**: the installer is not code-signed yet.
