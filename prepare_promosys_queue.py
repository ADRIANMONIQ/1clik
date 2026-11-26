import base64
import json
import subprocess
import sys

SSH = r"C:\Windows\System32\OpenSSH\ssh.exe"
KEY = r"C:\Users\Vaio\.ssh\omega_vps_ed25519"
HOST = "root@187.127.250.163"

REMOTE = r'''
import sqlite3,json
c=sqlite3.connect('/opt/omega-crm/omega_clientes.db')
c.row_factory=sqlite3.Row
rows=c.execute("""
SELECT c.id,c.cpf,c.nome,c.beneficio,c.matricula,c.especie,c.data_nascimento,
       c.updated_at,c.last_service_at
  FROM clients c
 WHERE c.active=1
   AND UPPER(TRIM(COALESCE(c.convenio,'')))='INSS'
   AND UPPER(TRIM(COALESCE(c.benefit_status,'ATIVO'))) NOT IN ('CESSADO','ÓBITO','OBITO')
   AND LENGTH(REPLACE(REPLACE(REPLACE(COALESCE(c.cpf,''),'.',''),'-',''),' ',''))=11
   AND COALESCE(NULLIF(TRIM(c.beneficio),''),NULLIF(TRIM(c.matricula),'')) IS NOT NULL
   AND NOT EXISTS (
       SELECT 1 FROM promobank_consultas pc
        WHERE pc.client_id=c.id
          AND SUBSTR(COALESCE(pc.finished_at,pc.created_at,''),1,10)=DATE('now','localtime')
   )
 ORDER BY COALESCE(NULLIF(c.last_service_at,''),NULLIF(c.updated_at,''),c.id) DESC,c.id DESC
 LIMIT 50
""").fetchall()
out=[]; seen=set()
for r in rows:
    cpf=''.join(ch for ch in (r['cpf'] or '') if ch.isdigit()).zfill(11)
    benefit=str(r['beneficio'] or r['matricula'] or '').strip()
    key=(cpf,benefit)
    if key in seen: continue
    seen.add(key)
    out.append({'client_id':r['id'],'cpf':cpf,'nome':r['nome'],'beneficio':benefit,'especie':str(r['especie'] or '').strip(),'data_nascimento':r['data_nascimento'] or ''})
print(json.dumps(out,ensure_ascii=False))
'''

payload=base64.b64encode(REMOTE.encode()).decode()
remote=f'''python3 -c "import base64;exec(base64.b64decode('{payload}'))"'''
proc=subprocess.run([SSH,'-i',KEY,'-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','ConnectTimeout=20',HOST,remote],capture_output=True,text=True,timeout=120)
if proc.returncode:
    print(proc.stderr,file=sys.stderr); raise SystemExit(proc.returncode)
data=json.loads(proc.stdout)
print(json.dumps({'count':len(data),'first':data[0] if data else None,'ids':[r['client_id'] for r in data]},ensure_ascii=False))
