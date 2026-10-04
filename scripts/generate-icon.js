#!/usr/bin/env node
// Generate the Windows app icon (.ico) and window PNGs from the brand SVG.
//
//   npm run icon
//
// Produces:
//   assets/branding/qamate-mark.ico       (electron-builder win.icon / installer)
//   assets/branding/qamate-mark-32.png    (in-app window/taskbar icon; main.js)
//   assets/branding/qamate-mark-256.png   (handy for docs/stores)
//
// Requires the devDependencies `sharp` and `png-to-ico`. If they are missing,
// run `npm install` first. If `sharp` cannot be installed in your environment,
// see docs/INSTALLER.md for a manual .ico route.

const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const SRC_SVG = path.join(ROOT, 'assets', 'branding', 'qamate-mark.svg');
const OUT_DIR = path.join(ROOT, 'assets', 'branding');
const ICO_SIZES = [16, 24, 32, 48, 64, 128, 256];

async function main() {
  if (!fs.existsSync(SRC_SVG)) {
    console.error(`Source SVG not found: ${SRC_SVG}`);
    process.exit(1);
  }

  let sharp, pngToIco;
  try {
    sharp = require('sharp');
    pngToIco = require('png-to-ico');
  } catch (e) {
    console.error('Missing dependencies. Run `npm install` (needs `sharp` and `png-to-ico`).');
    console.error(String(e && e.message));
    process.exit(1);
  }

  const svg = fs.readFileSync(SRC_SVG);

  // Rasterize each size on a transparent canvas.
  const pngBuffers = {};
  for (const size of ICO_SIZES) {
    pngBuffers[size] = await sharp(svg, { density: 384 })
      .resize(size, size, { fit: 'contain', background: { r: 0, g: 0, b: 0, alpha: 0 } })
      .png()
      .toBuffer();
  }

  const icoPath = path.join(OUT_DIR, 'qamate-mark.ico');
  const ico = await pngToIco(ICO_SIZES.map((s) => pngBuffers[s]));
  fs.writeFileSync(icoPath, ico);
  console.log(`✓ ${path.relative(ROOT, icoPath)} (${ICO_SIZES.join(', ')} px)`);

  fs.writeFileSync(path.join(OUT_DIR, 'qamate-mark-32.png'), pngBuffers[32]);
  console.log(`✓ ${path.relative(ROOT, path.join(OUT_DIR, 'qamate-mark-32.png'))}`);

  fs.writeFileSync(path.join(OUT_DIR, 'qamate-mark-256.png'), pngBuffers[256]);
  console.log(`✓ ${path.relative(ROOT, path.join(OUT_DIR, 'qamate-mark-256.png'))}`);

  // macOS: electron-builder converts a >=512px PNG into the .icns.
  const png1024 = await sharp(svg, { density: 384 })
    .resize(1024, 1024, { fit: 'contain', background: { r: 0, g: 0, b: 0, alpha: 0 } })
    .png()
    .toBuffer();
  fs.writeFileSync(path.join(OUT_DIR, 'qamate-mark-1024.png'), png1024);
  console.log(`✓ ${path.relative(ROOT, path.join(OUT_DIR, 'qamate-mark-1024.png'))}`);
}

main().catch((e) => { console.error(e); process.exit(1); });
