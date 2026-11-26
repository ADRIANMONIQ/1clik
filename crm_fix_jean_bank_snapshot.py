import sqlite3,json,os,datetime
db='/opt/omega-crm/omega_clientes.db'; os.makedirs('/opt/omega-crm/backups',exist_ok=True)
stamp=datetime.datetime.now().strftime('%Y%m%d_%H%M%S'); backup=f'/opt/omega-crm/backups/omega_clientes.before_jean_bank_snapshot_{stamp}.db'
c=sqlite3.connect(db,timeout=60); d=sqlite3.connect(backup); c.backup(d); d.close(); c.row_factory=sqlite3.Row
if sqlite3.connect(backup).execute('PRAGMA quick_check').fetchone()[0]!='ok': raise SystemExit('BACKUP_INVALID')
r=c.execute('SELECT dados_json FROM promobank_consultas WHERE id=22779 AND client_id=2405 AND cpf=?',('18439411200',)).fetchone()
if not r: raise SystemExit('QUERY_GUARD_FAILED')
j=json.loads(r['dados_json'] or '{}')
if str(j.get('beneficio'))!='2018739691': raise SystemExit('BENEFIT_GUARD_FAILED')
j.update({'banco_recebimento':'CREFISA','codigo_banco':'0069','meio_pagamento':'CARTÃO MAGNÉTICO','agencia':'1','conta':'','ddb':'27/07/2021'})
c.execute('BEGIN IMMEDIATE'); c.execute('UPDATE promobank_consultas SET dados_json=? WHERE id=22779',(json.dumps(j,ensure_ascii=False,separators=(',',':')),)); c.commit()
out=json.loads(c.execute('SELECT dados_json FROM promobank_consultas WHERE id=22779').fetchone()[0]); quick=c.execute('PRAGMA quick_check').fetchone()[0]
if quick!='ok' or out.get('banco_recebimento')!='CREFISA': raise SystemExit('READBACK_FAILED')
print(json.dumps({'backup':backup,'consultation_id':22779,'banco_recebimento':out['banco_recebimento'],'codigo_banco':out['codigo_banco'],'meio_pagamento':out['meio_pagamento'],'agencia':out['agencia'],'conta':out['conta'],'ddb':out['ddb'],'quick_check':quick},ensure_ascii=False))
