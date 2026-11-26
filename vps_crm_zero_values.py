import json
import base64
import subprocess
import sys

SSH = r"C:\Windows\System32\OpenSSH\ssh.exe"
KEY = r"C:\Users\Vaio\.ssh\omega_vps_ed25519"
HOST = "root@187.127.250.163"

INSPECT = r'''
import sqlite3, os, json
p='/opt/omega-crm/omega_clientes.db'
c=sqlite3.connect(p)
print('DB', os.path.realpath(p), os.path.getsize(p))
print('quick_check', c.execute('PRAGMA quick_check').fetchone()[0])
for t in ('clients','promobank_consultas','promobank_contratos'):
    print(t, c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0])
zero=("", "0", "0,00", "R$ 0,00")
for label,sql in (
 ('clients_nonzero_margin', "SELECT COUNT(*) FROM clients WHERE COALESCE(TRIM(ultima_margem_disponivel),'') NOT IN (?,?,?,?)"),
 ('contracts_nonzero_refin', "SELECT COUNT(*) FROM promobank_contratos WHERE COALESCE(TRIM(refin_disp),'') NOT IN (?,?,?,?)"),
 ('contracts_nonzero_porta', "SELECT COUNT(*) FROM promobank_contratos WHERE COALESCE(TRIM(porta_disp),'') NOT IN (?,?,?,?)")):
    print(label,c.execute(sql,zero).fetchone()[0])
print('pc_cols', json.dumps([r[1] for r in c.execute('PRAGMA table_info(promobank_consultas)')]))
print('client_value_cols', json.dumps([r[1] for r in c.execute('PRAGMA table_info(clients)') if 'margem' in r[1] or 'consulta' in r[1] or 'store_margin' in r[1]]))
for table,col in (('promobank_consultas','dados_json'),('clients','raw_json')):
    keys=set()
    for (raw,) in c.execute('SELECT '+col+' FROM '+table+' WHERE '+col+' IS NOT NULL LIMIT 200'):
        try:
            d=json.loads(raw or '{}')
            if isinstance(d,dict): keys.update(d.keys())
        except Exception: pass
    print(table+'_keys',json.dumps(sorted(k for k in keys if any(x in k.lower() for x in ('margem','simulad','contrat'))),ensure_ascii=False))
'''

