const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1200 } });
  await page.goto('http://14.103.220.208:8888/login');
  await page.fill('input[name="username"]', 'admin');
  await page.fill('input[name="password"]', '709709@');
  await Promise.all([page.waitForLoadState('networkidle'), page.click('button[type="submit"]')]);
  await page.goto('http://14.103.220.208:8888/subtitle-removal/58c29439-3c66-4fe8-812d-254670117837', { waitUntil: 'networkidle' });
  await page.screenshot({ path: 'G:/Code/AutoVideoSrt/.tmp/detail-fixed.png', fullPage: true });
  const compareTitle = await page.$eval('.sr-compare-panel-title', el => el.textContent).catch(() => 'NOT_FOUND');
  const cardH2 = await page.$eval('.sr-compare-card h2', el => el.textContent).catch(() => 'NOT_FOUND');
  console.log('COMPARE_TITLE:', compareTitle);
  console.log('CARD_H2:', cardH2);
  await browser.close();
})();
