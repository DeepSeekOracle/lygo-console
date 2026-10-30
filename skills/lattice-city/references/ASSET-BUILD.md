# Asset build — tiles, animations, audio, icons

The exact recipe. Read `ART_BIBLE.md` in the project first: the diamond registration, the locked
palette and the animation rules are non-negotiable.

## 1. A building or prop sprite

1. **Prompt** for an isometric building on a plain background, single object, no shadow, 3/4 view,
   chunky pixel-art clusters. Keep the subject inside the diamond footprint: wide base, low
   silhouette. Style anchor line for every prompt:
   `isometric game asset, 2:1 diamond footprint, pixel art, 3-5 tone ramp, light from upper left, no ground shadow, plain white background`
2. **Generate**:

   ```powershell
   pwsh -File tools\generate_image.ps1 -Prompt "<prompt>" -Out "<MEDIA>\tiles\b57_observatory.png"
   ```

   Qwen-Image 2.1, 1536×864, 20 steps, CFG 2.5, guidance 3.5, `euler` + `flux`, `--offload-to-cpu`.
   Measured 323 s. One heavy engine at a time on the 8 GB card.
3. **Cut out** the subject (rembg or PIL threshold on the flat background), trim to content, keep a
   2 px margin.
4. **Pixelate + register**: resize so the base spans exactly 128 px wide, paste so the object's base
   sits on the diamond's centre line (`y = 96` in the 128×128 box), and the diamond occupies the bottom
   half: `N(64,64) E(128,96) S(64,128) W(0,96)`.
5. **Palette snap** to the locked ramp (`ART_BIBLE.md`), 3–5 tones per material. Check at zoom 1.
6. **Contact shadow**: a soft ellipse, `rgba(0,0,0,.38)`, drawn by the engine (`ART-103`) — do not bake
   a hard shadow into the sprite.
7. **Manifest**: `"b57_observatory": ["b57_observatory.png"]` in `assets/tiles/manifest.json`.
8. **Wire**: reference the key from `themeVariant()` or a service/decor table in `city.js`.
9. **Prove**: `node tools/shot.js <preview> out.png 3.2 12345`, then open the PNG.
10. **Version**: bump `ASSET_VER` (city.js) and `city.js?v=` (index.html).

Batch runs: prefer the **LYGO Asset Foundry** worker so the manifest and naming stay consistent.

## 2. An animation (water, foam, fire, flags)

- Frames must share **one registration**: same canvas size, same pivot, only the moving element changes.
  Different registration = jitter that you cannot fix in code.
- 3 frames is enough for water; 2 frames at 420 ms is a visible flicker but not motion.
- Generate with a script, not by hand: `tools/make_water_frames.py` (deterministic seed, PIL), writing
  `name__0.png … name__2.png`.
- Manifest lists every frame in order; `city.js` gets `key: ms` in `ANIM_SPRITES`.
- Pass test, never a pixel diff: two `LC.animInfo()` samples >420 ms apart must show a different
  `current` frame and `loaded === frames`.
- Day/night tint redraws the canvas, so image diffs are meaningless for animation.

## 3. Rail / road tiles

Generated, not drawn: `tools/make_rail_tiles.py` (deterministic, seeded per tile name). Band slopes are
±0.5, joints must seal, no alpha-only stamps over zeroed RGB (that produced black discs at joints).
Never stretch a 128×64 sprite — that is the "raised plank" bug.

## 4. Audio

The game has no sample bank for cues. Build it in code:

1. **Bus** (`AUD-201`): `master → music/sfx`, gains persisted under `lattice-city-audio`, unlocked on the
   first pointer/key gesture. Expose `LC.audio = { get, set, mute, playAll }`.
2. **Cues** (`AUD-202`): oscillator + noise + short envelope per cue (place, road, rail, zone, demolish,
   deny, coin, milestone, level-up, fire, siren, train horn, car horn, click). Peak ≤ −6 dBFS, nothing
   above 8 kHz, no clicks (attack ≥ 5 ms).
3. **Ambience beds** (`AUD-203`): generated once, not synthesised live —
   `tools/make_ambience.py` (numpy → 44.1 kHz mono WAV, 30–60 s, loop seam cross-faded). Three beds:
   day (birds + distant traffic), night (crickets + wind), rain. Crossfade 3 s by `S.hour` / `S.rain`.
   Keep total < 3 MB.
4. **Placement**: train horn on `announce()`, wheel clack loop while `tr.seg` changes, silence at dwell.
   Duck `radio.js` to 35% for 2.5 s on announcements (`AUD-205`).
5. **Proof**: analyser readout for peak level, a cue matrix in `QA.md`, and a listen pass at 13:00 and
   23:00. Never autoplay audio.

Piper (`tools/voice.ps1`) is for spoken lines only, not music or cues.

## 5. UI icons (`ART-110`)

- Inline SVG, 24×24 viewBox, 2 px stroke, `currentColor`, no fills except small accents.
- One file of symbols in `index.html` (`<svg><symbol id="i-home">…`), referenced with `<use href="#i-home">`.
- Legible at 24 px on the panel background; test at 100% zoom, not retina.
- No emoji left in the build bar, HUD or dashboards.

## 6. UI tokens (`ART-109`)

`city.css`: `--bg --panel --panel-2 --ink --ink-dim --accent --good --warn --bad --radius --gap
--font-ui --font-mono`, defined once at `:root`. Every panel uses them; no raw hex outside that block.
Keep the dark amber identity.

## 7. What to hand back as evidence

| Task type | Evidence |
|---|---|
| Tile / building | PNG path + dimensions + a zoom-1 and zoom-3 screenshot |
| Animation | `LC.animInfo()` two samples + a screenshot |
| Audio | cue name → trigger → measured peak; a listen note at two times of day |
| UI | panel screenshot at 1280×720 and 1920×1080; keyboard pass note |
| Engine | before/after numbers from the F3 overlay, same seed, same city |
