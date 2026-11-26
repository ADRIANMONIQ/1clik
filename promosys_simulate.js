const { chromium } = require('playwright-core');
(async()=>{
  const benefit=String(process.argv[2]||'').replace(/\D/g,'');
  const operation=String(process.argv[3]||'');
  if(!benefit||!operation) throw new Error('benefício/operação ausente');
  const browser=await chromium.connectOverCDP('http://127.0.0.1:9246');
  const page=browser.contexts().flatMap(c=>c.pages()).find(p=>p.url().includes('sistemapromosys.com.br'));
  const tab=page.locator('a[id^="tituloTab_"]').filter({hasText:benefit}).last();
  const href=await tab.getAttribute('href');
  if(!href) throw new Error('aba do benefício não encontrada');
  await tab.click();
  const panelId=href.slice(1);
  const panel=page.locator(`[id="${panelId}"]`);
  const button=panel.getByRole('button',{name:new RegExp(operation,'i')}).first();
  await button.click();
  await page.waitForTimeout(1500);
  await page.waitForFunction(({panelId,operation})=>{
    const el=document.getElementById(panelId); if(!el) return false;
    const text=el.innerText||'';
    if(operation.toLowerCase().includes('novo')) return /VALOR CLIENTE|tabela\(s\)|Aprovados\s+[1-9]/i.test(text);
    return /Aprovados\s+\d+|Reprovados\s+\d+/i.test(text) && !/Selecione\s+uma operação\s+para simular todos/i.test(text);
  },{panelId,operation},{timeout:120000}).catch(()=>{});
  const text=await panel.innerText();
  console.log(JSON.stringify({benefit,operation,panel:href,text}));
  await browser.close();
})().catch(e=>{console.error(e.stack||String(e));process.exit(1)});
