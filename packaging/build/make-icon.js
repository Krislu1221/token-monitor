'use strict';

/**
 * 生成 Token monitor 的 icon.png / icon.icns
 * 复用 dsh-desktop 工程里的 sharp 做 SVG→PNG 栅格化；ICNS 手动组装
 * （本机 iconutil 曾不可用，手写容器最稳）。
 */
const fs = require('node:fs');
const path = require('node:path');

const SHARP = '/Users/krislu/Documents/项目讨论/deepseek/dsh-desktop/node_modules/sharp';
const sharp = require(SHARP);

const build = __dirname;
const SVG = path.join(build, 'icon.svg');
const PNG = path.join(build, 'icon.png');
const ICNS = path.join(build, 'icon.icns');

(async () => {
  await sharp(SVG, { density: 144 }).resize(1024, 1024).png().toFile(PNG);
  console.log('icon.png 生成完毕');

  const chunks = [
    ['icp4', 16],
    ['icp5', 32],
    ['ic07', 128],
    ['ic08', 256],
    ['ic09', 512],
    ['ic10', 1024],
  ];
  const master = sharp(PNG).resize(1024, 1024);
  const parts = [];
  for (const [fourcc, px] of chunks) {
    const png = await master.clone().resize(px, px).png().toBuffer();
    const len = 8 + png.length;
    const chunk = Buffer.alloc(len);
    chunk.write(fourcc, 0, 'ascii');
    chunk.writeUInt32BE(len, 4);
    png.copy(chunk, 8);
    parts.push(chunk);
  }
  const total = 8 + parts.reduce((s, c) => s + c.length, 0);
  const icns = Buffer.alloc(total);
  icns.write('icns', 0, 'ascii');
  icns.writeUInt32BE(total, 4);
  let off = 8;
  for (const chunk of parts) {
    chunk.copy(icns, off);
    off += chunk.length;
  }
  fs.writeFileSync(ICNS, icns);
  console.log(`icon.icns 生成完毕 (${total} 字节, chunks: ${chunks.map((c) => c[0]).join(' ')})`);
})().catch((err) => {
  console.error('图标生成失败:', err);
  process.exit(1);
});
