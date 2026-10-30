// Lattice City rail-shape harness — lays an elbow + cross, shoots the corner.
// usage: node shot_shapes.js <url> <out.png> [zoom] [seed]
const { chromium } = require('playwright');

(async () => {
  const url = process.argv[2] || 'http://127.0.0.1:8791/';
  const out = process.argv[3] || 'shapes.png';
  const zoom = Number(process.argv[4] || 5.0);
  const seed = Number(process.argv[5] || 12345);

  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 }, deviceScaleFactor: 2 });
  const errors = [];
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));

  await page.addInitScript((seed) => {
    let s = seed >>> 0;
    Math.random = () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };
    try { localStorage.clear(); } catch (e) {}
  }, seed);

  await page.goto(url, { waitUntil: 'networkidle' });
  await page.waitForFunction(() => window.LC && document.querySelector('#map'), null, { timeout: 30000 });
  await page.waitForTimeout(500);
  await page.evaluate(() => {
    const m = document.querySelector('#modal');
    if (m && !m.classList.contains('hidden')) { const b = document.querySelector('#modal-close'); if (b) b.click(); }
  });
  await page.waitForTimeout(500);

  const laid = await page.evaluate((zoom) => {
    const c = document.querySelector('#map');
    LC.S.cash = 5_000_000;
    const rail = { id: 'railTrack', name: 'Rail track', cost: 80 };
    const start = LC.screenToTile(c.clientWidth / 2, c.clientHeight / 2) || { gx: 80, gy: 80 };

    const ok = (gx, gy) => !!LC.regionOf(gx, gy) && LC.paint(rail, gx, gy);
    let origin = null;
    outer:
    for (let ring = 0; ring < 50; ring++) {
      for (let dx = -ring; dx <= ring; dx++) {
        for (let dy = -ring; dy <= ring; dy++) {
          if (Math.max(Math.abs(dx), Math.abs(dy)) !== ring) continue;
          const gx = start.gx + dx, gy = start.gy + dy;
          let n = 0;
          for (let i = 0; i < 5; i++) if (LC.regionOf(gx + i, gy) && LC.regionOf(gx + i, gy + 5)) n++;
          if (n < 5) continue;
          // elbow: 5 along +gx, then 5 along +gy from the far end
          let painted = 0;
          for (let i = 0; i < 5; i++) if (ok(gx + i, gy)) painted++;
          for (let j = 1; j < 5; j++) if (ok(gx + 4, gy + j)) painted++;
          // cross: two crossing lines 6 tiles away
          const cx = gx, cy = gy + 9;
          for (let i = 0; i < 5; i++) if (ok(cx + i, cy)) painted++;
          for (let j = -2; j <= 2; j++) if (j !== 0 && ok(cx + 2, cy + j)) painted++;
          if (painted >= 15) { origin = { gx, gy, painted }; break outer; }
        }
      }
    }
    if (!origin) return { error: 'no room for rail shapes', start };
    // centre the camera on a painted tile directly (focusOn works in view space)
    const p = LC.tileToScreen(origin.gx + 2.5, origin.gy + 2.5);
    LC.cam.x += c.clientWidth / 2 - p.x;
    LC.cam.y += c.clientHeight / 2 - p.y;
    return { origin, counts: LC.counts() };
  }, zoom);

  console.log('shapes', JSON.stringify(laid));
  await page.waitForTimeout(1600);
  const canvas = await page.$('#map');
  await canvas.screenshot({ path: out });
  console.log('shot', out, 'errors', JSON.stringify(errors.slice(0, 4)));
  await browser.close();
})();
