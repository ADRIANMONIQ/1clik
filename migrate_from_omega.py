import sqlite3
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SOURCE = BASE_DIR.parent / "omega_clientes.db"
TARGET = BASE_DIR / "data" / "1clik.db"
TABLES = ["whatsapp_channels", "whatsapp_templates", "whatsapp_messages", "whatsapp_campaigns", "whatsapp_campaign_items"]

def main():
    if not SOURCE.exists():
        raise SystemExit(f"Banco de origem nao encontrado: {SOURCE}")
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    source, target = sqlite3.connect(SOURCE), sqlite3.connect(TARGET)
    copied = {}
    try:
        target.execute("CREATE TABLE IF NOT EXISTS migration_state (source_path TEXT PRIMARY KEY, migrated_at TEXT NOT NULL, details TEXT)")
        target.execute("PRAGMA foreign_keys=OFF")
        for table in TABLES:
            schema = source.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            if not schema or not schema[0]:
                continue
            target.execute(schema[0])
            columns = [row[1] for row in source.execute(f'PRAGMA table_info("{table}")')]
            rows = source.execute(f'SELECT * FROM "{table}"').fetchall()
            quoted = ",".join(f'"{column}"' for column in columns)
            placeholders = ",".join("?" for _ in columns)
            if rows:
                target.executemany(f'INSERT OR IGNORE INTO "{table}" ({quoted}) VALUES ({placeholders})', rows)
            copied[table] = len(rows)
        target.execute("INSERT INTO migration_state(source_path,migrated_at,details) VALUES(?,?,?) ON CONFLICT(source_path) DO UPDATE SET migrated_at=excluded.migrated_at,details=excluded.details", (str(SOURCE), datetime.now().isoformat(timespec="seconds"), repr(copied)))
        target.commit()
    finally:
        source.close(); target.close()
    print("Migracao concluida sem alterar o CRM:")
    for table, count in copied.items():
        print(f"- {table}: {count}")

if __name__ == "__main__":
    main()
