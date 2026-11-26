import sqlite3
from pathlib import Path


database = Path(__file__).resolve().parents[1] / "data" / "1clik.db"
with sqlite3.connect(database) as connection:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(whatsapp_channels)")}
    required = {"meta_phone_number_id", "meta_access_token", "meta_waba_id", "meta_app_secret"}
    missing = sorted(required - columns)
    key_row = connection.execute(
        "SELECT setting_value FROM app_settings WHERE setting_key=?", ("internal_api_key",)
    ).fetchone()
    plaintext_secrets = connection.execute(
        "SELECT COUNT(*) FROM whatsapp_channels WHERE mode='meta' AND (coalesce(meta_access_token,'')<>'' OR coalesce(meta_app_secret,'')<>'')"
    ).fetchone()[0]
    active_official = connection.execute(
        "SELECT COUNT(*) FROM whatsapp_campaigns WHERE official_template_id IS NOT NULL AND status IN ('agendada','em processamento')"
    ).fetchone()[0]

if missing:
    raise SystemExit(f"Campos ausentes: {', '.join(missing)}")
if not key_row or not key_row[0].startswith("1clk_") or len(key_row[0]) < 32:
    raise SystemExit("API Key interna ausente ou inválida")
if plaintext_secrets:
    raise SystemExit(f"Existem {plaintext_secrets} canais Meta com segredo em texto puro")
key_file = database.parents[1] / "secrets" / "meta_credentials.key"
if not key_file.exists():
    raise SystemExit("Chave de criptografia externa ao banco não encontrada")
print(f"VALIDATION_OK official_api_fields=4 encrypted_credentials=ready internal_api_key=ready active_official={active_official}")
