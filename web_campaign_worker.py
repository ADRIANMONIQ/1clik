import json
import sqlite3
import time
from contextlib import closing
from datetime import datetime, time as clock_time
from pathlib import Path

from whatsapp_bridge import send, send_poll


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "1clik.db"
LOCK_PATH = BASE_DIR / "data" / "web_campaign_worker.lock"


def now():
    return datetime.now().isoformat(timespec="seconds")


def db():
    connection = sqlite3.connect(DB_PATH, timeout=20)
    connection.row_factory = sqlite3.Row
    return connection



def next_item():
    with closing(db()) as connection, connection:
        campaign = connection.execute("""
            SELECT * FROM whatsapp_campaigns
             WHERE send_mode='web' AND status='em processamento' AND cancel_requested=0
             ORDER BY id LIMIT 1
        """).fetchone()
        if not campaign:
            return None, None
        if campaign["max_sends"] and int(campaign["enviados"] or 0) >= int(campaign["max_sends"]):
            # Pause at a controlled test limit and preserve the other contacts.
            connection.execute(
                "UPDATE whatsapp_campaigns SET status='pausada',completed_at=NULL,updated_at=? WHERE id=?",
                (now(), campaign["id"]),
            )
            return None, None
        item = connection.execute("SELECT * FROM whatsapp_campaign_items WHERE campaign_id=? AND status='pendente' ORDER BY id LIMIT 1", (campaign["id"],)).fetchone()
        if not item:
            failures = connection.execute("SELECT COUNT(*) FROM whatsapp_campaign_items WHERE campaign_id=? AND status IN ('falhou','ignorado')", (campaign["id"],)).fetchone()[0]
            final = "concluída com falhas" if failures else "concluída"
            connection.execute("UPDATE whatsapp_campaigns SET status=?,falhas=?,completed_at=?,updated_at=? WHERE id=?", (final, failures, now(), now(), campaign["id"]))
            return None, None
        connection.execute("UPDATE whatsapp_campaign_items SET status='processando',attempt_count=attempt_count+1,last_attempt_at=?,updated_at=? WHERE id=? AND status='pendente'", (now(), now(), item["id"]))
        return dict(campaign), dict(item)


def process(campaign, item):
    ids = json.loads(campaign.get("channel_ids_json") or "[]") or [campaign["channel_id"]]
    index = (int(item["id"]) - 1) % len(ids) if campaign.get("channel_strategy") == "rotation" else 0
    channel_id = int(ids[index])
    with closing(db()) as connection:
        channel = connection.execute("SELECT * FROM whatsapp_channels WHERE id=? AND active=1 AND mode='web'", (channel_id,)).fetchone()
    variables = json.loads(item.get("variables_json") or "{}")
    message = variables.get("message") if isinstance(variables, dict) else campaign.get("mensagem", "")
    if not channel:
        result = {"ok": False, "status": "falhou", "error": "Conexão por QR Code indisponível."}
    else:
        result = send(item.get("normalized_phone") or item.get("telefone"), message, campaign.get("media_path") or "", channel["web_cdp_port"], channel["web_profile_dir"])
        if result.get("ok") and campaign.get("interaction_mode") == "poll" and not variables.get("poll_already_sent"):
            poll_options = json.loads(campaign.get("buttons_json") or "[]")
            poll_result = send_poll(
                item.get("normalized_phone") or item.get("telefone"),
                campaign.get("poll_question") or "Como deseja continuar?",
                poll_options,
                channel["web_cdp_port"],
                channel["web_profile_dir"],
                reuse_current_chat=True,
            )
            result["poll"] = poll_result
            # The main message was already delivered. Never retry it and cause
            # a duplicate only because the follow-up poll failed.
            if not poll_result.get("ok"):
                result["warning"] = "Mensagem enviada, mas a enquete falhou: " + str(poll_result.get("error") or poll_result.get("status"))
    timestamp = now()
    if result.get("ok"):
        new_status = "enviado"
    elif result.get("status") == "numero_sem_whatsapp":
        new_status = "ignorado"
    else:
        new_status = "falhou"
    with closing(db()) as connection, connection:
        item_note = str(result.get("warning") or "")[:500] if result.get("ok") else str(result.get("error") or result.get("status"))[:500]
        connection.execute("UPDATE whatsapp_campaign_items SET status=?,last_error=?,updated_at=? WHERE id=?", (new_status, item_note or None, timestamp, item["id"]))
        if result.get("ok"):
            connection.execute("UPDATE whatsapp_campaigns SET enviados=enviados+1,updated_at=? WHERE id=?", (timestamp, campaign["id"]))
        else:
            connection.execute("UPDATE whatsapp_campaigns SET falhas=falhas+1,updated_at=? WHERE id=?", (timestamp, campaign["id"]))
        connection.execute("""INSERT INTO whatsapp_messages(client_id,user_id,channel_id,direction,modalidade,status,telefone,telefone_conectado,template_id,mensagem,provider_message_id,raw_json,created_at,updated_at)
                              VALUES(NULL,NULL,?,'outbound','web',?,?,NULL,NULL,?,NULL,?,?,?)""",
                           (channel_id, result.get("status", new_status), item.get("telefone"), message, json.dumps(result, ensure_ascii=False), timestamp, timestamp))
    return result


def main():
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock = LOCK_PATH.open("a+b")
    try:
        import msvcrt
        lock.seek(0)
        if lock.tell() == 0:
            lock.write(b"0"); lock.flush()
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except (ImportError, OSError):
        lock.close()
        return
    try:
        while True:
            with closing(db()) as connection:
                active = connection.execute("SELECT 1 FROM whatsapp_campaigns WHERE send_mode='web' AND status='em processamento' LIMIT 1").fetchone()
            if not active:
                return
            campaign, item = next_item()
            if not campaign or not item:
                time.sleep(1)
                continue
            result = process(campaign, item)
            # A number that is not registered cannot receive anything, so
            # classify it as failed and continue without consuming the normal
            # interval reserved for successful outbound messages.
            if result.get("status") != "numero_sem_whatsapp":
                time.sleep(max(1, int(campaign.get("interval_seconds") or 30)))
    finally:
        lock.close()


if __name__ == "__main__":
    main()

