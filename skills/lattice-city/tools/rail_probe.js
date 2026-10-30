// Lattice City — rail probe.
// Builds one deterministic town in a single region, places two depots joined by an
// L-shaped track, then reports what the rail system actually does (trains, routes,
// riders) and shoots the result. Same seed => same map, so before/after is fair.
//
// usage: node rail_probe.js <url> <out.png> [seed] [waitMs]
const { chromium } = require('playwright');

(async () => {
  const url = process.argv[2] || 'http://127.0.0.1:8796/preview/lattice-city/';
  const out = process.argv[3] || 'rail_probe.png';
  const seed = Number(process.argv[4] || 12345);
  const waitMs = Number(process.argv[5] || 25000);

  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 2 });
  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));

  await page.addInitScript((seed) => {
    let s = seed >>> 0;
    Math.random = () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };
    // Shift the wall clock so the city sits in daylight, but let it keep RUNNING:
    // freezing Date also freezes the day tick, and then nothing ever spawns.
    const RealDate = Date;
    const skew = new RealDate(2026, 9, 30, 13, 0, 0).getTime() - RealDate.now();
    // eslint-disable-next-line no-global-assign
    Date = class extends RealDate {
      constructor(...a) { if (a.length) super(...a); else super(RealDate.now() + skew); }
      static now() { return RealDate.now() + skew; }
    };
    try { localStorage.clear(); } catch (e) {}
  }, seed);

  await page.goto(url, { waitUntil: 'networkidle' });
  await page.waitForFunction(() => window.LC && document.querySelector('#map'), null, { timeout: 30000 });
  await page.waitForTimeout(600);
  await page.evaluate(() => {
    const m = document.querySelector('#modal');
    if (m && !m.classList.contains('hidden')) {
      const b = document.querySelector('#modal-close');
      if (b) b.click();
    }
  });
  await page.waitForTimeout(500);

  const built = await page.evaluate(() => {
    const c = document.querySelector('#map');
    LC.S.cash = 9_000_000;
    LC.S.hour = 13;                       // fixed daylight so art is comparable run to run
    const road = { id: 'road', name: 'Road', cost: 12 };
    const homes = { id: 'homes', name: 'Homes', cost: 24, zone: 1 };
    const shops = { id: 'shops', name: 'Shops', cost: 30, zone: 2 };
    const depot = { id: 'rail', name: 'Rail depot', cost: 2200, service: 'rail' };
    const track = { id: 'railTrack', name: 'Rail track', cost: 80 };

    // Deterministic on TERRAIN only: growth near the start varies with how many day
    // ticks fired before the build, and that must not move the camera between runs.
    const usable = (gx, gy) => {
      const t = LC.regionOf(gx, gy);
      if (!t) return false;
      const r = t.r, i = t.i;
      return r.terrain[i] === 0 && !r.road[i];
    };

    const centre = LC.screenToTile(c.clientWidth / 2, c.clientHeight / 2) || { gx: 80, gy: 80 };
    const W = 9, H = 7;

    const homesPos = [];
    for (let dx = -45; dx <= 45; dx++) for (let dy = -45; dy <= 45; dy++) {
      const gx = centre.gx + dx, gy = centre.gy + dy;
      const t = LC.regionOf(gx, gy);
      if (t && t.r.zone[t.i] === 1 && t.r.level[t.i] > 0) homesPos.push({ x: gx, y: gy });
    }
    const nearHome = (x, y) => homesPos.some((h) => Math.abs(h.x - x) + Math.abs(h.y - y) <= 4);

    let origin = null;
    outer:
    for (let ring = 0; ring < 50 && !origin; ring++) {
      for (let dx = -ring; dx <= ring; dx++) {
        for (let dy = -ring; dy <= ring; dy++) {
          if (Math.max(Math.abs(dx), Math.abs(dy)) !== ring) continue;
          const gx = centre.gx + dx, gy = centre.gy + dy;
          let ok = true;
          for (let x = 0; x < W && ok; x++) for (let y = 0; y < H; y++) {
            if (!usable(gx + x, gy + y)) { ok = false; break; }
          }
          if (ok) { origin = { gx, gy }; break outer; }
        }
      }
    }
    if (!origin) return { error: 'no clear 9x7 meadow near the start' };

    const { gx, gy } = origin;
    const A = { gx, gy };
    const B = { gx: gx + W - 1, gy: gy + H - 1 };      // far corner: route must dogleg

    // clear the rectangle first, so growth timing cannot change what is buildable
    const clear = { id: 'bulldoze', name: 'Bulldoze', cost: 6 };
    for (let x = 0; x < W; x++) for (let y = 0; y < H; y++) LC.paint(clear, gx + x, gy + y);

    // two depot platforms, then the L-shaped track between them
    const depotA = LC.paint(depot, A.gx, A.gy);
    const depotB = LC.paint(depot, B.gx, B.gy);
    let laid = 0;
    for (let x = 1; x < W; x++) if (LC.paint(track, gx + x, gy)) laid++;          // east run
    for (let y = 1; y < H; y++) if (LC.paint(track, gx + W - 1, gy + y)) laid++;  // south run
    for (let x = 0; x < W - 1; x++) LC.paint(track, gx + x, gy + H - 1);          // second arm
    for (let x = 1; x < W - 1; x++) LC.paint(track, gx + x, gy + 3);              // cross street

    // a small town around both ends so stations have a catchment
    for (let x = 1; x <= 4; x++) for (let y = 1; y <= 2; y++) {
      LC.paint(homes, gx + x, gy + y);
      LC.paint(shops, gx + x, gy + y + 4);
    }
    for (let x = W - 5; x <= W - 2; x++) for (let y = H - 4; y <= H - 2; y++) {
      if (LC.paint(homes, gx + x, gy + y)) {}
    }

    LC.cam.zoom = 1.8;
    const p = LC.tileToScreen(gx + W / 2, gy + H / 2);
    LC.cam.x += c.clientWidth / 2 - p.x;
    LC.cam.y += c.clientHeight / 2 - p.y;
    return { origin, A, B, depotA, depotB, laidTrack: laid, counts: LC.counts() };
  });

  console.log('built', JSON.stringify(built));
  await page.waitForTimeout(2500);

  const snap = async (label) => {
    const info = await page.evaluate(() => ({
      rail: LC.railInfo(),
      stats: LC.railStats(),
      trains: (LC.S.trains || []).length,
      links: LC.S.links.filter((l) => l.kind === 'rail').length,
      built: LC.counts().built,
      hour: LC.S.hour,
      day: LC.S.day,
    }));
    console.log(label, JSON.stringify(info));
    return info;
  };

  const canvas = await page.$('#map');

  // the train is spawned by the day tick (TICK_SECONDS = 7), then it has to run the
  // route and call at both ends before any fare exists: wait for the events, not a guess
  await page.waitForFunction(() => (LC.S.trains || []).length > 0, null, { timeout: 30000 }).catch(() => null);
  const a = await snap('t0');
  await canvas.screenshot({ path: out });

  await page.waitForFunction(() => LC.railStats().riders > 0, null, { timeout: Math.max(waitMs, 60000) }).catch(() => null);
  const b = await snap('t1');
  const fps = await page.evaluate(() => new Promise((res) => {
    let n = 0; const t0 = performance.now();
    const step = () => {
      n++;
      if (performance.now() - t0 < 2000) requestAnimationFrame(step);
      else res(+(n / ((performance.now() - t0) / 1000)).toFixed(1));
    };
    requestAnimationFrame(step);
  }));
  console.log('fps', fps);
  await page.waitForTimeout(4000);
  const c = await snap('t2');
  const out2 = out.replace(/\.png$/, '-late.png');
  await canvas.screenshot({ path: out2 });

  // close-up on the train: cars, coupling, and the line under it
  await page.evaluate(() => {
    const tr = (LC.S.trains || [])[0];
    if (!tr) return;
    const c = document.querySelector('#map');
    const tile = tr.route[Math.min(tr.idx, tr.route.length - 1)];
    LC.cam.zoom = 2.6;
    const s = LC.tileToScreen(tile.x, tile.y);
    LC.cam.x += c.clientWidth / 2 - s.x;
    LC.cam.y += c.clientHeight / 2 - s.y;
  });
  await page.waitForTimeout(700);
  await canvas.screenshot({ path: out.replace(/\.png$/, '-mid.png') });

  await page.evaluate(() => {
    const tr = (LC.S.trains || [])[0];
    if (!tr) return;
    const c = document.querySelector('#map');
    const tile = tr.route[Math.min(tr.idx, tr.route.length - 1)];
    LC.cam.zoom = 3.8;
    const s = LC.tileToScreen(tile.x, tile.y);
    LC.cam.x += c.clientWidth / 2 - s.x;
    LC.cam.y += c.clientHeight / 2 - s.y;
  });
  await page.waitForTimeout(400);
  await canvas.screenshot({ path: out.replace(/\.png$/, '-cab.png') });

  console.log('shot', out, out2, 'errors', JSON.stringify(errors.slice(0, 5)));
  console.log('VERDICT', JSON.stringify({
    trainSpawned: (b.trains || 0) > 0,
    routeTiles: (b.rail[0] && b.rail[0].route) || 0,
    trainMoved: !!(a.rail[0] && b.rail[0] && (a.rail[0].idx !== b.rail[0].idx || a.rail[0].dwell !== b.rail[0].dwell)),
    trainKeptRunning: !!(b.rail[0] && c.rail[0] && b.rail[0].idx !== c.rail[0].idx),
    riders: c.stats.riders,
  }));
  await browser.close();
})();
