const { chromium } = require('playwright-core');

(async () => {
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9246');
  const contexts = browser.contexts();
  const pages = contexts.flatMap((context) => context.pages());
  const page = pages.find((item) => item.url().includes('sistemapromosys.com.br'));
  if (!page) throw new Error('Página Promosys não encontrada');
  await page.waitForLoadState('domcontentloaded');
  if (process.argv.includes('--open-inss')) {
    await page.locator('#butInss').click();
    await page.waitForTimeout(1200);
  }
  const data = await page.evaluate(() => ({
    title: document.title,
    url: location.href,
    text: document.body.innerText.slice(0, 12000),
    inputs: [...document.querySelectorAll('input,select,button,a')].map((element) => ({
      tag: element.tagName,
      id: element.id,
      name: element.getAttribute('name'),
      type: element.getAttribute('type'),
      placeholder: element.getAttribute('placeholder'),
      text: (element.innerText || element.value || '').trim().slice(0, 160),
      href: element.getAttribute('href'),
    })).filter((item) => item.id || item.name || item.placeholder || item.text),
  }));
  console.log(JSON.stringify(data));
  await browser.close();
})().catch((error) => {
  console.error(error.stack || String(error));
  process.exit(1);
});
