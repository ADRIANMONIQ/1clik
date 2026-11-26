from datetime import datetime

from meta_api import CredentialVault


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _columns(connection, table):
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def _add_column(connection, table, definition):
    name = definition.split()[0]
    if name not in _columns(connection, table):
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def initialize_meta_schema(connection, key_path):
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS meta_official_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id INTEGER NOT NULL,
            meta_template_id TEXT,
            name TEXT NOT NULL,
            language TEXT NOT NULL,
            category TEXT,
            status TEXT NOT NULL,
            header_text TEXT,
            body_text TEXT,
            footer_text TEXT,
            buttons_json TEXT,
            components_json TEXT NOT NULL,
            variables_json TEXT NOT NULL,
            synced_at TEXT NOT NULL,
            UNIQUE(channel_id,name,language)
        );
        CREATE INDEX IF NOT EXISTS idx_meta_templates_channel_status ON meta_official_templates(channel_id,status);

        CREATE TABLE IF NOT EXISTS meta_campaign_send_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            item_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            template_id INTEGER NOT NULL,
            original_phone TEXT,
            normalized_phone TEXT,
            variables_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pendente',
            http_status INTEGER,
            error_message TEXT,
            meta_error_code TEXT,
            wamid TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TEXT,
            last_attempt_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(campaign_id,item_id)
        );
        CREATE INDEX IF NOT EXISTS idx_meta_attempt_queue ON meta_campaign_send_attempts(status,next_attempt_at,campaign_id);

        CREATE TABLE IF NOT EXISTS meta_audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            channel_id INTEGER,
            campaign_id INTEGER,
            action TEXT NOT NULL,
            status TEXT NOT NULL,
            message TEXT,
            technical_json TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_meta_audit_created ON meta_audit_logs(created_at DESC);

        CREATE TABLE IF NOT EXISTS meta_webhook_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_key TEXT NOT NULL UNIQUE,
            channel_id INTEGER,
            event_type TEXT NOT NULL,
            provider_message_id TEXT,
            phone TEXT,
            payload_json TEXT NOT NULL,
            received_at TEXT NOT NULL,
            processed_at TEXT,
            processing_error TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_meta_webhook_received ON meta_webhook_events(received_at DESC);
        CREATE INDEX IF NOT EXISTS idx_meta_webhook_phone ON meta_webhook_events(phone,received_at DESC);

        CREATE TABLE IF NOT EXISTS meta_phone_suppressions (
            normalized_phone TEXT PRIMARY KEY,
            suppression_type TEXT NOT NULL,
            error_code TEXT NOT NULL,
            reason TEXT NOT NULL,
            source_campaign_id INTEGER,
            source_item_id INTEGER,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            blocked_until TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            reactivated_at TEXT,
            reactivated_by_user_id INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_meta_suppressions_active
            ON meta_phone_suppressions(active,suppression_type,blocked_until);
    """)

    channel_columns = (
        "meta_waba_id TEXT", "meta_app_secret TEXT", "meta_access_token_enc TEXT",
        "meta_app_secret_enc TEXT", "meta_token_masked TEXT", "meta_connection_status TEXT",
        "meta_connection_details_json TEXT", "meta_last_tested_at TEXT",
    )
    for definition in channel_columns:
        _add_column(connection, "whatsapp_channels", definition)

    campaign_columns = (
        "official_template_id INTEGER", "template_language TEXT", "variable_map_json TEXT",
        "interval_seconds INTEGER NOT NULL DEFAULT 30", "max_sends INTEGER",
        "scheduled_at TEXT", "started_at TEXT", "completed_at TEXT", "cancel_requested INTEGER NOT NULL DEFAULT 0",
        "preview_text TEXT", "send_mode TEXT", "channel_strategy TEXT NOT NULL DEFAULT 'single'",
        "channel_ids_json TEXT", "buttons_json TEXT", "media_path TEXT",
        "header_media_ids_json TEXT",
    )
    for definition in campaign_columns:
        _add_column(connection, "whatsapp_campaigns", definition)

    item_columns = (
        "original_phone TEXT", "normalized_phone TEXT", "variables_json TEXT",
        "attempt_count INTEGER NOT NULL DEFAULT 0", "last_http_status INTEGER",
        "last_error TEXT", "wamid TEXT", "last_attempt_at TEXT",
    )
    for definition in item_columns:
        _add_column(connection, "whatsapp_campaign_items", definition)
    connection.execute("UPDATE whatsapp_campaign_items SET original_phone=telefone WHERE original_phone IS NULL")

    user_columns = _columns(connection, "app_users")
    if "role" not in user_columns:
        connection.execute("ALTER TABLE app_users ADD COLUMN role TEXT NOT NULL DEFAULT 'master'")
    connection.execute("UPDATE app_users SET role='master' WHERE role IS NULL OR role=''")

    vault = CredentialVault(key_path)
    rows = connection.execute("SELECT id,meta_access_token,meta_app_secret,meta_access_token_enc,meta_app_secret_enc FROM whatsapp_channels").fetchall()
    for row in rows:
        updates = []
        params = []
        if row["meta_access_token"] and not row["meta_access_token_enc"]:
            updates.extend(("meta_access_token_enc=?", "meta_access_token=''"))
            params.append(vault.encrypt(row["meta_access_token"]))
        if row["meta_app_secret"] and not row["meta_app_secret_enc"]:
            updates.extend(("meta_app_secret_enc=?", "meta_app_secret=''"))
            params.append(vault.encrypt(row["meta_app_secret"]))
        if updates:
            params.extend((_now(), row["id"]))
            connection.execute(f"UPDATE whatsapp_channels SET {','.join(updates)},updated_at=? WHERE id=?", params)