APPLY = r'''
import sqlite3, os, json, shutil, datetime, hashlib
p='/opt/omega-crm/omega_clientes.db'
backup_dir='/opt/omega-crm/backups'
os.makedirs(backup_dir,exist_ok=True)
stamp=datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
b=os.path.join(backup_dir,'omega_clientes.before_zero_promosys_values_'+stamp+'.db')
size=os.path.getsize(p)
free=shutil.disk_usage(backup_dir).free
if free < size + 200*1024*1024:
    raise SystemExit('INSUFFICIENT_DISK_SPACE size=%d free=%d'%(size,free))
src=sqlite3.connect(p,timeout=60)
dst=sqlite3.connect(b)
src.backup(dst)
dst.close()
check=sqlite3.connect(b).execute('PRAGMA quick_check').fetchone()[0]
if check!='ok':
    raise SystemExit('BACKUP_QUICK_CHECK_FAILED '+str(check))

ZERO_KEYS={
 'margem_disponivel','margem_consignavel','margem_media','media_margem',
 'valor_simulado','valor_simulado_novo','valor_simulado_novo_contrato','novo_contrato_media',
 'refin_disp','porta_disp'
}
def zero_payload(obj):
    changed=False
    if isinstance(obj,dict):
        for k in list(obj):
            if str(k).strip().lower() in ZERO_KEYS:
                if obj[k] != 'R$ 0,00': obj[k]='R$ 0,00'; changed=True
            else:
                changed = zero_payload(obj[k]) or changed
    elif isinstance(obj,list):
        for item in obj: changed = zero_payload(item) or changed
    return changed

src.execute('BEGIN IMMEDIATE')
clients_changed=src.execute("UPDATE clients SET ultima_margem_disponivel='R$ 0,00', store_margin=0.0 WHERE COALESCE(TRIM(ultima_margem_disponivel),'') NOT IN ('','0','0,00','R$ 0,00') OR COALESCE(store_margin,0)<>0").rowcount
contracts_changed=src.execute("UPDATE promobank_contratos SET refin_disp='R$ 0,00', porta_disp='R$ 0,00' WHERE COALESCE(TRIM(refin_disp),'') NOT IN ('','0','0,00','R$ 0,00') OR COALESCE(TRIM(porta_disp),'') NOT IN ('','0','0,00','R$ 0,00')").rowcount
json_changed=0
for rowid,raw in src.execute("SELECT id,dados_json FROM promobank_consultas WHERE COALESCE(dados_json,'')<>''").fetchall():
    try: data=json.loads(raw)
    except Exception: continue
    if zero_payload(data):
        src.execute('UPDATE promobank_consultas SET dados_json=? WHERE id=?',(json.dumps(data,ensure_ascii=False,separators=(',',':')),rowid))
        json_changed+=1
src.commit()

zero=("", "0", "0,00", "R$ 0,00")
readback={
 'clients_nonzero_margin':src.execute("SELECT COUNT(*) FROM clients WHERE COALESCE(TRIM(ultima_margem_disponivel),'') NOT IN (?,?,?,?)",zero).fetchone()[0],
 'clients_nonzero_store_margin':src.execute("SELECT COUNT(*) FROM clients WHERE COALESCE(store_margin,0)<>0").fetchone()[0],
 'contracts_nonzero_refin':src.execute("SELECT COUNT(*) FROM promobank_contratos WHERE COALESCE(TRIM(refin_disp),'') NOT IN (?,?,?,?)",zero).fetchone()[0],
 'contracts_nonzero_porta':src.execute("SELECT COUNT(*) FROM promobank_contratos WHERE COALESCE(TRIM(porta_disp),'') NOT IN (?,?,?,?)",zero).fetchone()[0],
 'quick_check':src.execute('PRAGMA quick_check').fetchone()[0]
}
src.close()
if any(readback[k] for k in ('clients_nonzero_margin','clients_nonzero_store_margin','contracts_nonzero_refin','contracts_nonzero_porta')) or readback['quick_check']!='ok':
    raise SystemExit('READBACK_FAILED '+json.dumps(readback))
print(json.dumps({'backup':b,'backup_size':os.path.getsize(b),'backup_quick_check':check,'clients_changed':clients_changed,'contracts_changed':contracts_changed,'consultation_json_changed':json_changed,'readback':readback},ensure_ascii=False))
'''

VERIFY = r'''
import sqlite3, urllib.request, subprocess, json
p='/opt/omega-crm/omega_clientes.db'
c=sqlite3.connect(p)
zero=("", "0", "0,00", "R$ 0,00")
state={
 'service':subprocess.run(['systemctl','is-active','omega-crm.service'],capture_output=True,text=True).stdout.strip(),
 'quick_check':c.execute('PRAGMA quick_check').fetchone()[0],
 'clients_nonzero_margin':c.execute("SELECT COUNT(*) FROM clients WHERE COALESCE(TRIM(ultima_margem_disponivel),'') NOT IN (?,?,?,?)",zero).fetchone()[0],
 'contracts_nonzero_refin':c.execute("SELECT COUNT(*) FROM promobank_contratos WHERE COALESCE(TRIM(refin_disp),'') NOT IN (?,?,?,?)",zero).fetchone()[0],
 'contracts_nonzero_porta':c.execute("SELECT COUNT(*) FROM promobank_contratos WHERE COALESCE(TRIM(porta_disp),'') NOT IN (?,?,?,?)",zero).fetchone()[0]
}
r=urllib.request.urlopen('http://127.0.0.1:8080/login',timeout=15)
body=r.read().lower()
state.update(http=r.status,login_title=(b'<title' in body))
print(json.dumps(state))
'''

