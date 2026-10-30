# Rail tiles — geometry, generator, history

## The grid

Every ground sprite is a **128×128 PNG** whose opaque content sits in the bottom half:

```
        N (64,64)
       /        \
 W (0,96)        E (128,96)
       \        /
        S (64,128)

centre C = (64,96)     edge midpoints: N(96,80) E(96,112) S(32,112) W(32,80)
```

`city.js` draws every tile with one call:

```js
drawImage(SPRITES[key], s.x, s.y - 63 * cam.zoom, 128 * cam.zoom, 128 * cam.zoom)
```

so the sprite's diamond must already be at that position **inside its own canvas**. A sprite that is
128×64 gets stretched to 128×128, its content doubles in height and shifts up half a tile — it
floats above the ground like a ramp.

Band slope on the grid is **±0.5** (32 px vertical per 64 px horizontal). Measured from the road
sprites by sampling desaturated pixels:

| sprite | band centre (x=46 → x=82) | slope |
|---|---|---|
| `r02_road_straight_ew.png` | y 87 → 105 | **+0.5** |
| `r01_road_straight_ns.png` | y 105 → 87 | **−0.5** |

That is exactly the line `W(32,80) → E(96,112)` for EW and `S(32,112) → N(96,80)` for NS. Any rail
art that does not use these two lines will not join, no matter how it is padded.

## Which sprite a rail tile gets

`city.js` builds a 4-bit mask from the four rail neighbours and looks it up:

```js
const RAIL = {
  0: "rail_straight_ns",  1: "rail_straight_ns",  2: "rail_straight_ew",
  3: "rail_curve_ne",     4: "rail_straight_ns",  5: "rail_straight_ns",
  6: "rail_curve_es",     7: "rail_tee_nes",      8: "rail_straight_ew",
  9: "rail_curve_wn",    10: "rail_straight_ew", 11: "rail_tee_ewn",
 12: "rail_curve_sw",    13: "rail_tee_nsw",     14: "rail_tee_ews",  15: "rail_cross",
};
```

Bit values are sprite-space directions, not compass:

| bit | direction | edge midpoint |
|---|---|---|
| 1 | up-right | (96,80) |
| 2 | down-right | (96,112) |
| 4 | down-left | (32,112) |
| 8 | up-left | (32,80) |

## The generator

`games/lattice-city/tools/make_rail_tiles.py` writes 11 sprites (4 straights, 4 curves, 4 tees, 1
cross — 11 files, the four tees share names with the map). Run it with an explicit output when
working in the test build:

```powershell
C:\Python313\python.exe tools\make_rail_tiles.py --out "<build>\assets\tiles"
C:\Python313\python.exe tools\make_rail_tiles.py --check --out "<build>\assets\tiles"   # sizes only
```

How it works:
- draws at 4× (512×512) and downscales with `Image.BOX` for soft pixel edges;
- one centre-line per arm pair, from edge midpoint to edge midpoint (`paths_for`);
- a 24 px ballast bed, sleepers every 10.5 px, two rails offset ±6 px;
- clips to a slightly dilated diamond (`corner_mask`);
- deterministic per tile: `random.Random(abs(hash(name)) & 0xFFFF)`.

### Fixed 2026-10-26 (do not reintroduce)

1. **128×64 sprites** — the shipped `rail_*.png` were 128×64 with a ~0.75:1 band, so they stretched
   into raised planks. Everything is now 128×128 with ±0.5 bands.
2. **Black discs at joints** — the generator stamped an opaque circle into the alpha channel at each
   edge midpoint. The RGB underneath was still `(0,0,0)`, so the stamps rendered as black
   half-discs. Replaced by a square cap: the track runs `EXT = 4.5` px past the edge midpoint and
   the clip mask is stroked `2*EXT*S` wide, so two neighbouring caps overlap into a butt joint.
3. **Crossing lost a line** — `paths_for()` emitted a single straight for a 4-arm tile, so
   `rail_cross` drew only N–S. It now emits one straight **per opposite pair**.

### Known cosmetic gap

At very high zoom (>6) the outer edge of the ballast still shows a 1–2 px scallop where the
diamond boundary crosses the band. At normal play zoom (1–3.5) it is not visible. Fixing it means
moving from per-tile clips to a ribbon overlay pass in `city.js`; not worth the risk unless asked.

## Verifying rail art without the browser

```powershell
C:\Python313\python.exe - <<'PY'
from PIL import Image
p = r"<build>\assets\tiles\rail_straight_ew.png"
im = Image.open(p).convert("RGBA")
print(im.size, im.getchannel("A").getbbox())   # expect (128,128) and y >= 64
PY
```

A correct ground sprite reports `size (128,128)` and an opaque bbox whose top is **at or below 64**.
If the bbox starts near 0, the sprite is registered as a wall/building, not ground.

## Road reference art

`r01_road_straight_ns.png` and `r02_road_straight_ew.png` are the calibration pieces: full opaque
diamonds with a ±0.5 band. When in doubt, measure those before changing a rail sprite.
