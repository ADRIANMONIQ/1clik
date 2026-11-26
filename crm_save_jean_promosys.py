import sqlite3, json, shutil, datetime, os
DB='/opt/omega-crm/omega_clientes.db'
BACKUP_DIR='/opt/omega-crm/backups'
os.makedirs(BACKUP_DIR,exist_ok=True)
stamp=datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
backup=f'{BACKUP_DIR}/omega_clientes.before_jean_promosys_{stamp}.db'
src=sqlite3.connect(DB,timeout=60); dst=sqlite3.connect(backup); src.backup(dst); dst.close()
if sqlite3.connect(backup).execute('PRAGMA quick_check').fetchone()[0]!='ok': raise SystemExit('BACKUP_INVALID')
src.row_factory=sqlite3.Row
matches=src.execute("SELECT * FROM clients WHERE replace(replace(cpf,'.',''),'-','')=?",('18439411200',)).fetchall()
if len(matches)!=1 or matches[0]['id']!=2405 or matches[0]['convenio'].upper()!='INSS': raise SystemExit('CLIENT_GUARD_FAILED')
client=matches[0]; data=json.loads(client['raw_json'] or '{}')
promo={
 'origem':'Promosys','cpf':'18439411200','beneficio':'2018739691','especie':'42 - Aposentadoria por tempo de contribuicao','situacao':'ATIVO',
 'data_nascimento':'02/05/1967','nome_mae':'BEATRIZ CAMPOS DA SILVA','endereco':'AV. GENERALISSIMO DEODORO 2041','bairro':'CREMACAO','cidade':'BELEM','uf':'PA','cep':'66040-365',
 'contatos':['(91) 98447-5682','(91) 98409-8613'],'banco_pagamento':'0069 - CREFISA','meio_pagamento':'CARTÃO MAGNÉTICO','agencia':'1','conta':'','ddb':'27/07/2021',
 'valor_beneficio':'R$ 4.052,80','valor_base_calculo':'R$ 4.052,80','margem_disponivel':'R$ 0,70','margem_comprometida':'R$ 1.417,78','margem_rmc':'R$ 0,00','margem_rcc':'R$ 0,00',
 'novo_contrato_media':'R$ 0,00','novo_contrato_count':0,
 'refin_portabilidade':[{'contrato':'0348622665JCD','bancos_validos':2,'soma':'R$ 3.061,53','media':'R$ 1.530,77'},{'contrato':'2588469532','bancos_validos':13,'soma':'R$ 17.699,56','media':'R$ 1.361,50'},{'contrato':'820265296','bancos_validos':18,'soma':'R$ 253.074,47','media':'R$ 14.059,69'}],
 'refinanciamento':[{'contrato':'0348622665JCD','valor_cliente':'R$ 1.649,79'},{'contrato':'2588469532','valor_cliente':'R$ 0,00','motivo':'Sem simulacao disponivel'},{'contrato':'820265296','valor_cliente':'R$ 14.192,61','banco_destino':'335 - DIGIO'}],
 'cartoes':[{'tipo':'RMC','banco':'318 - BANCO BMG S A','contrato':'28331765','situacao':'Ativo','valor_contrato':'R$ 6.484,00','parcela':'R$ 175,05','taxa':'2,46%'},{'tipo':'RCC','banco':'243 - BANCO MASTER S A','contrato':'801361953','situacao':'Ativo','valor_contrato':'R$ 4.069,49','parcela':'R$ 175,05','taxa':'2,46%'}],
 'consultado_em':datetime.datetime.now().isoformat(timespec='seconds')
}
data['promosys_beneficio_atual']=promo
data['promosys_beneficios']=[x for x in data.get('promosys_beneficios',[]) if str(x.get('beneficio'))!='2018739691']+[promo]
data['promosys_contatos']=['(91) 98447-5682','(91) 98409-8613']
now=datetime.datetime.now().isoformat(timespec='seconds')
src.execute('BEGIN IMMEDIATE')
src.execute("UPDATE clients SET data_nascimento=?,matricula=?,beneficio=?,benefit_status='ATIVO',ultima_margem_disponivel='R$ 0,70',ultima_margem_cartao='R$ 0,00',ultima_margem_beneficio='R$ 0,00',ultima_consulta_margem_at=?,ultimo_status_margem='CONSULTADO PROMOSYS',store_margin=0.70,raw_json=?,updated_at=? WHERE id=2405",('02/05/1967','2018739691','2018739691',now,json.dumps(data,ensure_ascii=False,separators=(',',':')),now))
consult_data={'origem':'Promosys','cpf':'18439411200','beneficio':'2018739691','especie':promo['especie'],'dados_pessoais':{k:promo[k] for k in ['nome_mae','data_nascimento','endereco','bairro','cidade','uf','cep']},'contatos_promosys':promo['contatos'],'margem_disponivel':'R$ 0,70','margem_comprometida':'R$ 1.417,78','margem_rmc':'R$ 0,00','margem_rcc':'R$ 0,00','novo_contrato_media':'R$ 0,00','contratos':promo['refin_portabilidade'],'refinanciamento':promo['refinanciamento'],'cartoes':promo['cartoes'],'consulta_promosys_reutilizada':True}
existing=src.execute("SELECT id FROM promobank_consultas WHERE id=22779 AND client_id=2405").fetchone()
if not existing: raise SystemExit('CONSULTATION_GUARD_FAILED')
qid=existing['id']
if src.execute('SELECT COUNT(*) FROM promobank_contratos WHERE consulta_id=?',(qid,)).fetchone()[0]: raise SystemExit('CONTRACTS_ALREADY_PRESENT_GUARD')
src.execute("UPDATE promobank_consultas SET cpf=?,status='success',dados_json=?,error_message_sanitized=NULL,finished_at=? WHERE id=?",('18439411200',json.dumps(consult_data,ensure_ascii=False,separators=(',',':')),now,qid))
contracts=[
 ('3295 - ICRED (QI SOC. DO CRÉD.) - 329','05/2026','04/2031','R$ 5.006,65','', 'R$ 147,00','1,95%','60','54','0348622665JCD','R$ 1.649,79','R$ 1.530,77','10/04/2026'),
 ('341 - BANCO ITAU SA','05/2024','04/2031','R$ 5.788,48','', 'R$ 138,62','1,75%','84','54','2588469532','R$ 0,00','R$ 1.361,50','09/04/2024'),
 ('954 - BANCO CBSS S/A','08/2023','07/2030','R$ 0,00','', 'R$ 1.132,16','1,55%','84','45','820265296','R$ 14.192,61','R$ 14.059,69','28/07/2023')]
