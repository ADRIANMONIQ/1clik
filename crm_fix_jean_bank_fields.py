import sqlite3,json,os,datetime
db='/opt/omega-crm/omega_clientes.db'; os.makedirs('/opt/omega-crm/backups',exist_ok=True)
stamp=datetime.datetime.now().strftime('%Y%m%d_%H%M%S'); backup=f'/opt/omega-crm/backups/omega_clientes.before_jean_bank_fields_{stamp}.db'
c=sqlite3.connect(db,timeout=60); d=sqlite3.connect(backup); c.backup(d); d.close(); c.row_factory=sqlite3.Row
if sqlite3.connect(backup).execute('PRAGMA quick_check').fetchone()[0]!='ok': raise SystemExit('BACKUP_INVALID')
r=c.execute("SELECT raw_json FROM clients WHERE id=2405 AND cpf='18439411200' AND convenio='INSS'").fetchone()
if not r: raise SystemExit('CLIENT_GUARD_FAILED')
j=json.loads(r['raw_json'] or '{}'); p=j.get('promosys_beneficio_atual',{})
if p.get('beneficio')!='2018739691': raise SystemExit('BENEFIT_GUARD_FAILED')
p.update({'banco_recebimento':'CREFISA','codigo_banco':'0069','meio_pagamento':'CARTÃO MAGNÉTICO','agencia':'1','conta':'','ddb':'27/07/2021'})
j['promosys_beneficio_atual']=p
for item in j.get('promosys_beneficios',[]):
 if str(item.get('beneficio'))=='2018739691': item.update({k:p[k] for k in ('banco_recebimento','codigo_banco','meio_pagamento','agencia','conta','ddb')})
c.execute('BEGIN IMMEDIATE'); c.execute('UPDATE clients SET raw_json=?,updated_at=? WHERE id=2405',(json.dumps(j,ensure_ascii=False,separators=(',',':')),datetime.datetime.now().isoformat(timespec='seconds'))); c.commit()
out=json.loads(c.execute('SELECT raw_json FROM clients WHERE id=2405').fetchone()[0])['promosys_beneficio_atual']
quick=c.execute('PRAGMA quick_check').fetchone()[0]
if quick!='ok' or out.get('banco_recebimento')!='CREFISA' or out.get('codigo_banco')!='0069': raise SystemExit('READBACK_FAILED')
print(json.dumps({'backup':backup,'client_id':2405,'benefit':out['beneficio'],'banco_recebimento':out['banco_recebimento'],'codigo_banco':out['codigo_banco'],'meio_pagamento':out['meio_pagamento'],'agencia':out['agencia'],'conta':out['conta'],'ddb':out['ddb'],'quick_check':quick},ensure_ascii=False))
