const { chromium } = require('playwright-core');
(async () => {
 const b=await chromium.connectOverCDP('http://127.0.0.1:9246');
 const p=b.contexts().flatMap(c=>c.pages()).find(x=>x.url().includes('sistemapromosys.com.br'));
 const id='480d64f518782b93f8a775e3c294c07d';
 const data=await p.locator(`[id="${id}"] button`).evaluateAll(es=>es.map(e=>({text:e.innerText,id:e.id,cls:e.className,html:e.outerHTML.slice(0,300)})));
 console.log(JSON.stringify(data));
 await b.close();
})().catch(e=>{console.error(e.stack||String(e));process.exit(1)});