for banco,inicio,fim,financiado,quitacao,parcela,taxa,prazo,resta,num,refin,porta,averbado in contracts:
 src.execute("INSERT INTO promobank_contratos(consulta_id,banco_averbado,inicio,fim,financiado,quitacao,parcela,taxa_aprox,prazo,resta,numero_contrato,refin_disp,porta_disp,averbado) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(qid,banco,inicio,fim,financiado,quitacao,parcela,taxa,prazo,resta,num,refin,porta,averbado))
src.commit()
quick=src.execute('PRAGMA quick_check').fetchone()[0]
saved=src.execute('SELECT id,ultima_margem_disponivel,store_margin,raw_json FROM clients WHERE id=2405').fetchone()
rows=src.execute('SELECT numero_contrato,refin_disp,porta_disp FROM promobank_contratos WHERE consulta_id=? ORDER BY id',(qid,)).fetchall()
if quick!='ok' or len(rows)!=3 or json.loads(saved['raw_json']).get('promosys_beneficio_atual',{}).get('beneficio')!='2018739691': raise SystemExit('READBACK_FAILED')
print(json.dumps({'backup':backup,'client_id':2405,'consultation_id':qid,'margin':saved['ultima_margem_disponivel'],'contracts':[dict(r) for r in rows],'quick_check':quick,'crm_contacts_preserved':data.get('contatos_crm'),'promosys_contacts':data.get('promosys_contatos')},ensure_ascii=False))
src.close()
