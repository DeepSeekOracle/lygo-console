// Lattice City screenshot harness — deterministic map, road + rail strips, canvas shot.
// usage: node shot.js <url> <out.png> [zoom] [seed]
const { chromium } = require('playwright');

(async () => {
  const url = process.argv[2] || 'http://127.0.0.1:8791/';
  const out = process.argv[3] || 'shot.png';
  const zoom = Number(process.argv[4] || 3.2);
  const seed = Number(process.argv[5] || 12345);

  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 }, deviceScaleFactor: 2 });
  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));

  // deterministic terrain: same map for every run, so before/after is a fair comparison
  await page.addInitScript((seed) => {
    let s = seed >>> 0;
    Math.random = () => {
      s = (s * 1664525 + 1013904223) >>> 0;
      return s / 4294967296;
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
  await page.waitForTimeout(600);

  const laid = await page.evaluate((zoom) => {
    const c = document.querySelector('#map');
    LC.S.cash = 5_000_000;
    const road = { id: 'road', name: 'Road', cost: 12 };
    const rail = { id: 'railTrack', name: 'Rail track', cost: 80 };
    const N = 7;

    const start = LC.screenToTile(c.clientWidth / 2, c.clientHeight / 2) || { gx: 80, gy: 80 };
    const tryStrip = (tool, gx, gy) => {
      let ok = 0;
      for (let i = 0; i < N; i++) if (LC.paint(tool, gx + i, gy)) ok++;
      return ok === N;
    };

    let found = null;
    outer:
    for (let ring = 0; ring < 60 && !found; ring++) {
      for (let dx = -ring; dx <= ring; dx++) {
        for (let dy = -ring; dy <= ring; dy++) {
          if (Math.max(Math.abs(dx), Math.abs(dy)) !== ring) continue;
          const gx = start.gx + dx, gy = start.gy + dy;
          for (const gap of [3, -3, 4, -4]) {
            if (
              tryStrip(road, gx, gy) &&
              tryStrip(rail, gx, gy + gap)
            ) { found = { gx, gy, gap }; break outer; }
          }
        }
      }
    }
    if (!found) return { error: 'no buildable strip pair found', start };
    // zoom in, then centre on the rail strip directly (focusOn is view space)
    LC.cam.zoom = zoom;
    const p = LC.tileToScreen(found.gx + 3, found.gy + found.gap);
    LC.cam.x += c.clientWidth / 2 - p.x;
    LC.cam.y += c.clientHeight / 2 - p.y;
    return { origin: found, counts: LC.counts() };
  }, zoom);

  console.log('laid', JSON.stringify(laid));
  await page.waitForTimeout(1800);
  const canvas = await page.$('#map');
  await canvas.screenshot({ path: out });
  console.log('shot', out, 'errors', JSON.stringify(errors.slice(0, 5)));
  await browser.close();
})();
