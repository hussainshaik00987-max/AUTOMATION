import fs from 'node:fs';
import path from 'node:path';

const dist = path.resolve('dist');
if (fs.existsSync(dist)) {
  fs.rmSync(dist, { recursive: true, force: true });
}
fs.mkdirSync(dist, { recursive: true });

function copyRecursive(src, dest) {
  const stat = fs.statSync(src);
  if (stat.isDirectory()) {
    fs.mkdirSync(dest, { recursive: true });
    for (const file of fs.readdirSync(src)) {
      copyRecursive(path.join(src, file), path.join(dest, file));
    }
  } else {
    fs.copyFileSync(src, dest);
  }
}

const items = ['index.html', 'catalogue.html', 'robots.txt', 'sitemap.xml', 'assets', 'scenes', 'sites'];
for (const item of items) {
  if (fs.existsSync(item)) {
    copyRecursive(item, path.join(dist, item));
  }
}
console.log('Static site built into dist successfully!');
