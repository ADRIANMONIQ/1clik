import argparse
import json
import sqlite3
import time
from contextlib import closing
from datetime import datetime, time as clock_time, timedelta
from pathlib import Path

from meta_api import CredentialVault, MetaApiError, MetaClient


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "1clik.db"
KEY_PATH = BASE_DIR / "secrets" / "meta_credentials.key"
LOCK_PATH = BASE_DIR / "data" / "campaign_worker.lock"
UPLOAD_DIR = BASE_DIR / "data" / "uploads"
MAX_ATTEMPTS = 3
TEST_CAMPAIGN_MAX_CONTACTS = 2


def now():
    return datetime.now().isoformat(timespec="seconds")


def db():
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def resolve_media_path(value):
    """Keep saved campaigns usable after the standalone 1Clik folder is moved."""
    media_path = Path(str(value or ""))
    if media_path.is_file():
        return str(media_path)
    relocated = UPLOAD_DIR / media_path.name
    if media_path.name and relocated.is_file():
        return str(relocated)
    return str(media_path)


def audit(connection, campaign_id, channel_id, action, status, message, technical=None):
    connection.execute(
        "INSERT INTO meta_audit_logs(channel_id,campaign_id,action,status,message,technical_json,created_at) VALUES(?,?,?,?,?,?,?)",
        (channel_id, campaign_id, action, status, message[:500], json.dumps(technical or {}, ensure_ascii=False), now()),
    )


def recover_stale_attempts():
    cutoff = (datetime.now() - timedelta(minutes=10)).isoformat(timespec="seconds")
    with closing(db()) as connection, connection:
        stale = connection.execute("SELECT campaign_id,item_id FROM meta_campaign_send_attempts WHERE status='processando' AND updated_at<?", (cutoff,)).fetchall()
        for row in stale:
            message = "Resultado desconhecido apÃ³s interrupÃ§Ã£o; nÃ£o reenviado para evitar duplicidade."
            connection.execute("UPDATE meta_campaign_send_attempts SET status='falhou',error_message=?,updated_at=? WHERE campaign_id=? AND item_id=?", (message, now(), row["campaign_id"], row["item_id"]))
            connection.execute("UPDATE whatsapp_campaign_items SET status='falhou',last_error=?,updated_at=? WHERE id=?", (message, now(), row["item_id"]))
            audit(connection, row["campaign_id"], None, "stale_attempt", "falhou", message)


def activate_scheduled_campaigns():
    with closing(db()) as connection, connection:
        connection.execute(
            "UPDATE whatsapp_campaigns SET status='em processamento',started_at=COALESCE(started_at,?),updated_at=? WHERE status='agendada' AND scheduled_at<=? AND (send_mode='meta' OR official_template_id IS NOT NULL)",
            (now(), now(), now()),
        )


def claim_next_attempt(test_only=False):
    connection = db()
    try:
        connection.execute("BEGIN IMMEDIATE")
        test_filter = (
            f" AND c.total<={TEST_CAMPAIGN_MAX_CONTACTS}"
            if test_only else ""
        )
        row = connection.execute(f"""
            SELECT a.*,c.interval_seconds,c.max_sends,c.status AS campaign_status,
                   c.media_path,c.header_media_ids_json,
                   t.name AS template_name,t.language AS template_language,
                   t.components_json AS template_components_json,
                   ch.meta_phone_number_id,ch.meta_access_token_enc,ch.meta_api_version,
                   i.nome AS contact_name
              FROM meta_campaign_send_attempts a
              JOIN whatsapp_campaigns c ON c.id=a.campaign_id
              JOIN whatsapp_campaign_items i ON i.id=a.item_id
              JOIN meta_official_templates t ON t.id=a.template_id AND upper(t.status)='APPROVED'
              JOIN whatsapp_channels ch ON ch.id=a.channel_id AND ch.active=1 AND ch.mode='meta'
             WHERE a.status='pendente'
               AND c.status='em processamento'
               AND (a.next_attempt_at IS NULL OR a.next_attempt_at<=?)
               AND (c.max_sends IS NULL OR c.max_sends<=0 OR c.enviados<c.max_sends)
               {test_filter}
             ORDER BY a.id LIMIT 1
        """, (now(),)).fetchone()
        if not row:
            connection.commit()
            return None
        changed = connection.execute(
            "UPDATE meta_campaign_send_attempts SET status='processando',attempt_count=attempt_count+1,last_attempt_at=?,updated_at=? WHERE id=? AND status='pendente'",
            (now(), now(), row["id"]),
        ).rowcount
        connection.commit()
        return dict(row) if changed else None
    finally:
        connection.close()


