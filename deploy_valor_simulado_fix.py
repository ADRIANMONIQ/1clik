import datetime
import py_compile
import shutil

p = "/opt/omega-crm/app.py"
old = '        valor_simulado_novo = promobank_pick("novo_contrato_media", "valor_simulado_novo_contrato") or "R$ 0,00"'
new = '        valor_simulado_novo_raw = promobank_pick("novo_contrato_media", "valor_simulado_novo_contrato")\n        valor_simulado_novo = money_br(parse_brl_money(valor_simulado_novo_raw))'

s = open(p, encoding="utf-8").read()
if s.count(old) != 1:
    raise SystemExit(f"EXPECTED_LINE_COUNT={s.count(old)}")

stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
backup = f"/opt/omega-crm/backups/app.py.before_valor_simulado_format_{stamp}"
shutil.copy2(p, backup)
open(p, "w", encoding="utf-8", newline="").write(s.replace(old, new))
py_compile.compile(p, doraise=True)
print(f"BACKUP={backup}")
print("PATCHED=1")
