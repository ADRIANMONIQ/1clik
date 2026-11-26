import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
con = sqlite3.connect(ROOT / "data" / "1clik.db")
con.row_factory = sqlite3.Row
rows = con.execute(
    "SELECT id,nome,telefone,mode,active,rotation_enabled,created_at,updated_at FROM whatsapp_channels ORDER BY id"
).fetchall()
print([dict(row) for row in rows])
con.close()
