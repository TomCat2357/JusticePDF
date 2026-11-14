// lucide のアイコンを SVG にして src/resources/icons/lucide/ へ書き出す。
// 使い方: node dev/gen_lucide_icons.js [lucide パッケージの場所]
// 既定の場所は ../../JusticePDF-ONLINE/node_modules/lucide（lucide 0.294.0）。
'use strict';
const fs = require('fs');
const path = require('path');

const pkg = path.resolve(process.argv[2] || path.resolve(__dirname, '../../JusticePDF-ONLINE/node_modules/lucide'));
const lucide = require(path.join(pkg, 'dist/cjs/lucide.js'));
const outDir = path.resolve(__dirname, '../src/resources/icons/lucide');

const names = fs.readFileSync(path.join(__dirname, 'lucide_icons.txt'), 'utf8')
  .split(/\r?\n/).map((s) => s.trim()).filter((s) => s && !s.startsWith('#'));

const kebab = (n) => n.replace(/([a-z0-9])([A-Z])/g, '$1-$2').replace(/([A-Za-z])([0-9])/g, '$1-$2').toLowerCase();
const svg = (n) => '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
  + lucide.icons[n][2].map(([t, a]) => '<' + t + ' ' + Object.entries(a).map(([k, v]) => k + '="' + v + '"').join(' ') + '/>').join('')
  + '</svg>\n';

const missing = names.filter((n) => !lucide.icons[n]);
if (missing.length) {
  console.error('lucide に無いアイコン: ' + missing.join(', '));
  process.exit(1);
}
fs.mkdirSync(outDir, { recursive: true });
for (const n of names) fs.writeFileSync(path.join(outDir, kebab(n) + '.svg'), svg(n));
fs.copyFileSync(path.join(pkg, 'LICENSE'), path.join(outDir, 'LICENSE'));
console.log(names.length + ' 個を ' + outDir + ' に書き出しました');
