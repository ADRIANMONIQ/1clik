const { chromium } = require('playwright-core');
(async () => {
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9246');
  const page = browser.contexts().flatMap(c => c.pages()).find(p => p.url().includes('sistemapromosys.com.br'));
  const tab = page.locator('a[id^="tituloTab_"]').filter({ hasText: '2018739691' }).last();
  const href = await tab.getAttribute('href');
  if (!href) throw new Error('Aba do benefício não encontrada');
  const panelId = href.slice(1);
  const panel = page.locator(`[id="${panelId}"]`);
  await tab.click();
  console.log((await panel.innerText()).slice(-20000));
  await browser.close();
})().catch(e => { console.error(e.stack || String(e)); process.exit(1); });
