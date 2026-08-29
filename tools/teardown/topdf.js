const puppeteer = require('D:/Code/Personal Projects/cold_calling_india/whatsapp/node_modules/puppeteer-core');
const path = require('path');

const HTML = path.resolve(process.argv[2]);
const OUT = path.resolve(process.argv[3]);
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';

const foot = `
<div style="width:100%;font-family:'IBM Plex Mono',Consolas,monospace;font-size:7px;color:#767c92;
            padding:0 13mm;display:flex;justify-content:space-between;">
  <span>campaign live3 &middot; 50 calls &middot; Mon 24 Aug 2026</span>
  <span><span class="pageNumber"></span> / <span class="totalPages"></span></span>
</div>`;

(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: true,
    args: ['--no-sandbox', '--font-render-hinting=none'],
  });
  const page = await browser.newPage();
  await page.goto('file:///' + HTML.replace(/\\/g, '/'), { waitUntil: 'networkidle0', timeout: 120000 });
  await page.evaluate(() => document.fonts.ready);
  await page.pdf({
    path: OUT,
    format: 'A4',
    printBackground: true,
    displayHeaderFooter: true,
    headerTemplate: '<div></div>',
    footerTemplate: foot,
    margin: { top: '13mm', bottom: '14mm', left: '13mm', right: '13mm' },
  });
  await browser.close();
  console.log('wrote', OUT);
})().catch((e) => { console.error(e); process.exit(1); });
