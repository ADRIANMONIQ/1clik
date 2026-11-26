import sqlite3

db = r"C:\Users\Vaio\Documents\CRM LOJA\omega_clientes.db"
con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
columns = {row[1] for row in con.execute("PRAGMA table_info(clients)")}
species_column = next((name for name in ("especie", "especie_cliente", "esp") if name in columns), None)
species_select = f", c.{species_column} AS especie" if species_column else ", '' AS especie"
rows = con.execute(
    f"""
    SELECT p.id, p.cpf, p.status, p.created_at, p.finished_at,
           c.nome, c.cpf AS client_cpf, p.dados_json {species_select}
    FROM promobank_consultas p
    LEFT JOIN clients c ON c.id = p.client_id
    WHERE substr(COALESCE(p.finished_at, p.created_at), 1, 10) = ?
      AND lower(COALESCE(p.status, '')) LIKE 'conclu%'
    ORDER BY COALESCE(p.finished_at, p.created_at) DESC
    LIMIT 100
    """,
    ("2026-11-25",),
).fetchall()
for row in rows:
    print(dict(row))
con.close()