def finalize_campaign(connection, campaign_id):
    counts = {row["status"]: row["qty"] for row in connection.execute(
        "SELECT status,COUNT(*) AS qty FROM meta_campaign_send_attempts WHERE campaign_id=? GROUP BY status", (campaign_id,)
    )}
    remaining = counts.get("pendente", 0) + counts.get("processando", 0)
    if remaining:
        return
    failures = counts.get("falhou", 0) + counts.get("ignorado", 0)
    status = "concluÃ­da com falhas" if failures else "concluÃ­da"
    connection.execute(
        "UPDATE whatsapp_campaigns SET status=?,falhas=?,completed_at=?,updated_at=? WHERE id=? AND status='em processamento'",
        (status, failures, now(), now(), campaign_id),
    )
    audit(connection, campaign_id, None, "campaign_complete", status, "Fila da campanha finalizada.", counts)


def maintain_processing_campaigns():
    with closing(db()) as connection, connection:
        campaigns = connection.execute("""
            SELECT id,max_sends,enviados FROM whatsapp_campaigns
             WHERE status='em processamento'
               AND (send_mode='meta' OR official_template_id IS NOT NULL)
        """).fetchall()
        for campaign in campaigns:
            if campaign["max_sends"] and int(campaign["enviados"] or 0) >= int(campaign["max_sends"]):
                connection.execute(
                    "UPDATE whatsapp_campaigns SET status='pausada',falhas=(SELECT COUNT(*) FROM whatsapp_campaign_items WHERE campaign_id=? AND status='falhou'),completed_at=NULL,updated_at=? WHERE id=?",
                    (campaign["id"], now(), campaign["id"]),
                )
                audit(
                    connection, campaign["id"], None, "campaign_limit", "pausada",
                    "Limite mÃ¡ximo atingido. Os contatos restantes permanecem pendentes.",
                    {"max_sends": int(campaign["max_sends"]), "sent": int(campaign["enviados"] or 0)},
                )
                continue
            finalize_campaign(connection, campaign["id"])


