# Hashing rules and guide

One rule above all others: **hash at boundaries, not during work.**

During a build the ledger is append-only and unhashed on purpose. Ceremony while building slows the
loop and produces stamps that mean nothing. A hash is worth something only when it marks a *ship* —
a version an agent and a human both signed off on.

## When to hash

| Moment | Hash? | Why |
|---|---|---|
| Editing a tile, wiring a system, trying an idea | **No** | nothing is settled yet |
| Appending a schematic ledger entry | **No** | the ledger is a log, not a contract |
| A commit that will be deployed | No (the commit id is already the identity) | git is the record |
| **Full ship, agent + human sign-off** | **Yes — one stamp** | this is the audit |

## What a stamp contains

`tools/ship.py stamp` writes one JSON line to `<project>/docs/SHIP-STAMPS.jsonl`:

```json
{
  "ts": "2026-10-26T15:02:11",
  "kind": "ship",
  "bundle_sha256": "<sha256 over the file manifest + commit>",
  "commit": "a1d5997",
  "files": { "city.js": "<sha256>", "index.html": "<sha256>", "...": "..." },
  "live_match": true,
  "signer": "delta",
  "human_signoff": "justin",
  "status": "confirmed",
  "note": "signed off"
}
```

`bundle_sha256` is computed as:

```
sha256( for each file sorted by path: "<rel>:<sha256-lf>\n"  +  "commit:<short-head>\n" )
```

So the stamp changes if **any** key file or the commit changes. That is the whole point.

### Key files

`city.js` · `index.html` · `city.css` · `radio.js` · `assets/tiles/manifest.json`

Art lives in `assets/tiles/*.png` — hundreds of files. They are covered indirectly by the manifest
hash only when the *list* changes; a re-exported PNG with the same name is **not** caught. If a ship
depends on specific art, hash those PNGs too and record them under `files`.

## Candidate vs confirmed

- `stamp --signer <agent>` → `status: "candidate"`. The tool prints
  **"PENDING: no --human sign-off yet. This is not an audit stamp."**
- `stamp --signer <agent> --human <person>` → `status: "confirmed"`.

A candidate is a bookmark. Only a confirmed stamp is an audit. Never present a candidate as a ship
record, and never upgrade one by editing the file — append a new confirmed stamp.

## Normalisation (non-negotiable)

Hash **logical content**, never raw wire or checkout bytes.

| Trap | Symptom | Rule |
|---|---|---|
| **CRLF** | every file reports `LIVE DIFF` while looking identical | Windows checks out CRLF; GitHub Pages serves the LF blob from git. Normalise `\r\n -> \n` before hashing **both** sides |
| **gzip/deflate** | remote hash differs from a local file you know is identical | decompress `Content-Encoding` before hashing |
| **BOM** | HTML/JS off by three bytes | do not strip silently; if one side has a BOM, that is a real difference to record |
| **Trailing newline** | last-line-only diff | keep the file as generated; never "tidy" one side |

`ship.py` already normalises line endings (`_lf`) and decompresses remote responses. Any new
comparison script must do the same or it will lie.

## Before you stamp

1. `git -C D:\excavationpro-ca status -sb` → **clean**. A dirty tree means the stamp does not describe
   what is deployed.
2. `python tools\ship.py sync --check-live` → `live match: YES`.
3. Human has actually looked at `http://127.0.0.1:8796/preview/lattice-city/` and said so.

If any of the three fails, the stamp is a candidate at best, or should not exist.

## Reading a stamp later

```powershell
Get-Content <PROJECTS>\lattice-city\docs\SHIP-STAMPS.jsonl | Select-Object -Last 1
python tools\ship.py sync --check-live     # does today's tree still match what was stamped?
```

A stamp proves: *these exact file hashes, from this commit, matched LIVE, and these people signed.*
It does **not** prove the game is good. Quality is the sign-off; the stamp only fixes what was signed.

## Corrections

- Stamps and ledger entries are **append-only**. Never edit or delete a line.
- Wrong stamp? Append a new entry with `"kind": "void"`, the old `bundle_sha256`, and the reason.
- Missing LIVE parity at stamp time? Stamp `status: "candidate"` and record `live_match: false` —
  do not omit the field.

## Exclusions

Do not hash or stamp:

- `docs/VERSIONS.json` (regenerated on every sync)
- anything under `docs/` that embeds a timestamp
- `node_modules`, `.git`, build caches
- the ledger (`data/schematic/<slug>.jsonl`) — it is the narrative, not the artifact
