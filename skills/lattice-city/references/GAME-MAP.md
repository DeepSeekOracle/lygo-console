# Lattice City — exact locations

Verified 2026-10-26 on GAMEPC.

## The two live sites

### 1. excavationpro.ca — CURRENT
- URL: https://excavationpro.ca/games/lattice-city/
- Repo: `DeepSeekOracle/excavationpro-ca` (public, GitHub Pages, ~1 GB)
- Repo path: `games/lattice-city/`
- **Publishing checkout: `D:\excavationpro-ca`** ← commit and push from here
- Second checkout (do not publish from it): `<USER>\source\excavationpro-ca`
- Page: `index.html` → `<script src="./city.js?v=...">` + `./radio.js` + `city.css?v=...`
- Engine: `city.js` — classic script, wrapped in an IIFE, exports `window.LC`

### 2. chatagent.ca — OLD, stale
- URL: https://chatagent.ca/games/lattice-city/
- Repo: `DeepSeekOracle/chatagent` (public, GitHub Pages, ~2 GB)
- Repo path: `games/lattice-city/`
- **Checkout: `<USER>\source\chatagent`** (12 unpushed commits as of 2026-10-26)
- Page: `index.html` is a 31-line Vite shell → `assets/index-BqpfSHLf.js` + `assets/index-DjMziVNJ.css`
- Engine: the **bundle**. There is no `city.js` here. It renders `<div id="root">` (React shell),
  not the `#map` canvas — the two builds are not the same program any more.
- Open task: replace this deployment with the excavationpro version once the rail fix is signed off.

### Why both exist
Two different builds, same URL path, same tile art. A change to one never reaches the other.
Check `Rule 0` in `SKILL.md` before touching either.

## The test build (work here)

```
<DESKTOP>\LATTICE-CITY-BUILD
```
- Plain copy of `D:\excavationpro-ca\games\lattice-city` (no `.git`), 408 files.
- Served with `C:\Python313\python.exe -m http.server 8791 --bind 127.0.0.1`.
- URL: http://127.0.0.1:8791/
- Nothing here is published by itself. Copy verified files back to the repo to ship.

## Files inside `games/lattice-city/`

| Path | What it is |
|---|---|
| `index.html` | page, SEO/OG/AdSense, loads `city.js?v=` + `city.css?v=` + `radio.js` |
| `city.js` | the whole game (5,281 lines): world, iso renderer, economy, population |
| `city.css` | HUD/palette styling |
| `radio.js` / `radio.json` | LYGO Radio dock |
| `assets/tiles/manifest.json` | `key -> [file, ...]` index of every sprite |
| `assets/tiles/*.png` | 128×128 ground/building sprites (~300 files) |
| `assets/tiles/gen/` | generated terrain + rail track, loaded for `gen_*` keys |
| `tools/make_rail_tiles.py` | regenerates the 11 rail sprites |
| `assets/index-BqpfSHLf.js` | leftover Vite bundle — **dead weight on this site** |
| `cover.jpg`, `og.jpg`, `card-square-1080.jpg` | social art |

## Key constants in `city.js`

```js
const RS = 160;                 // region size in tiles
const TW = 128, TH = 64;        // tile sprite box
const HALF_W = 64, HALF_H = 32; // iso step per tile
const GROUND_OFFSET = 63;       // sprite drawn at y - 63*zoom
window.LC = { S, cam, paint, tileToScreen, screenToTile, save, load, counts, ... };
```

Tile draw (one line, same for road / rail / building):
```js
getCtx().drawImage(SPRITES[key], s.x, s.y - GROUND_OFFSET * cam.zoom, TW * cam.zoom, TW * cam.zoom);
```
So **every** ground sprite must already be 128×128 with its diamond in the bottom half.

## Repos and remotes

| Local path | Remote | Notes |
|---|---|---|
| `D:\excavationpro-ca` | `github.com/DeepSeekOracle/excavationpro-ca` | canonical publisher |
| `<USER>\source\excavationpro-ca` | same | second checkout, other agent's city work |
| `<USER>\source\chatagent` | `github.com/DeepSeekOracle/chatagent` | old game + site |
| `D:\chatagent` | same | older working copy |

Publishing a repo that is checked out twice is how the 2026-10-26 divergence happened. The
importers now reconcile (fetch → rebase → push-retry), but prefer one publisher.

## Other asset locations on the drives

| Path | Contents |
|---|---|
| `<MEDIA>` | generated media: sd-cli, piper, Qwen-Image/SDXL checkpoints |
| `<MODELS>` | model vault |
| `<USER>\source\LYGOapps\node_modules` | Playwright 1.62.1 for screenshots (`$env:NODE_PATH`) |
| `C:\Python313\python.exe` | the interpreter to use (`python3` is a WindowsApps stub) |
