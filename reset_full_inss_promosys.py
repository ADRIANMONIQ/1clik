import base64, subprocess, sys
SSH=r'C:\Windows\System32\OpenSSH\ssh.exe'; KEY=r'C:\Users\Vaio\.ssh\omega_vps_ed25519'; HOST='root@187.127.250.163'
CODE=r'''
import sqlite3,json,os,shutil,datetime
p='/opt/omega-crm/omega_clientes.db'; bd='/opt/omega-crm/backups'; os.makedirs(bd,exist_ok=True)
stamp=datetime.datetime.now().strftime('%Y%m%d_%H%M%S'); backup=f'{bd}/omega_clientes.before_full_inss_promosys_reset_{stamp}.db'
src=sqlite3.connect(p,timeout=120); dst=sqlite3.connect(backup); src.backup(dst); dst.close()
if sqlite3.connect(backup).execute('PRAGMA quick_check').fetchone()[0]!='ok': raise SystemExit('BACKUP_INVALID')
ZERO={'margem_disponivel','margem_consignavel','margem_media','media_margem','margem_cartao','margem_cartao_credito','margem_beneficio','margem_cartao_beneficio_saque','valor_simulado','valor_simulado_novo','valor_simulado_novo_contrato','novo_contrato_media','novo_contrato_count','refinanciamento_valor_por_contrato','refin_portabilidade_media_por_contrato','refin_disp','porta_disp'}
CLEAR={'contratos_vigentes','contratos_medias','simulacoes_novo_contrato','simulacao_novo_contrato','ofertas_novo_contrato','novo_contrato_ofertas','simulacoes_refinanciamento','ofertas_refinanciamento','simulacoes_refin_portabilidade','ofertas_refin_portabilidade'}
def scrub(obj):
 changed=False
 if isinstance(obj,dict):
  for k in list(obj):
   key=str(k).strip().lower()
   if key in ZERO:
    value=0 if key.endswith('_count') or key.endswith('_media') else 'R$ 0,00'
    if obj[k]!=value: obj[k]=value; changed=True
   elif key in CLEAR:
    if obj[k]!=[]: obj[k]=[]; changed=True
   else: changed=scrub(obj[k]) or changed
 elif isinstance(obj,list):
  for item in obj: changed=scrub(item) or changed
 return changed
src.row_factory=sqlite3.Row; src.execute('BEGIN IMMEDIATE')
ids=[r[0] for r in src.execute("SELECT id FROM clients WHERE UPPER(TRIM(COALESCE(convenio,'')))='INSS'")]
client_changes=0
for row in src.execute("SELECT id,raw_json FROM clients WHERE UPPER(TRIM(COALESCE(convenio,'')))='INSS'").fetchall():
 raw=row['raw_json'] or '{}'
 try:data=json.loads(raw)
 except Exception:data={}
 changed=scrub(data)
 new=json.dumps(data,ensure_ascii=False,separators=(',',':')) if changed else raw
 src.execute("UPDATE clients SET ultima_margem_disponivel='R$ 0,00',ultima_margem_cartao='R$ 0,00',ultima_margem_beneficio='R$ 0,00',store_margin=0.0,ultimo_status_margem='AGUARDANDO NOVA CONSULTA PROMOSYS',raw_json=?,updated_at=? WHERE id=?",(new,datetime.datetime.now().isoformat(timespec='seconds'),row['id']))
 client_changes+=1
consult_changes=0
for row in src.execute("SELECT q.id,q.dados_json FROM promobank_consultas q JOIN clients c ON c.id=q.client_id WHERE UPPER(TRIM(COALESCE(c.convenio,'')))='INSS'").fetchall():
 try:data=json.loads(row['dados_json'] or '{}')
 except Exception:data={}
 if scrub(data):
  src.execute('UPDATE promobank_consultas SET dados_json=? WHERE id=?',(json.dumps(data,ensure_ascii=False,separators=(',',':')),row['id']));consult_changes+=1
deleted_contracts=src.execute("DELETE FROM promobank_contratos WHERE consulta_id IN (SELECT q.id FROM promobank_consultas q JOIN clients c ON c.id=q.client_id WHERE UPPER(TRIM(COALESCE(c.convenio,'')))='INSS')").rowcount
src.commit()
check=src.execute('PRAGMA quick_check').fetchone()[0]
nonzero=src.execute("SELECT COUNT(*) FROM clients WHERE UPPER(TRIM(COALESCE(convenio,'')))='INSS' AND (COALESCE(store_margin,0)<>0 OR COALESCE(TRIM(ultima_margem_disponivel),'')<>'R$ 0,00' OR COALESCE(TRIM(ultima_margem_cartao),'')<>'R$ 0,00' OR COALESCE(TRIM(ultima_margem_beneficio),'')<>'R$ 0,00')").fetchone()[0]
contracts=src.execute("SELECT COUNT(*) FROM promobank_contratos pc JOIN promobank_consultas q ON q.id=pc.consulta_id JOIN clients c ON c.id=q.client_id WHERE UPPER(TRIM(COALESCE(c.convenio,'')))='INSS'").fetchone()[0]
bad_json=0
for row in src.execute("SELECT q.dados_json FROM promobank_consultas q JOIN clients c ON c.id=q.client_id WHERE UPPER(TRIM(COALESCE(c.convenio,'')))='INSS'"):
 try:d=json.loads(row[0] or '{}')
 except Exception:continue
 if d.get('contratos_vigentes') or d.get('contratos_medias') or str(d.get('margem_disponivel','')).strip() not in ('','0','0,00','R$ 0,00'): bad_json+=1
src.close()
if check!='ok' or nonzero or contracts or bad_json: raise SystemExit('READBACK_FAILED '+json.dumps({'quick':check,'nonzero':nonzero,'contracts':contracts,'bad_json':bad_json}))
print(json.dumps({'backup':backup,'clients_reset':client_changes,'consultations_scrubbed':consult_changes,'contracts_deleted':deleted_contracts,'readback':{'quick_check':check,'nonzero_margins':nonzero,'contract_rows':contracts,'bad_json':bad_json}}))
'''
p=base64.b64encode(CODE.encode()).decode(); remote=f'''python3 -c "import base64;exec(base64.b64decode('{p}'))"'''
r=subprocess.run([SSH,'-i',KEY,'-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','ConnectTimeout=20',HOST,remote],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=600)
print(r.stdout); print(r.stderr,file=sys.stderr); raise SystemExit(r.returncode)
