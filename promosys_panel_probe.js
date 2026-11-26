const { chromium } = require('playwright-core');
(async()=>{
 const benefit=String(process.argv[2]||'').replace(/\D/g,'');
 const browser=await chromium.connectOverCDP('http://127.0.0.1:9246');
 const page=browser.contexts().flatMap(c=>c.pages()).find(p=>p.url().includes('sistemapromosys.com.br'));
 const tab=page.locator('a[id^="tituloTab_"]').filter({hasText:benefit}).last();
 const id=(await tab.getAttribute('href')).slice(1);
 const out=await page.locator(`[id="${id}"]`).evaluate(panel=>[...panel.querySelectorAll('button,a,select,input,[class*="aprov"],[id*="aprov"],[class*="tabela"],[id*="tabela"]')].map(e=>({tag:e.tagName,id:e.id,cls:e.className,name:e.getAttribute('name'),type:e.getAttribute('type'),text:(e.innerText||e.value||'').trim().slice(0,500),display:getComputedStyle(e).display,visible:!!(e.offsetWidth||e.offsetHeight||e.getClientRects().length)})).filter(x=>x.text||x.id||x.name));
 console.log(JSON.stringify(out)); await browser.close();
})().catch(e=>{console.error(e.stack||String(e));process.exit(1)});
