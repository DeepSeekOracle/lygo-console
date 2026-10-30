# Building tiles and animation from scratch

Everything here is verified against the shipped engine. Read `RAIL-TILES.md` first for the geometry.

## 1. The registration contract

A tile sprite is **128×128**, transparent outside the diamond, with the diamond at:

```
N(64,64)  E(128,96)  S(64,128)  W(0,96)      centre C(64,96)
```

`city.js` draws it as `drawImage(img, x, y - 63*zoom, 128*zoom, 128*zoom)`. Content that is not
registered to that diamond will float or sink. Objects that rise (buildings, towers) still live in the
same 128×128 canvas — they simply paint above `y=64`.

**Test a new sprite before wiring it:**

```powershell
C:\Python313\python.exe -c "from PIL import Image; im=Image.open(r'<file>.png').convert('RGBA'); print(im.size, im.getchannel('A').getbbox())"
```

Expect `(128, 128)` and a bbox whose top is ≥ 64 for ground art.

## 2. Generating the art

Forge runs local Qwen-Image 2.1. Use the CLI, not the UI, when you want a repeatable command:

```powershell
cd <DRIVE_D>\LYGO\asset-foundry
C:\Python313\python.exe forge.py generate --help        # prompt, size, seed, out
C:\Python313\python.exe forge.py cutout   --help        # chroma-key to RGBA
C:\Python313\python.exe forge.py pixelate --help        # deterministic palette quantise
C:\Python313\python.exe forge.py sheet    --help        # uniform sprite sheet
C:\Python313\python.exe forge.py validate --help        # asset / sheet / tile checks
```

Working recipe for one tile:

1. **Concept** — write the silhouette in words ("small stone water mill, wheel on the left, slate roof").
2. **Generate** at 1024×1024 with a magenta or flat background, fixed seed recorded in the pack.
3. **Cutout** to RGBA, then **pixelate** to a chunky 3–5 tone ramp.
4. **Register** — scale to 128×128, paste so the footprint touches the diamond edges, add a
   1–2 px contact shadow at 35–45 % black.
5. **Validate** dimensions + bbox, then look at it at 100 % (`read_image`).

Never generate at 128×128 directly; generate large, cut out, then downsample — that is where the
readable silhouettes come from.

## 3. Wiring a tile in

1. Drop the PNG in `assets/tiles/<family><id>_<subject>.png`.
2. Add to `assets/tiles/manifest.json`: `"<key>": ["<file>.png"]`.
3. Reference `<key>` from `city.js`: `ROAD`, `RAIL`, `SVC_SPRITE`, `themeVariant()`, or a landmark.
4. Bump `city.js?v=` in `index.html` (manifest is fetch-busted, PNGs are not).
5. Screenshot at seed 12345 and compare.

## 4. Animation — what the engine actually supports

The loader is:

```js
img.src = "assets/tiles/" + man[key][0];   // first file only
```

So `b04_windmill__0.png` / `__1.png` used to be dead weight — only `man[key][0]` was loaded.

**That is fixed.** Commit `a95948d` (2026-10-26) added a real frame-swap hook, and the windmill spins on
the live site. Animation is now a data change, not an engine change.

### Route A — frame swap (this is what ships, use it)

Implemented in `city.js`. Two pieces:

```js
// 1. the allowlist — add a key here and it animates; value is the frame duration in ms
const ANIM_SPRITES = { b04_windmill: 420, b05_watermill: 420 };
const ANIM_FRAMES = {};

// 2. loadSprites() now loads the remaining frames for listed keys
if (ANIM_SPRITES[key] && man[key].length > 1) {
  ANIM_FRAMES[key] = man[key].map((file, fi) => { /* one Image per frame */ return f; });
}

// 3. animateSprites(ts) runs at the top of drawFrame()
const f = Math.floor(ts / (ANIM_SPRITES[key] || 400)) % frames.length;
SPRITES[key] = frames[f];
```

**To animate a tile:** list every frame in `manifest.json` in animation order, then add one line to
`ANIM_SPRITES`. Nothing else.

