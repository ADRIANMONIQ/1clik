const { chromium } = require('playwright-core');
(async () => {
 const benefit=String(process.argv[2]||'').replace(/\D/g,'');
 const b=await chromium.connectOverCDP('http://127.0.0.1:9246');
 const p=b.contexts().flatMap(c=>c.pages()).find(x=>x.url().includes('sistemapromosys.com.br'));
 const t=p.locator('a[id^="tituloTab_"]').filter({hasText:benefit}).last();
 const href=await t.getAttribute('href');
 if(!href) throw new Error('BENEFIT_TAB_NOT_FOUND');
 const panel=p.locator(`[id="${href.slice(1)}"]`);
 console.log(JSON.stringify({benefit,text:(await panel.innerText()).slice(0,14000)}));
 await b.close();
})().catch(e=>{console.error(e.stack||String(e));process.exit(1)});
