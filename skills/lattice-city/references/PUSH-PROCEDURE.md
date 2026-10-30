# Publish procedure — excavationpro.ca

**Publishing is a decision, not a step.** Ask Justin before pushing. When he says yes, do exactly
this and prove it afterwards.

## 0. Preflight

```powershell
git -C D:\excavationpro-ca fetch origin
git -C D:\excavationpro-ca status -sb          # must show no dirty files
git -C D:\excavationpro-ca log --oneline -3
git -C D:\excavationpro-ca rev-list --left-right --count origin/main...HEAD
```

- Dirty tree → stop. Do not commit over another agent's work.
- `HEAD` ahead of `origin/main` by N → that is normal (an earlier push failed). Do not reset.

## 1. Copy only what you changed

```powershell
Copy-Item '<DESKTOP>\LATTICE-CITY-BUILD\assets\tiles\rail_*.png' `
          'D:\excavationpro-ca\games\lattice-city\assets\tiles\' -Force
Copy-Item '<DESKTOP>\LATTICE-CITY-BUILD\tools\make_rail_tiles.py' `
          'D:\excavationpro-ca\games\lattice-city\tools\' -Force
```

Then look at what you are about to ship:

```powershell
git -C D:\excavationpro-ca status --porcelain
git -C D:\excavationpro-ca diff --stat
```

Never `robocopy /MIR` the whole game folder into the repo, and never `git add -A`.

## 2. Bump the cache-buster

`index.html` carries the only versioning the browser respects:

```html
<link rel="stylesheet" href="./city.css?v=20261025-sim7">
<script src="./city.js?v=20261025-audiofix1"></script>
```

Change the `v=` on whichever file changed (UTC-ish timestamp + short tag, e.g. `20261026-railflat1`).
Sprite PNGs are fetched **without** a query string, so the Pages CDN can serve the old art for
`Cache-Control: max-age=600` (~10 min) after a push. If a change must appear immediately, either
touch `city.js`'s version (forces a reload) or add a version query where the sprite URL is built
(`loadSprites()` in `city.js`).

## 3. Commit

```powershell
git -C D:\excavationpro-ca add games/lattice-city/index.html `
    games/lattice-city/tools/make_rail_tiles.py `
    games/lattice-city/assets/tiles/rail_straight_ns.png
# ...list the files explicitly; check the staged list before committing
git -C D:\excavationpro-ca diff --cached --stat
git -C D:\excavationpro-ca -c user.name=DeepSeekOracle `
    -c user.email=DeepSeekOracle@users.noreply.github.com `
    commit -m "Fix rail tile registration: flat 128x128 isometric sprites"
```

## 4. Push and wait

```powershell
git -C D:\excavationpro-ca push origin HEAD:refs/heads/main
gh api repos/DeepSeekOracle/excavationpro-ca/pages/builds/latest --jq '.status'   # poll until "built"
```

Pages takes 1–2 minutes. Do not claim success before the build reports `built`.

## 5. Verify live (not local)

```powershell
pwsh -File "<skill>\tools\verify_live.ps1"
```

It proves, from the network:
1. `https://excavationpro.ca/games/lattice-city/` returns 200;
2. the page loads `city.js` (not the bundle);
3. the cache-buster is the one you just committed;
4. each rail PNG is 128×128 on the live CDN.

## 6. If the live page still shows the old thing

| Check | Why |
|---|---|
| cache-buster on the live HTML | Pages served a cached HTML page |
| `?v=` on the sprite URL | CDN holding the old PNG for up to 10 min |
| `gh api .../pages/builds/latest` | build may still be `building`, or `errored` |
| which engine the page loads | the bundle vs `city.js` trap |

## Rollback

```powershell
git -C D:\excavationpro-ca revert --no-edit <sha>
git -C D:\excavationpro-ca push origin HEAD:refs/heads/main
```

Never `reset --hard` a published branch and never force-push.
