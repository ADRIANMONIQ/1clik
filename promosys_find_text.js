const { chromium }=require('playwright-core');
(async()=>{
 const benefit=String(process.argv[2]||'').replace(/\D/g,''); const needle=String(process.argv[3]||'Aprovados');
 const browser=await chromium.connectOverCDP('http://127.0.0.1:9246');
 const page=browser.contexts().flatMap(c=>c.pages()).find(p=>p.url().includes('sistemapromosys.com.br'));
 const tab=page.locator('a[id^="tituloTab_"]').filter({hasText:benefit}).last(); const id=(await tab.getAttribute('href')).slice(1);
 const out=await page.locator(`[id="${id}"]`).evaluate((panel,needle)=>[...panel.querySelectorAll('*')].filter(e=>!['SCRIPT','STYLE'].includes(e.tagName)&&e.children.length===0&&(e.textContent||'').trim().toLowerCase().includes(needle.toLowerCase())).slice(0,30).map(e=>{let p=e;for(let i=0;i<3&&p.parentElement;i++)p=p.parentElement;return {leaf:{tag:e.tagName,id:e.id,cls:e.className,text:(e.textContent||'').trim()},ancestor:(p.outerHTML||'').slice(0,5000)}}),needle);
 console.log(JSON.stringify(out)); await browser.close();
})().catch(e=>{console.error(e.stack||String(e));process.exit(1)});