SCOPED_INSS = r'''
import sqlite3, os, json, shutil, datetime
p='/opt/omega-crm/omega_clientes.db'
source='/opt/omega-crm/backups/omega_clientes.before_zero_promosys_values_20261125_173734.db'
stamp=datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
mistake='/opt/omega-crm/backups/omega_clientes.after_unscoped_zero_'+stamp+'.db'
if not os.path.exists(source): raise SystemExit('RESTORE_SOURCE_MISSING')
shutil.copy2(p,mistake)
shutil.copy2(source,p)
c=sqlite3.connect(p,timeout=60)
if c.execute('PRAGMA quick_check').fetchone()[0]!='ok': raise SystemExit('RESTORED_DB_INVALID')
ZERO_KEYS={'margem_disponivel','margem_consignavel','margem_media','media_margem','valor_simulado','valor_simulado_novo','valor_simulado_novo_contrato','novo_contrato_media','refin_disp','porta_disp'}
def zero_payload(obj):
    changed=False
    if isinstance(obj,dict):
        for k in list(obj):
            if str(k).strip().lower() in ZERO_KEYS:
                if obj[k]!='R$ 0,00': obj[k]='R$ 0,00'; changed=True
            else: changed=zero_payload(obj[k]) or changed
    elif isinstance(obj,list):
        for item in obj: changed=zero_payload(item) or changed
    return changed
zero=("", "0", "0,00", "R$ 0,00")
non_inss_before={
 'margin':c.execute("SELECT COUNT(*) FROM clients WHERE UPPER(TRIM(COALESCE(convenio,'')))<>'INSS' AND COALESCE(TRIM(ultima_margem_disponivel),'') NOT IN (?,?,?,?)",zero).fetchone()[0],
 'refin':c.execute("SELECT COUNT(*) FROM promobank_contratos pcx JOIN promobank_consultas q ON q.id=pcx.consulta_id JOIN clients x ON x.id=q.client_id WHERE UPPER(TRIM(COALESCE(x.convenio,'')))<>'INSS' AND COALESCE(TRIM(pcx.refin_disp),'') NOT IN (?,?,?,?)",zero).fetchone()[0],
 'porta':c.execute("SELECT COUNT(*) FROM promobank_contratos pcx JOIN promobank_consultas q ON q.id=pcx.consulta_id JOIN clients x ON x.id=q.client_id WHERE UPPER(TRIM(COALESCE(x.convenio,'')))<>'INSS' AND COALESCE(TRIM(pcx.porta_disp),'') NOT IN (?,?,?,?)",zero).fetchone()[0]
}
c.execute('BEGIN IMMEDIATE')
clients_changed=c.execute("UPDATE clients SET ultima_margem_disponivel='R$ 0,00',store_margin=0.0 WHERE UPPER(TRIM(COALESCE(convenio,'')))='INSS' AND (COALESCE(TRIM(ultima_margem_disponivel),'') NOT IN ('','0','0,00','R$ 0,00') OR COALESCE(store_margin,0)<>0)").rowcount
contracts_changed=c.execute("UPDATE promobank_contratos SET refin_disp='R$ 0,00',porta_disp='R$ 0,00' WHERE consulta_id IN (SELECT q.id FROM promobank_consultas q JOIN clients x ON x.id=q.client_id WHERE UPPER(TRIM(COALESCE(x.convenio,'')))='INSS') AND (COALESCE(TRIM(refin_disp),'') NOT IN ('','0','0,00','R$ 0,00') OR COALESCE(TRIM(porta_disp),'') NOT IN ('','0','0,00','R$ 0,00'))").rowcount
json_changed=0
rows=c.execute("SELECT q.id,q.dados_json FROM promobank_consultas q JOIN clients x ON x.id=q.client_id WHERE UPPER(TRIM(COALESCE(x.convenio,'')))='INSS' AND COALESCE(q.dados_json,'')<>''").fetchall()
for rowid,raw in rows:
    try: data=json.loads(raw)
    except Exception: continue
    if zero_payload(data):
        c.execute('UPDATE promobank_consultas SET dados_json=? WHERE id=?',(json.dumps(data,ensure_ascii=False,separators=(',',':')),rowid)); json_changed+=1
c.commit()
readback={
 'inss_nonzero_margin':c.execute("SELECT COUNT(*) FROM clients WHERE UPPER(TRIM(COALESCE(convenio,'')))='INSS' AND COALESCE(TRIM(ultima_margem_disponivel),'') NOT IN (?,?,?,?)",zero).fetchone()[0],
 'inss_nonzero_refin':c.execute("SELECT COUNT(*) FROM promobank_contratos pcx JOIN promobank_consultas q ON q.id=pcx.consulta_id JOIN clients x ON x.id=q.client_id WHERE UPPER(TRIM(COALESCE(x.convenio,'')))='INSS' AND COALESCE(TRIM(pcx.refin_disp),'') NOT IN (?,?,?,?)",zero).fetchone()[0],
 'inss_nonzero_porta':c.execute("SELECT COUNT(*) FROM promobank_contratos pcx JOIN promobank_consultas q ON q.id=pcx.consulta_id JOIN clients x ON x.id=q.client_id WHERE UPPER(TRIM(COALESCE(x.convenio,'')))='INSS' AND COALESCE(TRIM(pcx.porta_disp),'') NOT IN (?,?,?,?)",zero).fetchone()[0],
 'quick_check':c.execute('PRAGMA quick_check').fetchone()[0]
}
non_inss_after={
 'margin':c.execute("SELECT COUNT(*) FROM clients WHERE UPPER(TRIM(COALESCE(convenio,'')))<>'INSS' AND COALESCE(TRIM(ultima_margem_disponivel),'') NOT IN (?,?,?,?)",zero).fetchone()[0],
 'refin':c.execute("SELECT COUNT(*) FROM promobank_contratos pcx JOIN promobank_consultas q ON q.id=pcx.consulta_id JOIN clients x ON x.id=q.client_id WHERE UPPER(TRIM(COALESCE(x.convenio,'')))<>'INSS' AND COALESCE(TRIM(pcx.refin_disp),'') NOT IN (?,?,?,?)",zero).fetchone()[0],
 'porta':c.execute("SELECT COUNT(*) FROM promobank_contratos pcx JOIN promobank_consultas q ON q.id=pcx.consulta_id JOIN clients x ON x.id=q.client_id WHERE UPPER(TRIM(COALESCE(x.convenio,'')))<>'INSS' AND COALESCE(TRIM(pcx.porta_disp),'') NOT IN (?,?,?,?)",zero).fetchone()[0]
}
c.close()
if readback['quick_check']!='ok' or any(readback[k] for k in ('inss_nonzero_margin','inss_nonzero_refin','inss_nonzero_porta')) or non_inss_before!=non_inss_after:
    raise SystemExit('SCOPED_READBACK_FAILED '+json.dumps({'readback':readback,'non_inss_before':non_inss_before,'non_inss_after':non_inss_after}))
print(json.dumps({'restored_from':source,'unscoped_copy':mistake,'clients_changed':clients_changed,'contracts_changed':contracts_changed,'consultation_json_changed':json_changed,'readback':readback,'non_inss_preserved':non_inss_after}))
'''

def run(remote_code):
    payload = base64.b64encode(remote_code.encode('utf-8')).decode('ascii')
    remote = f'''python3 -c "import base64;exec(base64.b64decode('{payload}'))"'''
    cmd = [SSH, '-i', KEY, '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=20', HOST, remote]
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=180)
    print(proc.stdout, end='')
    if proc.stderr:
        print(proc.stderr, file=sys.stderr, end='')
    raise SystemExit(proc.returncode)

if __name__ == '__main__':
    mode=sys.argv[1] if len(sys.argv)>1 else 'inspect'
    if mode=='apply':
        run(APPLY)
    if mode=='verify':
        subprocess.run([SSH,'-i',KEY,'-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','ConnectTimeout=20',HOST,'systemctl restart omega-crm.service'],check=True,timeout=60)
        run(VERIFY)
    if mode=='scoped-inss':
        subprocess.run([SSH,'-i',KEY,'-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','ConnectTimeout=20',HOST,'systemctl stop omega-crm.service'],check=True,timeout=60)
        try: run(SCOPED_INSS)
        finally: subprocess.run([SSH,'-i',KEY,'-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','ConnectTimeout=20',HOST,'systemctl start omega-crm.service'],timeout=60)
    run(INSPECT)
