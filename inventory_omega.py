import json
import sqlite3
from pathlib import Path


source = Path(__file__).resolve().parents[1] / "omega_clientes.db"
connection = sqlite3.connect(source)
tables = [
    row[0]
    for row in connection.execute(
        """SELECT name FROM sqlite_master
           WHERE type='table' AND (name LIKE 'whatsapp%' OR name LIKE '%campaign%')
           ORDER BY name"""
    )
]
result = []
for table in tables:
    columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
    count = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    result.append({"table": table, "count": count, "columns": columns})
print(json.dumps(result, ensure_ascii=False, indent=2))
print("\nCHANNELS (sem credenciais)")
for row in connection.execute(
    """SELECT id,nome,telefone,mode,official_number,active,web_cdp_port,
              web_profile_dir,rotation_enabled,last_used_at
       FROM whatsapp_channels ORDER BY id"""
):
    print(dict(zip(["id", "nome", "telefone", "mode", "official_number", "active", "web_cdp_port", "web_profile_dir", "rotation_enabled", "last_used_at"], row)))
print("\nTEMPLATES")
for row in connection.execute("SELECT id,nome,active,LENGTH(mensagem) FROM whatsapp_templates ORDER BY id"):
    print({"id": row[0], "nome": row[1], "active": row[2], "chars": row[3]})
