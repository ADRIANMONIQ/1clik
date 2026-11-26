import sqlite3,json,re
c=sqlite3.connect('/opt/omega-crm/omega_clientes.db'); c.row_factory=sqlite3.Row
rows=c.execute("""SELECT c.id,c.cpf,c.nome,c.beneficio,c.matricula,c.especie,c.data_nascimento
FROM clients c WHERE c.active=1 AND UPPER(TRIM(COALESCE(c.convenio,'')))='INSS'
AND UPPER(TRIM(COALESCE(c.benefit_status,'ATIVO'))) NOT IN ('CESSADO','ÓBITO','OBITO')
AND LENGTH(REPLACE(REPLACE(REPLACE(COALESCE(c.cpf,''),'.',''),'-',''),' ',''))=11
AND COALESCE(NULLIF(TRIM(c.beneficio),''),NULLIF(TRIM(c.matricula),'')) IS NOT NULL
AND NOT EXISTS (SELECT 1 FROM promobank_consultas q WHERE q.client_id=c.id AND SUBSTR(COALESCE(q.finished_at,q.created_at,''),1,10)=DATE('now','localtime'))
ORDER BY COALESCE(NULLIF(c.last_service_at,''),NULLIF(c.updated_at,''),c.id) DESC,c.id DESC LIMIT 500""").fetchall()
out=[]; seen=set()
for r in rows:
 cpf=''.join(x for x in (r['cpf'] or '') if x.isdigit()).zfill(11); ben=str(r['beneficio'] or r['matricula'] or '').strip(); key=(cpf,ben)
 match=re.search(r'(?<!\d)(21|31|32|36|41|42|46|87|91|92)(?!\d)',str(r['especie'] or ''))
 if not match: continue
 if key in seen: continue
 seen.add(key); out.append({'client_id':r['id'],'cpf':cpf,'nome':r['nome'],'beneficio':ben,'especie':r['especie'] or '', 'nascimento':r['data_nascimento'] or ''})
 if len(out)>=50: break
print(json.dumps({'count':len(out),'clients':out},ensure_ascii=False))
