const { chromium } = require('playwright-core');

(async () => {
  const benefit = String(process.argv[2] || '').replace(/\D/g, '');
  if (!benefit) throw new Error('Benefício ausente');
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9246');
  const page = browser.contexts().flatMap((context) => context.pages())
    .find((item) => item.url().includes('sistemapromosys.com.br'));
  if (!page) throw new Error('Promosys não encontrado');
  if (!await page.locator('input[name="valor"]').count()) {
    await page.locator('#butInss').click();
    await page.locator('input[name="valor"]').waitFor({ state: 'visible' });
  }
  const before = await page.locator('a[id^="tituloTab_"]').count();
  await page.locator('input[name="valor"]').fill(benefit);
  if (await page.locator('#naoTelefoneRadio').count()) {
    await page.locator('#naoTelefoneRadio').check();
  }
  const form = page.locator('input[name="valor"]').locator('xpath=ancestor::form');
  await form.getByRole('button', { name: 'Consultar', exact: true }).click();
  await page.waitForFunction(({ benefit, before }) => {
    const tabs=[...document.querySelectorAll('a[id^="tituloTab_"]')];
    return tabs.length > before && tabs.some((tab) => (tab.textContent || '').replace(/\D/g,'') === benefit);
  }, { benefit, before }, { timeout: 90000 });
  const tab = page.locator('a[id^="tituloTab_"]').filter({ hasText: benefit }).last();
  const href = await tab.getAttribute('href');
  if (!href || !href.startsWith('#')) throw new Error('Painel do benefício não identificado');
  const panel = page.locator(href);
  await panel.waitFor({ state: 'visible', timeout: 30000 });
  const text = await panel.innerText();
  console.log(JSON.stringify({ benefit, tabCountBefore: before, tabCountAfter: await page.locator('a[id^="tituloTab_"]').count(), panel: href, text }));
  await browser.close();
})().catch((error) => {
  console.error(error.stack || String(error));
  process.exit(1);
});
