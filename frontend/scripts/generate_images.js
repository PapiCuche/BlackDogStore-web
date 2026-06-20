const fs = require('fs').promises;
const path = require('path');

async function generate() {
  const sharp = require('sharp');
  const dir = path.join(__dirname, '..', 'public', 'assets', 'branding');
  const files = await fs.readdir(dir);
  const svgs = files.filter(f => /^hero-.*\.svg$/.test(f) && !/thumb|@2x/.test(f));

  for (const svg of svgs) {
    const base = svg.replace(/\.svg$/, '');
    const svgPath = path.join(dir, svg);
    const svgBuffer = await fs.readFile(svgPath);

    // generate png/webp at 1600x900 (default), 3200x1800 (@2x) and thumb 600x338
    const sizes = [
      { suffix: '', width: 1600, height: 900 },
      { suffix: '@2x', width: 3200, height: 1800 },
      { suffix: '-thumb', width: 600, height: 338 },
    ];

    for (const s of sizes) {
      const outPng = path.join(dir, `${base}${s.suffix}.png`);
      const outWebp = path.join(dir, `${base}${s.suffix}.webp`);

      await sharp(svgBuffer)
        .resize(s.width, s.height, { fit: 'cover' })
        .png({ quality: 90 })
        .toFile(outPng);

      await sharp(svgBuffer)
        .resize(s.width, s.height, { fit: 'cover' })
        .webp({ quality: 85 })
        .toFile(outWebp);

      console.log(`Wrote ${outPng} and ${outWebp}`);
    }
  }
}

generate().catch(err => {
  console.error(err);
  process.exit(1);
});
