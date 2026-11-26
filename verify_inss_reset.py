import sqlite3

db = sqlite3.connect("/opt/omega-crm/omega_clientes.db")
quick = db.execute("PRAGMA quick_check").fetchone()[0]
contracts = db.execute(
    """SELECT COUNT(*)
       FROM promobank_contratos pc
       JOIN promobank_consultas q ON q.id = pc.consulta_id
       JOIN clients c ON c.id = q.client_id
       WHERE UPPER(TRIM(COALESCE(c.convenio, ''))) = 'INSS'"""
).fetchone()[0]
nonzero = db.execute(
    """SELECT COUNT(*) FROM clients
       WHERE UPPER(TRIM(COALESCE(convenio, ''))) = 'INSS'
       AND (COALESCE(store_margin, 0) <> 0
         OR COALESCE(TRIM(ultima_margem_disponivel), '') <> 'R$ 0,00'
         OR COALESCE(TRIM(ultima_margem_cartao), '') <> 'R$ 0,00'
         OR COALESCE(TRIM(ultima_margem_beneficio), '') <> 'R$ 0,00')"""
).fetchone()[0]
print(f"QUICK_CHECK={quick}")
print(f"INSS_CONTRACTS={contracts}")
print(f"INSS_NONZERO_MARGINS={nonzero}")
if quick != "ok" or contracts or nonzero:
    raise SystemExit(1)
