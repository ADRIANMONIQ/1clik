import base64,subprocess
SSH=r'C:\Windows\System32\OpenSSH\ssh.exe'; KEY=r'C:\Users\Vaio\.ssh\omega_vps_ed25519'; HOST='root@187.127.250.163'
CODE=r'''
import sqlite3,json
c=sqlite3.connect('/opt/omega-crm/omega_clientes.db'); c.row_factory=sqlite3.Row
cpf='17536782268'
cl=c.execute("SELECT id,cpf,nome,convenio,ultima_margem_disponivel,store_margin,updated_at,raw_json FROM clients WHERE REPLACE(REPLACE(REPLACE(cpf,'.',''),'-',''),' ','')=?",(cpf,)).fetchone()
print('CLIENT',json.dumps(dict(cl) if cl else None,ensure_ascii=False))
if cl:
 rows=c.execute("SELECT id,status,created_at,finished_at,dados_json FROM promobank_consultas WHERE client_id=? ORDER BY id DESC LIMIT 3",(cl['id'],)).fetchall()
 for r in rows:
  d=dict(r)
  try:
   j=json.loads(d.pop('dados_json') or '{}'); d['keys']=sorted(j.keys()); d['margin']={k:j.get(k) for k in ('margem_disponivel','margem_consignavel','novo_contrato_media','valor_simulado_novo','valor_simulado_novo_contrato')}; d['contracts']=j.get('contratos_vigentes'); d['simulation_keys']={k:j.get(k) for k in j if any(x in k.lower() for x in ('simul','oferta','novo_contrato'))}
  except Exception as e:d['json_error']=str(e)
  print('CONSULTA',json.dumps(d,ensure_ascii=False))
 print('CONTRACT_ROWS',c.execute('SELECT COUNT(*) FROM promobank_contratos pc JOIN promobank_consultas q ON q.id=pc.consulta_id WHERE q.client_id=?',(cl['id'],)).fetchone()[0])
print('INSS_NONZERO_MARGIN',c.execute("SELECT COUNT(*) FROM clients WHERE UPPER(TRIM(COALESCE(convenio,'')))='INSS' AND COALESCE(store_margin,0)<>0").fetchone()[0])
print('INSS_CONTRACT_ROWS',c.execute("SELECT COUNT(*) FROM promobank_contratos pc JOIN promobank_consultas q ON q.id=pc.consulta_id JOIN clients x ON x.id=q.client_id WHERE UPPER(TRIM(COALESCE(x.convenio,'')))='INSS'").fetchone()[0])
print('QUICK',c.execute('PRAGMA quick_check').fetchone()[0])
'''
p=base64.b64encode(CODE.encode()).decode(); remote=f'''python3 -c "import base64;exec(base64.b64decode('{p}'))"'''
r=subprocess.run([SSH,'-i',KEY,'-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','ConnectTimeout=20',HOST,remote],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=120)
print(r.stdout); print(r.stderr); raise SystemExit(r.returncode)
