---
name: lattice-city
description: Build, modify, test and publish the Lattice City isometric city-builder (live at https://excavationpro.ca/games/lattice-city/, older stale copy on chatagent.ca). Use when asked to fix, upgrade, extend, tune or add art, audio, assets or engine features to Lattice City, when a tile renders wrong (rail, roads, buildings), when a fix "won't go live", or when working the game through the LYGO Forge (project <PROJECTS>\lattice-city, preview :8796, board TASKS.json, vault). Covers where the files live on disk, which engine each site loads, the Forge task workflow, the palette and 128x128 diamond contract, the image/audio program list, the test harnesses, the vault, and the traps that have already wasted three agents.
---

# Lattice City — build, test, publish

The one-page truth. Read `references/GAME-MAP.md` for every path, `references/RAIL-TILES.md`
for the tile art, `references/RAIL-NETWORK.md` for how stations, routes, trains and fares are wired,
`references/ART-PIPELINE.md` for making and animating tiles, `references/FORGE.md` for the task board,
dispatch rules, workers and board file format, `references/ASSET-BUILD.md` for the exact programs and
step-by-step art/audio recipes, and `references/PUSH-PROCEDURE.md` for publishing.

## Where the work happens (2026-10-30)

| | Path |
|---|---|
| Forge project (edit here) | `<PROJECTS>\lattice-city` — the game the preview serves is `site/` |
| Preview | `http://127.0.0.1:8796/preview/lattice-city/` |
| Board | `<PROJECTS>\lattice-city\TASKS.json` (v2 program, 47 tasks, `RAIL-000` shipped) |
| Publishing repo | `D:\excavationpro-ca`, path `games/lattice-city` |
| Vault (reference + cold zip) | `<EDRIVE>\LYGO GAMES AND WEBPAGES\lattice-city\` |
| Live | https://excavationpro.ca/games/lattice-city/ |

Do **not** build in `D:\excavationpro-ca` (shared, 1 GB, other agents) and do not experiment in the
Desktop build. Edit `site/`, prove it on the preview, then ship through `REL-701` only.

## Rule 0 — two sites, two engines, one shared art folder

| Site | Engine the page loads | Repo | Status |
|---|---|---|---|
| **https://excavationpro.ca/games/lattice-city/** | `city.js` (classic script) + `radio.js` | `DeepSeekOracle/excavationpro-ca` | **LIVE / current** |
| https://chatagent.ca/games/lattice-city/ | `assets/index-BqpfSHLf.js` (Vite bundle) | `DeepSeekOracle/chatagent` | **OLD / stale** |

**This is why three agents "fixed" the game and nothing changed.**
- Editing `city.js` does nothing on chatagent.ca — that page never loads it.
- Editing `assets/index-BqpfSHLf.js` does nothing on excavationpro.ca — that page never loads it.
- The commit `fc5aff6` "fix: railroad tile isometric projection" edited **only the bundle**, so the
  live site kept the broken sprites.

Confirm which engine is live before you change anything:

```powershell
(Invoke-WebRequest https://excavationpro.ca/games/lattice-city/ -UseBasicParsing).Content `
  -split "`n" | Select-String 'city\.js|index-.*\.js'
```

## Rule 1 — build on the desktop, never in git

The publishing checkout is **`D:\excavationpro-ca`** (1 GB repo, several agents, remote
`DeepSeekOracle/excavationpro-ca`). Do not experiment in it.

Work in the **test build**:

```
<DESKTOP>\LATTICE-CITY-BUILD      <- edit here
http://127.0.0.1:8791/                          <- boot here
```

Bootstrap / refresh it from the live repo (always pull LIVE first):

```powershell
$src = 'D:\excavationpro-ca\games\lattice-city'
$dst = '<DESKTOP>\LATTICE-CITY-BUILD'
git -C 'D:\excavationpro-ca' fetch origin; git -C 'D:\excavationpro-ca' status -sb   # must be clean
robocopy $src $dst /E /NFL /NDL /NJH /NJS /NP
cd $dst; C:\Python313\python.exe -m http.server 8791 --bind 127.0.0.1
```

`index.html` fetches `assets/tiles/manifest.json`, so the game **must** be served over HTTP —
opening `file://` gives a black canvas.

## Rule 2 — look at it, don't assume

`screenshots/` proof or it didn't happen. The harness places real road + rail tiles on a
deterministic map and shoots the canvas:

```powershell
$env:NODE_PATH = '<USER>\source\LYGOapps\node_modules'   # Playwright 1.62.1
node tools\shot.js        http://127.0.0.1:8791/ out.png 3.2 12345   # straight road + rail strips
node tools\shot_shapes.js http://127.0.0.1:8791/ shapes.png 5.0 12345 # elbow, tee, crossing
```

The 5th argument is the RNG seed — same seed means the same map, which is the only way a
before/after comparison means anything. Then `read_image` the PNG and actually look.

The page exposes `window.LC` (`S`, `cam`, `paint`, `tileToScreen`, `counts()`), so tiles can be laid
programmatically instead of clicking:

```js
LC.S.cash = 5e6;
LC.paint({ id: 'railTrack', name: 'Rail track', cost: 80 }, gx, gy);
LC.cam.zoom = 3; const p = LC.tileToScreen(gx, gy);   // tileToScreen reads cam, so set zoom first
LC.cam.x += c.clientWidth / 2 - p.x;
```

Three QA hooks ship in `city.js`, and they are the only honest way to check animation (a pixel diff is
swamped by the day/night tint):

- `LC.animInfo()` — per animated key: frames, how many loaded, and the live frame filename.
- `LC.findSprite(key)` — coordinates of built tiles resolving to a sprite (finds a windmill).
- `LC.tileKey(gx, gy)` — the sprite key, theme, zone and level the renderer used.

Two `animInfo()` samples >420 ms apart must report a different `current` frame. That is the pass test.

## Rule 3 — tile geometry is 128×128, diamond in the bottom half

Every ground sprite is a 128×128 PNG drawn into a 128×128 box at `y - 63*zoom`:
diamond `N(64,64) E(128,96) S(64,128) W(0,96)`, centre `(64,96)`, band slopes **±0.5**.
A 128×64 sprite is stretched to double height and floats above the grid — that is the entire
railroad bug, and it is why the art must be regenerated rather than nudged. Full spec:
`references/RAIL-TILES.md`.

## Rule 4 — publishing is a decision, not a step

Never push without Justin's explicit yes. When he says go, follow
`references/PUSH-PROCEDURE.md` exactly: dirty-repo gate, bump the cache-buster in `index.html`,
commit only `games/lattice-city`, push, wait for the Pages build, then verify the live URL.

## Find any asset in one command

Do not grep `city.js` and guess. The game has a generated engineering index:

```powershell
C:\Python313\python.exe tools\map_game.py --grep windmill     # lookup, prints matches only
C:\Python313\python.exe tools\map_game.py                     # rewrite references/GAME-INDEX.txt
```

`references/GAME-INDEX.txt` (691 lines) contains: where the game lives, page wiring, every file, the
constants, all 202 functions with line numbers, all 280 sprite keys with frame count + pixel size +
who uses them, **the theme → zone → level → sprite table**, the service and rail maps, orphan/missing
PNG hygiene, and the browser QA hooks.

Use `--grep` first for any question like "where is X". Example — the windmill:

```
b04_windmill   2 frame(s)  128x128  THEME[mill].R[lvl 1], THEME[mill].I[lvl 1]   ANIMATING
```

That one line answers "which sprite, how many frames, which theme rolls it". Re-run the tool after
touching `city.js`, `manifest.json` or the tile folder — a stale index is worse than none.

## Working the Forge board (the normal way to build now)

The Forge project is `<PROJECTS>\lattice-city`. It has `forge.json`, `TASKS.json`, `GDD.md`,
`ART_BIBLE.md`, `TECH_SPEC.md`, `QA.md`, `docs/GAME-INDEX.txt` and a `site/` folder that the preview
serves. The board is the schedule; do not invent work outside it unless Justin asks.

```powershell
# what is next (the API returns the lowest task whose deps are done)
(Invoke-WebRequest http://127.0.0.1:8796/api/projects/lattice-city/tasks -UseBasicParsing).Content
# dispatch a task to a worker
Invoke-RestMethod http://127.0.0.1:8796/api/build -Method Post -ContentType application/json `
  -Body '{"project":"lattice-city","task":"ART-101"}'
# note a build in the append-only schematic ledger
Invoke-RestMethod http://127.0.0.1:8796/api/projects/lattice-city/schematic/ledger -Method Post `
  -ContentType application/json -Body '{"note":"what changed","kind":"build","author":"you"}'
```

Rules that keep three agents from fighting:

1. **One task at a time on `city.js`.** Two dispatches that both write the same file = the second
   overwrites the first. Art/audio/QA tasks may run beside an engine task only if the file sets differ.
2. **The task's `files`, `worker` and `prove` fields are the contract.** Do what they say, run the
   `prove` command, paste the output into `evidence`, then mark it done.
3. `status` is one of `pending`, `active`, `done`, `blocked` — never `in_progress`.
4. A task is done when the preview shows it, not when the file is saved. A doc-only change is `blocked`.
5. Record durable decisions in `DECISIONS.md` and refresh `docs/GAME-INDEX.txt` after touching
   `city.js`, `manifest.json` or the tile folder.

Full board/dispatch detail: `references/FORGE.md`.

### Free build — the no-rails mode (2026-10-30)

The board has a permanent task **`GENERAL-000` — "GENERAL BUILD — tell the Forge what to build"**.
Select it in the TASKS tab, type the instruction into the Build Desk box, dispatch. There is no scope,
no dependency and no phase gate; the instruction is the brief.

```powershell
Invoke-RestMethod http://127.0.0.1:8796/api/build -Method Post -ContentType application/json -Body (@{
  project="lattice-city"; task="GENERAL-000"; model="hermes"
  prompt="add rain to the map and make the trains honk at stations"
} | ConvertTo-Json)
```

It never closes and is never picked by "next". If the instruction is empty the API answers 400.

### What a build brief says now

Since 2026-10-30 every brief (board task or free build) carries a **BUILD CONTRACT** that overrides the
old role text: edit files in `site/`, run the task's `prove` command, return `RESULT / FILES CHANGED /
PROOF / BLOCKERS`. The old lines that told workers to *"update the owning artifact"* and listed **docs**
as "Outputs expected" are gone — those were why six hours of agents produced 47 summaries and one audio
bus.

The board also no longer rewrites itself: `ensure_task_chain()` used to chain every task to the previous
one on each server start, which made exactly one task dispatchable and blocked the free-build slot.
Explicit dependencies are now preserved, and an explicit dispatch ignores dependencies entirely.

### Which worker actually builds (measured 2026-10-30)

| Worker | Result on this game |
|---|---|
| `hermes` | **builds**: edited `site/city.js`, ran `node --check`, returned RESULT / FILES / PROOF. Use this for build tasks. |
| `opencode/deepseek…`, `gemini/…` | free quota exhausted on 2026-10-30 → fall through to the 4B local model |
| `nvidia/…`, `local/…` (4B) | writes an unrelated `src/*.js` module; now **rejected** by the no-build gate |

Since 2026-10-30 a job is `failed` unless a file under `site/` (or the task's `files`) changed. A
4B summary run shows `NO BUILD: no change in site` in the job log instead of a green tick.

### Proving a simulation change

```powershell
node tools/probe.js http://127.0.0.1:8796/preview/lattice-city/ 12345 20
```

Builds a deterministic town (12x10 or the largest clear block), waits, then prints JSON: `pop`, `jobs`,
`demand {r,c,i}`, `income`, `upkeep`, `mood`, `built`, `landValue`, `crime`, `pollution`, `taxRate`,
`trains`, `rail`. No prose. That is the acceptance readout for every SIM task.

## Visuals: what to use, and the order to do it in

The contract is in `ART_BIBLE.md` (diamond registration, locked palette, animation rules) and the
step-by-step recipe is `references/ART-PIPELINE.md`. The short version:

| Want | Use |
|---|---|
| A new building / prop | `tools\generate_image.ps1` (Qwen-Image 2.1, 1536×864, 20 steps, CFG 2.5, guidance 3.5, `euler`+`flux`, `--offload-to-cpu`) — ~323 s per image on the 4060 Ti; or the Forge **Qwen-Image 2.1** worker |
| Retouch / palette work on a tile | **PD Howler 11** worker, or PIL on `C:\Python313\python.exe` |
| Procedural frames (water, foam, glow) | a `tools/make_*_frames.py` script; deterministic seed, same 128×128 registration every frame |
| Pixel-perfect check | zoom 1 and zoom 3 screenshots; a tile that only reads at 400% is wrong |
| Wire it in | add the PNG to `assets/tiles/`, a `key: [file]` entry to `manifest.json`, reference the key from `city.js`; for animation add `key: ms` to `ANIM_SPRITES` |
| Prove it | `node tools/shot.js <preview> out.png 3.2 12345` then **look** at the PNG |
| Cache | bump `ASSET_VER` in `city.js` **and** `city.js?v=` in `index.html`, or the CDN serves old art for ~10 min |

Palette: use the measured ramp in `ART_BIBLE.md` ("Locked palette"). At most two off-ramp colours per
new sprite, and they go back into that table.

**Sound** works the other way: the game has no audio files for cues — synthesise them in code through
the audio bus (`AUD-201`), and only ambience beds become WAVs (`tools/make_ambience.py`). Piper is for
spoken lines only. `radio.js` is the existing listen portal; duck it, never autoplay it.

## Vault: the reference copy

`<EDRIVE>\LYGO GAMES AND WEBPAGES\lattice-city\` holds one `RELEASE-READONLY/<commit>/` per pushed
release (ACL write-locked, readable), a cold `.zip`, `SUBMISSIONS/` for pre-overwrite backups, and an
append-only `LEDGER.jsonl`. Snapshot after every push:

```powershell
C:\Python313\python.exe tools\vault_snapshot.py snapshot    # HEAD -> vault, locked, ledgered
C:\Python313\python.exe tools\vault_snapshot.py list
```

Restore direction is **vault → working copy**. If a file comes back empty or corrupt, read it from
`RELEASE-READONLY\<newest>\<path>`, rebuild, write it into `site/`, then re-submit. Never write into
the vault.

## Checklist for any change

1. `git -C D:\excavationpro-ca fetch origin`, confirm the tree is clean, note `HEAD`.
2. Pick the next board task (`GET /api/projects/lattice-city/tasks`); read its `files`, `worker`, `prove`.
3. Make the change in `<PROJECTS>\lattice-city\site` (the preview serves it live). Never edit in
   `D:\excavationpro-ca`.
4. `node --check site\city.js` for JS; run the task's `prove` command against
   `http://127.0.0.1:8796/preview/lattice-city/` at seed 12345; **look** at the PNG with `read_image`.
5. Compare against the previous shot at the **same seed and same camera**.
6. Fill the task `evidence` (command, output, screenshot) and mark it `done`; append the schematic ledger.
7. Publishing is a separate, explicit decision (`REL-701`): copy only the changed files into
   `D:\excavationpro-ca\games\lattice-city`, bump the cache-buster, `git status` → `git diff --stat`,
   commit, push, wait for Pages, run `verify_live.ps1`, compare the LF-normalised hash, then
   `vault_snapshot.py snapshot`.

## Traps that have already cost time

| Symptom | Cause | Fix |
|---|---|---|
| Fix is in the file but live page unchanged | Edited `city.js` for chatagent, or the bundle for excavationpro | Check Rule 0 first |
| New PNG not showing after push | Sprite URLs have no cache-buster; Pages CDN holds ~10 min | Bump the query in `index.html`, or wait out `Cache-Control: max-age=600` |
| Rail tiles look like raised planks | 128×64 sprite stretched into a 128×128 box | Regenerate with `tools/make_rail_tiles.py` |
| Black discs at every rail joint | Old generator stamped alpha opaque over zeroed RGB | Fixed 2026-10-26 — do not reintroduce alpha-only stamps |
| Rail crossing missing one line | `paths_for()` returned one straight for a 4-arm tile | Fixed 2026-10-26 — one straight per opposite pair |
| A tile has 2 frames but never moves | Key is not in `ANIM_SPRITES`, so only frame 0 loads | Add `key: ms` to `ANIM_SPRITES` in `city.js` (hook shipped `a95948d`) |
| "I can't see the windmill spin" | Windmills are the `mill` theme's Homes/Works level-1 building; `oldtown` has none | Theme a region `mill` and build Works (see `references/ART-PIPELINE.md`) |
| Animation looks jittery | Frames with different registration, or per-tile frame counters | One 128×128 registration, frame index from the shared `ts` |
| `ship.py` reports `LIVE DIFF` on every file | CRLF checkout vs LF blob served by Pages | Already handled by `_lf`; never hash raw bytes |
| Stamp says confirmed but nothing was audited | Stamp written without `--human` and edited by hand | Forbidden — append a new stamp, leave the old line |
| Live art looks old after a push | Sprite URLs cached, `ASSET_VER` not bumped | Bump `ASSET_VER` in `city.js` + `city.js?v=` in `index.html` |
| Every drop/job fails in git | Two checkouts of one remote diverged | Use the reconcile gate in `music_drop`/`signal_drop`; never `reset --hard` |
| Black canvas, no errors | Opened via `file://` | Serve over HTTP |
| Modal blocks the screenshot on first run | `firstRun()` on a clean profile | Harness clicks `#modal-close` |

## Where art comes from

- Hand-placed tile art: `games/lattice-city/assets/tiles/*.png`, indexed by
  `assets/tiles/manifest.json` (`key -> [file, ...]`, first entry wins).
- Generated terrain: `assets/tiles/gen/` (mountains, hills, ridges, rail track).
- Rail generator: `tools/make_rail_tiles.py` — deterministic, seeded per tile name.
- Big generated media lives in `<MEDIA>`; model vault `<MODELS>`.

Adding a tile = drop a 128×128 PNG in `assets/tiles/`, add a manifest entry, and reference the key
from `city.js` (see `RAIL`, `ROAD`, `SVC_SPRITE`, `themeVariant`). No page edits needed.

**Creating art from scratch and animating it: `references/ART-PIPELINE.md`.** It carries the
registration contract, the Qwen-Image → cutout → pixelate → register recipe, the manifest wiring, and
the live animation path: multi-frame tiles are driven by `ANIM_SPRITES` in `city.js` (shipped
`a95948d`), so animating a tile is a manifest + one-line data change, not an engine change.

## Preview the real game, then stamp the ship

The Forge project holds a synced copy of the game, so the Forge preview runs the actual thing:
**http://127.0.0.1:8796/preview/lattice-city/**

```powershell
python tools\ship.py sync --check-live        # repo -> project, hash, compare to LIVE
python tools\ship.py stamp --signer delta     # candidate stamp (NOT an audit)
python tools\ship.py stamp --signer delta --human justin --note "signed off"
```

- `sync` copies `D:\excavationpro-ca\games\lattice-city` into the project, writes
  `docs/VERSIONS.json` (sha256 per key file, commit, live match) — 408 files, 376 tiles.
- `stamp` writes one bundle hash over those files + the commit to `docs/SHIP-STAMPS.jsonl`.
  **Only a full ship with agent + human sign-off gets a confirmed stamp**; without `--human` it is
  recorded as `candidate` and says so out loud.
- During build nothing is hashed. The schematic ledger stays append-only and plain; the stamp is the
  receipt at the end.

**Trap:** never hash working-tree files against LIVE raw. Windows checks out CRLF while GitHub Pages
serves the LF blob from git, so *every* file reports DIFF. `ship.py` normalises line endings before
hashing; any comparison script must do the same.

### Hash rules (full guide: `references/HASHING.md`)

1. **Hash at boundaries, not during work.** Build → no hashes. Ship → one stamp.
2. **One stamp = one bundle hash** over the key files (`city.js`, `index.html`, `city.css`,
   `radio.js`, `assets/tiles/manifest.json`) plus the commit.
3. **Normalise before hashing** — LF, decompressed, same encoding. Raw bytes always lie on Windows.
4. **Candidate ≠ confirmed.** `--human <person>` is the only thing that makes a stamp an audit.
5. **Append-only.** Never edit a stamp; a correction is a new `kind: "void"` line.
6. **A stamp proves what was signed, not that it is good.** Quality is the human look at the preview.

## Project home

The Forge project for this game is `<PROJECTS>\lattice-city` (board, plan, art bible, QA, tech
spec). Its `skill/lattice-city` junction points back here. The Forge asset registry
(`Assets → Asset folders`) is the canonical index of where the art lives on disk — 132 folders are
already tracked; add new ones there instead of scattering a fresh tree.

**Live state (verified 2026-10-30):** commit `e2b8bd9`, cache-buster `20261030-localtrain1`, live
`city.js` LF-sha256 `91150df9…a126e448` equals the built file; `verify_live.ps1` all PASS. The vault holds
`RELEASE-READONLY\e2b8bd9` (407 files, bundle `26900b6a…`) plus `lattice-city_e2b8bd9.zip`.

### Forge → Schematic (the paper, per project)

Every project hosts one board. Forge finds it in this order:

```
docs/GAME-INDEX.txt   ← the generated engineering index (map_game.py writes a copy here)
docs/SCHEMATIC.txt
docs/SCHEMATIC.md
SCHEMATIC.md
docs/GAME-MAP.md
```

**Read it before you touch the game; append to its ledger after every upgrade.** The ledger is
append-only and lives outside the project at `…\lygo\forge\data\schematic\<slug>.jsonl`, so project
edits cannot erase the history.

API: `GET /api/projects/<slug>/schematic`, `POST /api/projects/<slug>/schematic/ledger`
(`{note, kind, author}`). Refresh the board after changing `city.js`, the manifest or the tiles:

```powershell
C:\Python313\python.exe tools\map_game.py
Copy-Item references\GAME-INDEX.txt <PROJECTS>\lattice-city\docs\ -Force
```

## Installing this skill

Canonical copy: `...\default-workspace\skills\@deepseekoracle\lattice-city`.
It is junctioned into: `<USER>\.dsh\skills\lattice-city` (DSH),
`<USER>\AppData\Local\hermes\skills\lattice-city` (Hermes),
`<CONSOLE_DIR>\skills\lattice-city` (LYGO Agent Manager console).
Edit the canonical copy only; the junctions follow it.
