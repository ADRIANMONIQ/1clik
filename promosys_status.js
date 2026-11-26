const { chromium } = require('playwright-core');
(async()=>{
  const browser=await chromium.connectOverCDP('http://127.0.0.1:9246');
  const page=browser.contexts().flatMap(c=>c.pages()).find(p=>p.url().includes('sistemapromosys.com.br'));
  if(!page) throw new Error('Promosys não encontrado');
  const out=await page.evaluate(()=>({
    url:location.href,
    queryValue:document.querySelector('input[name="valor"]')?.value||'',
    tabs:[...document.querySelectorAll('a[id^="tituloTab_"]')].map(a=>({id:a.id,text:(a.textContent||'').trim(),href:a.getAttribute('href')})),
    alerts:[...document.querySelectorAll('.alert,.toast,.swal2-html-container,.notyf__message')].filter(e=>getComputedStyle(e).display!=='none').map(e=>(e.textContent||'').trim()).filter(Boolean),
    body:(document.body.innerText||'').slice(-5000)
  }));
  console.log(JSON.stringify(out));
  await browser.close();
})().catch(e=>{console.error(e.stack||String(e));process.exit(1)});
