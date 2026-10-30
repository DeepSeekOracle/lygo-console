# Rail network — how it is wired and how to test it

Shipped 2026-10-26 (`a1d5997`). Before this, "trains" were markers lerping in a straight line between
two depot **centroids**: no track following, no stops, no money. That is gone.

## The wiring

| Piece | Where | Rule |
|---|---|---|
| Depot is a rail node | `hasRail()` | `rail[t.i] === 1 \|\| SVC[svc[t.i]] === "rail"` — track laid up to a station connects, and neighbouring track tiles draw a segment pointing at it |
| Route | `railRoute(a, b)` | BFS over `hasRail` tiles, 4-neighbour, depot-to-depot, capped at 400 tiles. Returns `null` when track does not actually connect |
| Train | `spawnTrain()` | holds `route`, `idx`, `seg`, `dwell`, `demandA/demandB` — falls back to the old lerp when `route` is null |
| Movement | the `S.trains` update | `seg += speed * dt * sim * 3.2` tiles; at either end: `dwell = DWELL` (3.5 s), reverse, `serviceStop()` |
| Money | `serviceStop()` | riders = `min(TRAIN_CAP 40, homes within 5 tiles of the station)`; `S.cash += riders * FARE (1.1)`; `S.rail.{riders,fares,stops,trips}`; small mood bump |
| Draw | `(S.trains \|\| []).forEach` | position interpolated between `route[idx]` and `route[idx+1]`; 3 cars drawn along `ang` |

QA hooks: `LC.railRoute({x,y},{x,y})`, `LC.railInfo()`, `LC.railStats()`.

## Tricks that cost time (read these first)

1. **Regions are world-offset.** `railCentres()` uses `r.rx * RS + …`. Scanning `0..RS` for tiles finds
   nothing in a claimed region that sits at `rx=1`. Scan `0 .. LC.GW * LC.RS`.
2. **A rail link needs TWO claimed regions that each hold a depot.** `S.links` only forms between
   neighbouring regions (`if (!nb || !r.rails || !nb.rails) return;`). One region, any number of depots
   → no link → no trains. Test the line only after claiming a second region.
3. **`railCentres()` averages every depot in a region into one point.** So intra-region "two stations"
   is not a thing yet; the route always runs region-centroid to region-centroid. Individual-station
   routing is the next upgrade.
4. **`LC.tileKey()` returns a ROAD key when `road[i]` is set**, even if rail is also there — it is the
   renderer's own precedence. When probing, pick clean land or you will debug a phantom.
5. **Services refuse water/mountain.** `paint({id:'rail'…})` returns false on unbuildable tiles; always
   check the boolean, never assume.
6. **`paint({id:'water'…})` (water mill) refuses most meadow tiles** — it needs water proximity. Do not
   use it as a generic "place a service" helper.

## How to verify

```js
// 1. connectivity: depot -> track -> far end
const route = LC.railRoute({ x: depotGx, y: depotGy }, { x: endGx, y: endGy });
route.length > 1                      // PASS: station is on the network

// 2. a train is running (needs a second claimed region)
LC.railInfo()                         // route length, idx, dwell, riders
LC.railStats()                        // riders / fares / stops / trips

// 3. fares reached the spine
LC.S.cash                             // rises at each station stop
```

## Verified end to end (2026-10-26)

The whole loop is now proven, not just the routing:

```
2 regions claimed (claimRegion(rx, ry, true) is the free-claim path)
1 depot each + 10 track tiles across the border -> S.links rail = 1
train spawned with route 10, names "Mill country" -> "Chapel town"
after a few stops:  stops 2 · trips 1 · riders 40 · fares $44 · dwell 2.37s
```

Notes from the run:

- **Demand is sampled when the train spawns.** Zone homes *before* the first train appears or the
  station will carry nobody. `demandA/demandB` are fixed at spawn.
- Trains spawn a few seconds after the link forms, and travel at ~0.2–0.35 tiles/s, so a 10-tile line
  takes ~30–45 s of real time. For a test, set `t.speed = 40` on the live train (poll it — the first
  pass usually runs before any train exists).
- Fares land in `S.cash`; the rest of the cash delta in a test is ordinary tax from the homes you
  zoned, so read `LC.railStats().fares`, not the cash difference.