def process_attempt(attempt):
    vault = CredentialVault(KEY_PATH)
    try:
        token = vault.decrypt(attempt["meta_access_token_enc"])
        variables_data = json.loads(attempt["variables_json"] or "[]")
        contact_name = str(attempt.get("contact_name") or "").strip()
        if contact_name and isinstance(variables_data, dict):
            short_name = contact_name.split()[0]
            for component in ("header", "body"):
                variables_data[component] = [
                    (
                        {**value, "text": short_name}
                        if isinstance(value, dict) and str(value.get("text") or "").strip().casefold() == contact_name.casefold()
                        else short_name if str(value).strip().casefold() == contact_name.casefold()
                        else value
                    )
                    for value in variables_data.get(component, [])
                ]
            for key, values in variables_data.get("buttons", {}).items():
                variables_data["buttons"][key] = [
                    short_name if str(value).strip().casefold() == contact_name.casefold() else value
                    for value in values
                ]
        used_variables_json = json.dumps(variables_data, ensure_ascii=False)
        with closing(db()) as connection, connection:
            connection.execute(
                "UPDATE meta_campaign_send_attempts SET variables_json=?,updated_at=? WHERE id=?",
                (used_variables_json, now(), attempt["id"]),
            )
            connection.execute(
                "UPDATE whatsapp_campaign_items SET variables_json=?,updated_at=? WHERE id=?",
                (used_variables_json, now(), attempt["item_id"]),
            )
        client = MetaClient(token, attempt["meta_api_version"])
        header_media = None
        components = json.loads(attempt.get("template_components_json") or "[]")
        media_header = next((
            component for component in components
            if str(component.get("type") or "").upper() == "HEADER"
            and str(component.get("format") or "").upper() in ("IMAGE", "VIDEO", "DOCUMENT")
        ), None)
        if media_header:
            media_path = resolve_media_path(attempt.get("media_path"))
            if not media_path:
                raise MetaApiError("Este modelo exige uma imagem de cabeÃ§alho, mas a campanha nÃ£o possui arquivo.", 400, None, False)
            media_ids = json.loads(attempt.get("header_media_ids_json") or "{}")
            media_id = str(media_ids.get(str(attempt["channel_id"])) or "")
            if not media_id:
                media_id = client.upload_media(attempt["meta_phone_number_id"], media_path)
                media_ids[str(attempt["channel_id"])] = media_id
                with closing(db()) as connection, connection:
                    connection.execute(
                        "UPDATE whatsapp_campaigns SET header_media_ids_json=?,updated_at=? WHERE id=?",
                        (json.dumps(media_ids), now(), attempt["campaign_id"]),
                    )
            header_media = {"type": str(media_header.get("format") or "IMAGE").lower(), "id": media_id}
        result = client.send_template(
            attempt["meta_phone_number_id"], attempt["normalized_phone"],
            attempt["template_name"], attempt["template_language"], variables_data, header_media,
        )
        with closing(db()) as connection, connection:
            connection.execute(
                "UPDATE meta_campaign_send_attempts SET status='enviado',http_status=?,wamid=?,error_message=NULL,updated_at=? WHERE id=?",
                (result["http_status"], result["wamid"], now(), attempt["id"]),
            )
            connection.execute(
                "UPDATE whatsapp_campaign_items SET status='enviado',attempt_count=attempt_count+1,last_http_status=?,last_error=NULL,wamid=?,last_attempt_at=?,updated_at=? WHERE id=?",
                (result["http_status"], result["wamid"], now(), now(), attempt["item_id"]),
            )
            connection.execute("UPDATE whatsapp_campaigns SET enviados=enviados+1,updated_at=? WHERE id=?", (now(), attempt["campaign_id"]))
            finalize_campaign(connection, attempt["campaign_id"])
        return max(1, int(attempt["interval_seconds"] or 30))
    except MetaApiError as exc:
        current_attempt = int(attempt["attempt_count"] or 0) + 1
        retry = exc.temporary and current_attempt < MAX_ATTEMPTS
        next_attempt = (datetime.now() + timedelta(seconds=60 * current_attempt)).isoformat(timespec="seconds") if retry else None
        final_status = "pendente" if retry else "falhou"
        safe_message = str(exc)[:500]
        with closing(db()) as connection, connection:
            connection.execute(
                "UPDATE meta_campaign_send_attempts SET status=?,http_status=?,meta_error_code=?,error_message=?,next_attempt_at=?,updated_at=? WHERE id=?",
                (final_status, exc.http_status, str(exc.error_code or ""), safe_message, next_attempt, now(), attempt["id"]),
            )
            connection.execute(
                "UPDATE whatsapp_campaign_items SET status=?,attempt_count=attempt_count+1,last_http_status=?,last_error=?,last_attempt_at=?,updated_at=? WHERE id=?",
                (final_status, exc.http_status, safe_message, now(), now(), attempt["item_id"]),
            )
            audit(connection, attempt["campaign_id"], attempt["channel_id"], "meta_send_error", final_status, safe_message, {"http_status": exc.http_status, "error_code": exc.error_code, "temporary": exc.temporary})
            if not retry:
                finalize_campaign(connection, attempt["campaign_id"])
        return 1


def run_once():
    activate_scheduled_campaigns()
    maintain_processing_campaigns()
    attempt = claim_next_attempt(test_only=False)
    if not attempt:
        return False, 1
    return True, process_attempt(attempt)


def acquire_lock():
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    handle = LOCK_PATH.open("a+b")
    try:
        import msvcrt
        handle.seek(0)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return handle
    except (ImportError, OSError):
        handle.close()
        return None


def main(loop=True):
    lock = acquire_lock()
    if not lock:
        return 0
    recover_stale_attempts()
    try:
        while True:
            processed, delay = run_once()
            if not loop:
                return 0
            time.sleep(delay if processed else max(2, delay))
    finally:
        lock.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    raise SystemExit(main(loop=not args.once))

