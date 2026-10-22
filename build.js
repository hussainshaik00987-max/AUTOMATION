import fs from 'node:fs';
import path from 'node:path';

const targetDirs = ['dist', 'build', 'out', 'public'];

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

for (const dirName of targetDirs) {
  const dirPath = path.resolve(dirName);
  if (!fs.existsSync(dirPath)) {
    fs.mkdirSync(dirPath, { recursive: true });
  }
  for (const item of items) {
    if (fs.existsSync(item)) {
      copyRecursive(item, path.join(dirPath, item));
    }
  }
}
console.log('All static directories (dist, build, out, public) synced successfully!');
