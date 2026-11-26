const { chromium } = require('playwright-core');
(async () => {
 const b=await chromium.connectOverCDP('http://127.0.0.1:9246');
 const p=b.contexts().flatMap(c=>c.pages()).find(x=>x.url().includes('sistemapromosys.com.br'));
 const id='480d64f518782b93f8a775e3c294c07d';
 const panel=p.locator(`[id="${id}"]`);
 const btn=panel.locator('button[data-ic-opfilter="REFINPORTABILIDADE"]');
 await btn.click(); await p.waitForTimeout(2500);
 console.log(JSON.stringify({active:await panel.locator('button.ic-active').first().innerText(),text:(await panel.innerText()).slice(-9000)}));
 await b.close();
})().catch(e=>{console.error(e.stack||String(e));process.exit(1)});
