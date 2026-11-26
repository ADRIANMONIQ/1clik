import sqlite3, json
p='/opt/omega-crm/omega_clientes.db'
c=sqlite3.connect(p)
c.row_factory=sqlite3.Row
print('TABLES', [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('clients','promobank_consultas','promobank_contratos')")])
rows=c.execute("SELECT id,nome,cpf,convenio,raw_json FROM clients WHERE replace(replace(replace(cpf,'.',''),'-',''),'/','')=?",('18439411200',)).fetchall()
for r in rows:
 d=dict(r); raw=d.pop('raw_json') or '{}'
 try: d['raw_json']=json.loads(raw)
 except Exception: d['raw_json']=raw
 print(json.dumps(d,ensure_ascii=False,default=str))
print('CLIENT_COLUMNS',[r[1] for r in c.execute('PRAGMA table_info(clients)')])
print('CONTRACT_COLUMNS',[r[1] for r in c.execute('PRAGMA table_info(promobank_contratos)')])
print('CONSULT_COLUMNS',[r[1] for r in c.execute('PRAGMA table_info(promobank_consultas)')])
