const { chromium } = require('playwright-core');
(async () => {
 const b=await chromium.connectOverCDP('http://127.0.0.1:9246');
 const p=b.contexts().flatMap(c=>c.pages()).find(x=>x.url().includes('sistemapromosys.com.br'));
 const id='480d64f518782b93f8a775e3c294c07d';
 const panel=p.locator(`[id="${id}"]`);
 await panel.locator('button[data-ic-opfilter="REFINPORTABILIDADE"]').click();
 await p.waitForTimeout(1200);
 const buttons=panel.getByRole('button',{name:/Aprovados/});
 const n=await buttons.count(); const results=[];
 for(let i=0;i<n;i++){
   const btn=buttons.nth(i);
   await btn.click(); await p.waitForTimeout(500);
   results.push((await panel.innerText()).slice(-5000));
 }
 console.log(JSON.stringify({approvedButtons:n,results}));
 await b.close();
})().catch(e=>{console.error(e.stack||String(e));process.exit(1)});