Rules:
- Derive the frame from the shared timestamp, never a per-tile counter, or tiles desync and shimmer.
- Frames must share the same 128×128 registration — a frame with different geometry will jitter.
- 2–4 frames, 350–600 ms. More frames cost load time on every visit.
- The key stays `SPRITES[key]`, so every existing draw path (tiles, landmarks, inspector) follows for
  free.

**Shipped example:** `b04_windmill` and `b05_watermill` at 420 ms. They are the `mill` theme's
Homes/Works level-1 building — see `MILL-THEMES` note below.

### Route B — sprite sheet

One PNG with N cells side by side, drawn with a source rectangle:

```js
drawImage(sheet, frame * 128, 0, 128, 128, x, y - 63*zoom, 128*zoom, 128*zoom);
```

Better for fire/smoke (6 frames) than for terrain.

### Route C — code transform

Rotate or offset a still sprite. This is how windmill sails and clock hands should work:
one sprite, `ctx.translate` + `ctx.rotate(angle)`, angle from the game clock. Zero extra art.

### Budget

| Element | Frames | Loop | Notes |
|---|---|---|---|
| water / foam | 3–4 | 160 ms | desync by tile hash so it doesn't pulse as one block |
| fire | 6 | 120 ms | additive, warm |
| smoke | 4 | 180 ms | rises 2 px per loop |
| train wheels | 2 | 90 ms | offset phase per car |
| windmill / clock | 0 | — | code rotation |

Every animation needs a still first frame that reads in a screenshot.

## 5. Verifying animation

**Do not use a pixel diff.** The day/night tint redraws the whole canvas continuously, so a two-shot
diff is either swamped by global lighting or (if the mill is off-camera) exactly zero. Both outcomes
are useless.

Ask the page instead. `city.js` ships three QA hooks on `window.LC`:

| Hook | Returns | Use |
|---|---|---|
| `LC.animInfo()` | per animated key: `frames`, `loaded`, and the `current` frame filename | proves the swap is running |
| `LC.findSprite(key)` | coordinates of built tiles resolving to that sprite | finds a windmill to look at |
| `LC.tileKey(gx,gy)` | `{key, theme, zone, level, terr}` using the renderer's own rule | explains why a tile looks the way it does |

Canonical check (works on localhost **and** on the live URL):

```js
const t1 = await page.evaluate(() => LC.animInfo());
await page.waitForTimeout(450);            // > one frame period
const t2 = await page.evaluate(() => LC.animInfo());
// PASS when t1[key].current !== t2[key].current and loaded === frames
```

This is how the windmill was signed off on production: both keys reported `frames=2 loaded=2`, and
`b04_windmill__0.png` flipped to `b04_windmill__1.png` across a 450 ms sample.

### Where windmills actually are

`b04_windmill` / `b05_watermill` are the **`mill` theme's Homes and Works level-1 building**. The
starting region is `oldtown`, whose equivalents are `b01_cottage_red` and `b03_blacksmith_forge` — so a
brand-new city contains **no windmill**, no matter how far you scroll. To see the spin:

```js
// force the theme, then build Works
for (let dx = -6; dx <= 6; dx++) for (let dy = -6; dy <= 6; dy++) {
  const t = LC.regionOf(239 + dx, 239 + dy);
  if (!t) continue;
  t.r.theme = 'mill';
  t.r.zone[t.i] = 3; t.r.level[t.i] = 1;   // Z.I, level 1
}
LC.findSprite('b04_windmill');               // then frame those coordinates
```

Themes live in `city.js` as `THEME = { oldtown, mill, watch, harbour }`; each has `R`/`C`/`I` arrays
indexed by level.

### Animation is not a static-screenshot feature

If a reviewer only looks at one screenshot, an animated tile is indistinguishable from a still one.
Always pair the visual with `animInfo()` evidence, and say which frame was live.

## 6. Don't

- Don't bake day/night into sprites — the engine tints globally (`night > 0.18`).
- Don't animate more than one property per sprite.
- Don't add art without a manifest entry; it will never load.
- Don't put a 128×64 sprite anywhere near the ground layer.
