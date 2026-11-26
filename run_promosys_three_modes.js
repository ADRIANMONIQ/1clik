const { chromium } = require('playwright-core');
(async () => {
  const b = await chromium.connectOverCDP('http://127.0.0.1:9246');
  const p = b.contexts().flatMap(c => c.pages()).find(x => x.url().includes('sistemapromosys.com.br'));
  const id = '480d64f518782b93f8a775e3c294c07d';
  const panel = p.locator(`[id="${id}"]`);
  const outputs = [];
  for (const [label, selector] of [
    ['NOVO CONTRATO', 'button[data-ic-opfilter="NOVO"]'],
    ['REFIN PORTABILIDADE', 'button[data-ic-opfilter="REFINPORTABILIDADE"]'],
    ['REFINANCIAMENTO', 'button[data-ic-opfilter="REFINANCIAMENTO"]'],
  ]) {
    await panel.locator(selector).click();
    await p.waitForTimeout(2500);
    outputs.push({ label, active: await panel.locator('button.ic-active').allInnerTexts(), text: (await panel.innerText()).slice(-14000) });
  }
  console.log(JSON.stringify(outputs));
  await b.close();
})().catch(e => { console.error(e.stack || String(e)); process.exit(1); });
