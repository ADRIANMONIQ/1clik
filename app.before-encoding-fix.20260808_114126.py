import base64
import html
import csv
import hashlib
import hmac
import io
import json
import mimetypes
import os
import re
import secrets
import smtplib
import sqlite3
import subprocess
import sys
import time
import traceback
import uuid
import webbrowser
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.parser import BytesParser
from email.policy import default
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread, Timer
from urllib.parse import parse_qs, urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from meta_api import CredentialVault, MetaApiError, MetaClient, DEFAULT_GRAPH_API_VERSION, component_text, mask_secret, template_variable_specs
from meta_schema import initialize_meta_schema
from whatsapp_bridge import send, start, status
from insights import campaign_contacts, campaign_summaries, opportunity_data


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "1clik.db"
META_KEY_PATH = BASE_DIR / "secrets" / "meta_credentials.key"
HOST = "127.0.0.1"
PORT = int(os.environ.get("ONECLIK_PORT", "8091"))
ALLOWED_MEDIA = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".mov", ".avi", ".mkv", ".pdf"}
SESSION_COOKIE = "oneclik_session"
INITIAL_USERNAME = "OMEGA.CREDITOS"
INITIAL_PASSWORD_HASH = "pbkdf2_sha256$210000$819b97b9e0259f40982ec2068a0ce676$2ae68f51ac573d31c4dc54912f89c00919f9177ae342bd25e8527b82ac859aef"
RECOVERY_EMAIL = "adrianmoniq@gmail.com"


def now():
    return datetime.now().isoformat(timespec="seconds")


def format_phone(value):
    digits = "".join(char for char in str(value or "") if char.isdigit())
    if digits.startswith("55") and len(digits) in (12, 13):
        digits = digits[2:]
    return f"{digits[:2]} {digits[2:]}" if len(digits) in (10, 11) else str(value or "").strip()


def first_name(value):
    parts = str(value or "").strip().split()
    return parts[0] if parts else ""


def campaign_variable_value(column, value):
    """Keep campaign previews and both send engines on the same personalization rule."""
    normalized = str(column or "").strip().lower().replace(" ", "_")
    return first_name(value) if normalized in ("nome", "name", "nome_cliente") else str(value or "").strip()


def personalize_message(message, row):
    normalized = {str(key).strip().lower().replace(" ", "_"): str(value or "").strip() for key, value in row.items()}
    if "nome" in normalized:
        normalized["nome"] = first_name(normalized["nome"])
    rendered = str(message or "")
    for key, value in normalized.items():
        rendered = rendered.replace("{{" + key + "}}", value)
        rendered = rendered.replace("{{" + key.upper() + "}}", value)
    return rendered


def meta_phone(value):
    digits = "".join(char for char in str(value or "") if char.isdigit())
    if digits.startswith("55") and len(digits) in (12, 13):
        return digits
    return "55" + digits if len(digits) in (10, 11) else ""


def send_meta(channel, phone, message):
    phone_number_id = (channel["meta_phone_number_id"] or "").strip()
    access_token = CredentialVault(META_KEY_PATH).decrypt(channel["meta_access_token_enc"])
    api_version = (channel["meta_api_version"] or DEFAULT_GRAPH_API_VERSION).strip().lstrip("/")
    target = meta_phone(phone)
    if not phone_number_id or not access_token:
        return {"ok": False, "status": "erro_config_api", "error": "Informe o Access Token e o Phone Number ID da Meta."}
    if not target:
        return {"ok": False, "status": "erro_sem_telefone", "error": "NÃºmero de destino invÃ¡lido."}
    payload = {"messaging_product": "whatsapp", "to": target, "type": "text", "text": {"preview_url": False, "body": message or ""}}
    request = Request(
        f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8", errors="replace")
        data = json.loads(raw or "{}")
        provider_id = (data.get("messages") or [{}])[0].get("id", "")
        return {"ok": True, "status": "enviado", "provider_message_id": provider_id, "raw": data}
    except HTTPError as exc:
        return {"ok": False, "status": "erro_api", "error": exc.read().decode("utf-8", errors="replace")[:1000]}
    except (URLError, TimeoutError, OSError) as exc:
        return {"ok": False, "status": "erro_api", "error": str(exc)[:1000]}


def db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def hash_password(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), 210000).hex()
    return f"pbkdf2_sha256$210000${salt}${digest}"


def verify_password(password, stored):
    try:
        algorithm, rounds, salt, expected = stored.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), int(rounds)).hex()
        return hmac.compare_digest(digest, expected)
    except Exception:
        return False


def send_password_reset_email(email, token):
    reset_url = f"http://{HOST}:{PORT}/redefinir-senha?token={token}"
    message = EmailMessage()
    message["Subject"] = "RedefiniÃ§Ã£o de senha do 1Clik"
    message["From"] = os.environ.get("OMEGA_SMTP_FROM", os.environ.get("OMEGA_SMTP_USER", RECOVERY_EMAIL))
    message["To"] = email
    message.set_content(
        "Recebemos uma solicitaÃ§Ã£o para redefinir a senha do 1Clik.\n\n"
        f"Abra este link no computador onde o 1Clik estÃ¡ instalado:\n{reset_url}\n\n"
        "O link expira em 30 minutos e sÃ³ pode ser usado uma vez."
    )
    host = os.environ.get("OMEGA_SMTP_HOST")
    smtp_user = os.environ.get("OMEGA_SMTP_USER")
    smtp_password = os.environ.get("OMEGA_SMTP_PASSWORD")
    if not host or not smtp_user or not smtp_password:
        outbox = DATA_DIR / "outbox"
        outbox.mkdir(parents=True, exist_ok=True)
        (outbox / f"redefinicao_{datetime.now():%Y%m%d_%H%M%S}.eml").write_text(message.as_string(), encoding="utf-8")
        return False
    with smtplib.SMTP(host, int(os.environ.get("OMEGA_SMTP_PORT", "587")), timeout=20) as smtp:
        smtp.starttls()
        smtp.login(smtp_user, smtp_password)
        smtp.send_message(message)
    return True


def initialize():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    with db() as connection:
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT NOT NULL,
            message TEXT,
            media_name TEXT,
            status TEXT NOT NULL,
            error TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at DESC);
        CREATE TABLE IF NOT EXISTS app_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            email TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS app_sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            last_seen TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            expires_at TEXT NOT NULL,
            used_at TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS app_settings (
            setting_key TEXT PRIMARY KEY,
            setting_value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS conversation_handled (
            campaign_item_id INTEGER PRIMARY KEY,
            campaign_id INTEGER NOT NULL,
            handled INTEGER NOT NULL DEFAULT 1,
            handled_by_user_id INTEGER,
            handled_at TEXT,
            updated_at TEXT NOT NULL
        );
        """)
        channel_columns = {row[1] for row in connection.execute("PRAGMA table_info(whatsapp_channels)")}
        if "meta_waba_id" not in channel_columns:
            connection.execute("ALTER TABLE whatsapp_channels ADD COLUMN meta_waba_id TEXT")
        if "meta_app_secret" not in channel_columns:
            connection.execute("ALTER TABLE whatsapp_channels ADD COLUMN meta_app_secret TEXT")
        api_key = connection.execute("SELECT setting_value FROM app_settings WHERE setting_key='internal_api_key'").fetchone()
        if not api_key:
            connection.execute(
                "INSERT INTO app_settings(setting_key,setting_value,updated_at) VALUES('internal_api_key',?,?)",
                ("1clk_" + secrets.token_urlsafe(32), now()),
            )
        webhook_token = connection.execute("SELECT setting_value FROM app_settings WHERE setting_key='meta_webhook_verify_token'").fetchone()
        if not webhook_token:
            connection.execute(
                "INSERT INTO app_settings(setting_key,setting_value,updated_at) VALUES('meta_webhook_verify_token',?,?)",
                ("whk_" + secrets.token_urlsafe(36), now()),
            )
        initialize_meta_schema(connection, META_KEY_PATH)
        campaign_columns = {row[1] for row in connection.execute("PRAGMA table_info(whatsapp_campaigns)")}
        if "interaction_mode" not in campaign_columns:
            connection.execute("ALTER TABLE whatsapp_campaigns ADD COLUMN interaction_mode TEXT NOT NULL DEFAULT 'numbered'")
        if "poll_question" not in campaign_columns:
            connection.execute("ALTER TABLE whatsapp_campaigns ADD COLUMN poll_question TEXT")
        user = connection.execute("SELECT id FROM app_users WHERE username=?", (INITIAL_USERNAME,)).fetchone()
        if user:
            connection.execute("UPDATE app_users SET email=?, active=1, updated_at=? WHERE id=?", (RECOVERY_EMAIL, now(), user["id"]))
        else:
            connection.execute(
                "INSERT INTO app_users(username,password_hash,email,active,created_at,updated_at) VALUES(?,?,?,1,?,?)",
                (INITIAL_USERNAME, INITIAL_PASSWORD_HASH, RECOVERY_EMAIL, now(), now()),
            )


def access_page(title, content):
    return f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>1Clik - {html.escape(title)}</title><style>
    *{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;background:radial-gradient(circle at 15% 10%,#173d73 0,#07182f 42%,#030b17 100%);font:15px Segoe UI,Arial;color:#10233f}}
    .access{{width:min(430px,calc(100% - 32px));background:#fff;border:1px solid #d8e1ed;border-radius:22px;padding:32px;box-shadow:0 28px 70px #0008}}
    .logo{{font-size:34px;font-weight:900;color:#0e2d55;margin-bottom:6px}}.logo b{{color:#f97316}}.subtitle{{color:#64748b;margin:0 0 26px}}
    h1{{font-size:21px;margin:0 0 18px}}label{{display:block;font-weight:750;margin:14px 0 7px}}input{{width:100%;padding:13px 14px;border:1px solid #cbd5e1;border-radius:10px;font:inherit}}input:focus{{outline:3px solid #fed7aa;border-color:#f97316}}
    button{{width:100%;border:0;border-radius:10px;padding:13px;background:#f97316;color:#fff;font-weight:850;font-size:15px;cursor:pointer;margin-top:20px}}button:hover{{background:#df5f0b}}a{{color:#0d5fae;font-weight:700;text-decoration:none}}.links{{display:flex;justify-content:space-between;margin-top:18px;font-size:13px}}.notice{{padding:11px 12px;border-radius:9px;background:#fff1e8;border:1px solid #fdc79f;color:#933d0b;margin-bottom:16px}}.error{{background:#fff0f1;border-color:#f5bbc1;color:#a32331}}
    </style></head><body><section class="access"><div class="logo"><b>1</b>Clik</div><p class="subtitle">Acesso seguro ao sistema</p>{content}</section></body></html>"""


def login_page(message="", error=False):
    notice = f'<div class="notice {"error" if error else ""}">{html.escape(message)}</div>' if message else ""
    return access_page("Entrar", f"""{notice}<h1>Entrar no 1Clik</h1><form method="post" action="/login">
    <label>Login</label><input name="username" autocomplete="username" required autofocus>
    <label>Senha</label><input type="password" name="password" autocomplete="current-password" required>
    <button type="submit">ENTRAR</button></form><div class="links"><a href="/esqueci-senha">Esqueci minha senha</a></div>""")


def forgot_page(message="", error=False):
    notice = f'<div class="notice {"error" if error else ""}">{html.escape(message)}</div>' if message else ""
    return access_page("Esqueci a senha", f"""{notice}<h1>Recuperar acesso</h1><form method="post" action="/esqueci-senha">
    <label>Login ou e-mail</label><input name="identity" required autofocus>
    <button type="submit">ENVIAR RECUPERAÃ‡ÃƒO</button></form><div class="links"><a href="/login">Voltar ao login</a></div>""")


def reset_page(token, message="", error=False):
    notice = f'<div class="notice {"error" if error else ""}">{html.escape(message)}</div>' if message else ""
    return access_page("Redefinir senha", f"""{notice}<h1>Definir nova senha</h1><form method="post" action="/redefinir-senha">
    <input type="hidden" name="token" value="{html.escape(token)}"><label>Nova senha</label><input type="password" name="password" minlength="8" required autofocus>
    <label>Confirmar senha</label><input type="password" name="confirm_password" minlength="8" required>
    <button type="submit">SALVAR NOVA SENHA</button></form>""")


def layout(content, title="Painel", username=""):
    return f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>1Clik - {html.escape(title)}</title><style>
    :root{{--bg:#f4f5f7;--panel:#ffffff;--panel2:#f1f3f5;--line:#d9dee5;--text:#29313d;--muted:#6b7280;--orange:#f97316;--orange-dark:#d95e0d;--orange-soft:#fff0e5;--green:#1f9d55;--red:#d64550}}
    *{{box-sizing:border-box}} body{{margin:0;background:linear-gradient(140deg,#f7f7f8,#eaedf0);color:var(--text);font:15px Segoe UI,Arial,sans-serif;min-height:100vh}}
    header{{height:72px;display:flex;align-items:center;padding:0 28px;border-bottom:1px solid var(--line);background:#fffdfb;box-shadow:0 2px 14px #1f29370a;position:sticky;top:0;z-index:2}}
    .brand{{font-size:24px;font-weight:800;letter-spacing:-.4px}} .brand b{{color:var(--orange)}} .tag{{color:var(--muted);margin-left:14px;font-size:13px}}
    .app-shell{{display:grid;grid-template-columns:230px minmax(0,1fr);min-height:calc(100vh - 72px)}}
    .sidebar{{background:#0b2340;color:#fff;padding:24px 14px;position:sticky;top:72px;height:calc(100vh - 72px)}}.sidebar a{{display:block;color:#dce8f5;text-decoration:none;padding:12px 14px;border-radius:10px;margin:4px 0;font-weight:750}}.sidebar a:hover,.sidebar a.active{{background:#173b63;color:#fff}}.sidebar .menu-title{{padding:0 14px 12px;color:#91abc5;font-size:12px;text-transform:uppercase;letter-spacing:.08em}}
    main{{width:100%;max-width:1220px;margin:28px auto;padding:0 24px}} .grid{{display:grid;grid-template-columns:1.1fr .9fr;gap:22px}} .card{{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:22px;box-shadow:0 12px 28px #1f29370a}}
    h1,h2{{margin:0 0 18px}} h1{{font-size:24px}} h2{{font-size:18px}} label{{display:block;color:#475569;font-weight:700;margin:14px 0 7px}}
    input,textarea,select{{width:100%;border:1px solid #cfd5dc;background:#fff;color:var(--text);border-radius:10px;padding:12px;font:inherit}} input:focus,textarea:focus,select:focus{{outline:3px solid #fed7aa;border-color:var(--orange)}} textarea{{min-height:120px;resize:vertical}}
    button,.button{{display:inline-flex;align-items:center;justify-content:center;border:0;border-radius:10px;padding:12px 17px;font-weight:800;cursor:pointer;text-decoration:none;background:var(--orange);color:white;margin-top:16px;box-shadow:0 4px 10px #f9731633}} button:hover,.button:hover{{background:var(--orange-dark)}}
    .secondary{{background:#e7eaee;color:#334155;margin-left:8px;box-shadow:none}} .status{{display:flex;align-items:center;gap:10px;padding:14px;border-radius:12px;background:var(--panel2)}} .dot{{width:11px;height:11px;border-radius:99px;background:var(--red)}} .dot.on{{background:var(--green);box-shadow:0 0 14px #1f9d5588}}
    .notice{{padding:12px 14px;border-radius:10px;margin-bottom:17px;background:var(--orange-soft);color:#9a3d0a;border:1px solid #fed7aa}} .notice.error{{background:#fff0f1;color:#a32331;border-color:#f6b7bf}}
    table{{width:100%;border-collapse:collapse}} th,td{{padding:11px 8px;border-bottom:1px solid var(--line);text-align:left;font-size:13px}} th{{color:var(--muted)}} .ok{{color:var(--green)}} .fail{{color:var(--red)}} .muted{{color:var(--muted)}}
    @media(max-width:800px){{.app-shell{{grid-template-columns:1fr}}.sidebar{{position:static;height:auto;display:flex;overflow:auto;padding:8px}}.sidebar .menu-title{{display:none}}.sidebar a{{white-space:nowrap}}.grid{{grid-template-columns:1fr}}.tag{{display:none}}}}
    .account{{margin-left:auto;display:flex;align-items:center;gap:12px;font-size:13px;font-weight:700}}.account a{{color:#b33b08;text-decoration:none}}
    </style></head><body><header><div class="brand"><b>1</b>Clik</div><div class="tag">Mensagens independentes do CRM</div><div class="account"><span>{html.escape(username)}</span><a href="/logout">Sair</a></div></header><div class="app-shell"><aside class="sidebar"><div class="menu-title">Menu</div><a data-nav="inicio" href="/#inicio">Painel</a><a data-nav="envios" href="/#envios">Envios e campanhas</a><a data-nav="conversas" href="/#conversas">Conversas respondidas</a><a data-nav="conexoes" href="/#conexoes">ConexÃµes</a><a data-nav="modelos" href="/#modelos">Modelos e templates</a></aside><main>{content}</main></div></body></html>"""


def dashboard(notice="", error=False, username=""):
    with db() as connection:
        history = connection.execute("""
            SELECT m.id,m.telefone AS phone,m.mensagem AS message,m.status,m.direction,m.created_at,
                   GROUP_CONCAT(DISTINCT c.nome) AS campaign_names
              FROM whatsapp_messages m
              LEFT JOIN whatsapp_campaign_items i
                ON replace(replace(replace(m.telefone,' ',''),'-',''),'(','') = replace(replace(replace(i.telefone,' ',''),'-',''),'(','')
              LEFT JOIN whatsapp_campaigns c ON c.id=i.campaign_id AND m.created_at >= c.created_at
             GROUP BY m.id
             ORDER BY m.id DESC LIMIT 80
        """).fetchall()
        channels = connection.execute("SELECT * FROM whatsapp_channels ORDER BY active DESC,id").fetchall()
        templates = connection.execute("SELECT * FROM whatsapp_templates WHERE active=1 ORDER BY nome").fetchall()
        official_templates = connection.execute("""SELECT t.*,c.nome AS channel_name FROM meta_official_templates t
            JOIN whatsapp_channels c ON c.id=t.channel_id ORDER BY t.synced_at DESC,t.name,t.language""").fetchall()
        meta_logs = connection.execute("SELECT * FROM meta_audit_logs ORDER BY id DESC LIMIT 30").fetchall()
        send_attempts = connection.execute("""SELECT a.*,c.nome AS campaign_name,t.name AS template_name
            FROM meta_campaign_send_attempts a JOIN whatsapp_campaigns c ON c.id=a.campaign_id
            JOIN meta_official_templates t ON t.id=a.template_id ORDER BY a.id DESC LIMIT 80""").fetchall()
        campaigns = connection.execute("""
            SELECT c.*,
                   COUNT(DISTINCT CASE WHEN lower(m.direction) IN ('in','inbound') THEN m.id END) AS respondidos,
                   (SELECT COUNT(*) FROM whatsapp_campaign_items pi WHERE pi.campaign_id=c.id AND pi.status IN ('pendente','processando')) AS pendentes_reais,
                   (SELECT COUNT(*) FROM whatsapp_campaign_items fi WHERE fi.campaign_id=c.id AND fi.status IN ('falhou','ignorado')) AS erros_reais,
                   (SELECT COUNT(*) FROM whatsapp_campaign_items si WHERE si.campaign_id=c.id AND si.status='enviado') AS enviados_reais,
                   (SELECT nome FROM whatsapp_campaign_items ni WHERE ni.campaign_id=c.id ORDER BY ni.id LIMIT 1) AS first_item_nome
              FROM whatsapp_campaigns c
              LEFT JOIN whatsapp_campaign_items i ON i.campaign_id=c.id
              LEFT JOIN whatsapp_messages m
                ON replace(replace(replace(m.telefone,' ',''),'-',''),'(','') = replace(replace(replace(i.telefone,' ',''),'-',''),'(','')
               AND m.created_at >= c.created_at
             GROUP BY c.id
             ORDER BY c.id DESC LIMIT 30
        """).fetchall()
        inbound_message_count = connection.execute(
            "SELECT COUNT(*) FROM whatsapp_messages WHERE lower(direction) IN ('in','inbound')"
        ).fetchone()[0]
        setting = connection.execute("SELECT setting_value FROM app_settings WHERE setting_key='internal_api_key'").fetchone()
        internal_api_key = setting["setting_value"] if setting else ""
    channel_states = {}
    for row in channels:
        if row["mode"] == "web":
            channel_states[row["id"]] = status(
                # The WhatsApp tab may already be authenticated while Chrome is
                # still finishing its initial load.  A 350 ms probe caused the
                # dashboard to show a false red/offline state even with the chat
                # list visible.  Give the isolated CDP session enough time to
                # confirm the authenticated UI.
                timeout=2.0,
                cdp_port=row["web_cdp_port"],
                profile_dir=row["web_profile_dir"],
            )
        elif row["mode"] == "meta":
            channel_states[row["id"]] = {"connected": bool(row["active"] and row["meta_connection_status"] == "validada")}
        else:
            channel_states[row["id"]] = {"connected": False}
    total = sum(int(row["total"] or 0) for row in campaigns)
    sent_count = sum(int(row["enviados"] or 0) for row in campaigns)
    failures = sum(int(row["falhas"] or 0) for row in campaigns)
    delivered = max(0, sent_count - failures)
    responded_count = int(inbound_message_count or 0)
    approved_template_count = sum(1 for row in official_templates if str(row["status"] or "").upper() == "APPROVED")
    template_options = '<option value="">Mensagem livre</option>' + "".join(f'<option value="{row["id"]}">{html.escape(row["nome"])}</option>' for row in templates)
    official_template_options = "".join(f'<option value="{row["id"]}" data-body="{html.escape(row["body_text"] or "", quote=True)}" data-buttons="{html.escape(row["buttons_json"] or "[]", quote=True)}">{html.escape(row["channel_name"])} â€” {html.escape(row["name"])} ({html.escape(row["language"])})</option>' for row in official_templates if str(row["status"]).upper() == "APPROVED")
    active_channels = [row for row in channels if row["active"]]
    channel_options = "".join(f'<option value="{row["id"]}">{html.escape(row["nome"])} ({html.escape(row["mode"])})</option>' for row in active_channels) or '<option value="">ConexÃ£o independente</option>'
    channel_selector_cards = "".join(
        f'<label class="channel-choice" data-channel-mode="{html.escape(row["mode"])}"><input type="checkbox" value="{row["id"]}"><span><b>{html.escape(row["nome"])}</b><small>{html.escape(row["telefone"] or "Sem nÃºmero")} Â· {"API Oficial" if row["mode"] == "meta" else "QR Code" if row["mode"] == "web" else "Manual"}</small></span></label>'
        for row in active_channels if row["mode"] in ("meta", "web")
    ) or '<p class="muted">Cadastre uma conexÃ£o antes de criar a campanha.</p>'
    meta_channel_options = "".join(f'<option value="{row["id"]}">{html.escape(row["nome"])} â€” {html.escape(row["telefone"] or "")}</option>' for row in active_channels if row["mode"] == "meta")
    connection_cards = "".join(
        f"""<article class="connection-card" data-conn-row data-search="{html.escape((row['nome'] or '')+' '+(row['telefone'] or '')+' '+row['mode'])}">
        <div class="connection-title"><span class="status-dot {'online' if channel_states[row['id']].get('connected') else 'offline'}"></span><div><b>{html.escape(row['nome'])}</b><small>{'Ativo' if channel_states[row['id']].get('connected') else 'NÃ£o ativo'}</small></div></div>
        <div class="connection-number">{html.escape(row['telefone'] or 'NÃºmero nÃ£o informado')}</div>
        <div class="muted">{'Sem API Oficial â€” QR Code' if row['mode']=='web' else 'API Oficial â€” Meta' if row['mode']=='meta' else 'Manual'} Â· RodÃ­zio: {'Sim' if row['rotation_enabled'] else 'NÃ£o'}</div>
        {f'<form method="post" action="/channel/connect"><input type="hidden" name="channel_id" value="{row["id"]}"><button>{"Reconectar QR Code" if channel_states[row["id"]].get("connected") else "Conectar QR Code"}</button></form>' if row['mode']=='web' else ''}
        </article>"""
        for row in channels
    ) or '<p class="muted">Nenhum WhatsApp cadastrado. Clique em â€œConectar via QR Codeâ€ para adicionar o primeiro.</p>'
    official_template_rows = "".join(f"<tr><td>{html.escape(row['channel_name'])}</td><td>{html.escape(row['name'])}</td><td>{html.escape(row['language'])}</td><td>{html.escape(row['category'] or 'â€”')}</td><td>{html.escape(row['status'])}</td><td>{html.escape((row['body_text'] or '')[:180])}</td><td>{html.escape(row['synced_at'])}</td></tr>" for row in official_templates)
    meta_log_rows = "".join(f"<tr><td>{html.escape(row['created_at'])}</td><td>{html.escape(row['action'])}</td><td>{html.escape(row['status'])}</td><td>{html.escape(row['message'] or '')}</td></tr>" for row in meta_logs)
    send_attempt_rows = "".join(f"<tr><td>{html.escape(row['campaign_name'])}</td><td>{html.escape(row['original_phone'] or 'â€”')}</td><td>{html.escape(row['template_name'])}</td><td>{html.escape(row['status'])}</td><td>{row['attempt_count']}</td><td>{row['http_status'] or 'â€”'}</td><td>{html.escape(row['wamid'] or 'â€”')}</td><td>{html.escape(row['error_message'] or '')}</td></tr>" for row in send_attempts)
    template_rows = "".join(f"<tr><td>{html.escape(row['nome'])}</td><td>{html.escape((row['mensagem'] or '')[:150])}</td></tr>" for row in templates)
    def campaign_controls(row):
        status_value = str(row["status"] or "").lower()
        actions = []
        if status_value in ("rascunho", "agendada", "importada"):
            actions.append(("start", "Iniciar"))
        if status_value == "em processamento":
            actions.append(("pause", "Pausar"))
        if status_value == "pausada":
            actions.append(("continue", "Continuar"))
        if status_value in ("rascunho", "agendada", "em processamento", "pausada"):
            actions.append(("cancel", "Cancelar"))
        controls = " ".join(f'<form method="post" action="/campaign/action" style="display:inline"><input type="hidden" name="campaign_id" value="{row["id"]}"><button name="action" value="{action}">{label}</button></form>' for action, label in actions)
        if row["send_mode"] == "web" and status_value not in ("cancelada", "concluÃ­da", "concluÃ­da com falhas"):
            media_label = "Trocar imagem/vÃ­deo" if row["media_path"] else "Adicionar imagem/vÃ­deo"
            controls += f''' <form method="post" action="/campaign/media" enctype="multipart/form-data" style="display:inline-flex;gap:6px;align-items:center;margin-top:7px"><input type="hidden" name="campaign_id" value="{row["id"]}"><input type="file" name="media" accept="image/*,video/*" required style="max-width:210px"><button>{media_label}</button></form>'''
        if status_value not in ("cancelada",):
            controls += f'''<details class="campaign-editor"><summary>Editar campanha</summary><form method="post" action="/campaign/edit" enctype="multipart/form-data"><input type="hidden" name="campaign_id" value="{row["id"]}"><label>Nome da campanha</label><input name="nome" value="{html.escape(row["nome"] or "", quote=True)}" required><label>Mensagem</label><textarea name="mensagem" required>{html.escape(row["mensagem"] or "")}</textarea><label>Tipo de interaÃ§Ã£o</label><select name="interaction_mode"><option value="numbered" {'selected' if (row['interaction_mode'] or 'numbered') == 'numbered' else ''}>OpÃ§Ãµes numeradas â€” cliente digita 1, 2 ou 3</option><option value="poll" {'selected' if row['interaction_mode'] == 'poll' else ''}>Enquete clicÃ¡vel do WhatsApp</option>{f'<option value="official" {"selected" if row["interaction_mode"] == "official" else ""}>BotÃµes clicÃ¡veis â€” API Oficial Meta</option>' if row['send_mode'] == 'meta' else ''}</select><label>Pergunta da enquete</label><input name="poll_question" value="{html.escape(row['poll_question'] or 'Como deseja continuar?', quote=True)}"><label>Intervalo entre envios (segundos)</label><input type="number" name="interval_seconds" min="1" value="{int(row["interval_seconds"] or 30)}"><label>Limite mÃ¡ximo de envios</label><input type="number" name="max_sends" min="0" value="{row["max_sends"] or ""}" placeholder="Todos"><label>Imagem ou vÃ­deo</label><input type="file" name="media" accept="image/*,video/*"><label>OpÃ§Ãµes de interaÃ§Ã£o (uma por linha)</label><textarea name="buttons">{html.escape(chr(10).join(json.loads(row["buttons_json"] or "[]")))}</textarea><button>Salvar alteraÃ§Ãµes</button></form></details>'''
        controls += f''' <form method="post" action="/campaign/delete" style="display:inline" onsubmit="return confirm('Excluir definitivamente a campanha {html.escape(row['nome'] or '', quote=True)} e toda a fila dela?');"><input type="hidden" name="campaign_id" value="{row['id']}"><button type="submit" style="background:#dc2626">Excluir campanha</button></form>'''
        return controls
    def campaign_row(row):
        total_items = max(0, int(row["total"] or 0))
        sent_items = int(row["enviados_reais"] or 0)
        error_items = int(row["erros_reais"] or 0)
        pending_items = int(row["pendentes_reais"] or 0)
        processed = sent_items + error_items
        percent = round((processed / total_items) * 100) if total_items else 0
        preview_text = str(row["preview_text"] or "")
        full_name = str(row["first_item_nome"] or "").strip()
        if full_name:
            preview_text = re.sub(re.escape(full_name), first_name(full_name), preview_text, flags=re.IGNORECASE)
        return f"""<article class="campaign-card">
        <div class="campaign-identity"><h3>{html.escape(row['nome'])}</h3><span class="origin-tag">{html.escape(row['origem'] or 'Lista')}</span><p>â–£ {html.escape((row['created_at'] or '')[:10])} &nbsp; â—¯ {total_items} conversas</p></div>
        <div class="campaign-summary"><span data-campaign-status class="campaign-status status-{html.escape(str(row['status']).lower().replace(' ', '-'))}">{html.escape(row['status'])}</span><p>{html.escape(preview_text[:220])}</p><div class="campaign-actions">{campaign_controls(row)}</div></div>
        <div class="campaign-progress"><div class="progress-ring" style="--progress:{percent}"><strong>{percent}%</strong></div><small>{processed} de {total_items}<br>processados</small></div>
        <div class="campaign-stats"><div class="stat-box sent"><span>âž¤</span><b>{sent_items}</b><small>enviados</small></div><div class="stat-box errors"><span>âš </span><b>{error_items}</b><small>erros</small></div><div class="stat-box pending"><span>â—·</span><b>{pending_items}</b><small>pendentes</small></div><div class="stat-box replies"><span>â—‹</span><b>{row['respondidos']}</b><small>respondidos</small></div></div>
        </article>"""
    campaign_rows = "".join(campaign_row(row) for row in campaigns)
    groups = {"novo": [], "aguardando": [], "respondido": [], "erro": []}
    for row in history:
        key = "erro" if "erro" in (row["status"] or "").lower() or "falha" in (row["status"] or "").lower() else "respondido" if (row["direction"] or "").lower() in ("in", "inbound") else "aguardando" if row["status"] in ("enviado", "enviado_web") else "novo"
        groups[key].append(row)
    def track_column(title, items):
        cards = "".join(f'<div class="track-card" data-track="{html.escape((row["phone"] or "")+" "+(row["message"] or "")+" "+(row["campaign_names"] or ""))}"><b>{html.escape(row["phone"] or "Contato")}</b><span>{html.escape(row["campaign_names"] or "Sem campanha vinculada")} Â· {html.escape((row["created_at"] or "")[0:16])}</span><p>{html.escape((row["message"] or "")[:100])}</p></div>' for row in items)
        return f'<div class="track-col"><h3>{title} <i>{len(items)}</i></h3>{cards or "<p class=muted>Sem registros.</p>"}</div>'
    inbound_track = track_column("Respostas recebidas", groups["respondido"])
    notice_html = f"<div class=\"notice{' error' if error else ''}\">{html.escape(notice)}</div>" if notice else ""
    content = f"""<style>
    [hidden]{{display:none!important}}
    .channel-picker{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;margin-top:9px}}.channel-choice{{display:flex;gap:10px;align-items:center;margin:0;padding:11px;border:1px solid var(--line);border-radius:10px;background:#fff;cursor:pointer}}.channel-choice:has(input:checked){{border-color:var(--orange);background:var(--orange-soft)}}.channel-choice input{{width:auto}}.channel-choice span,.channel-choice small{{display:block}}.channel-choice small{{color:var(--muted);margin-top:3px}}.button-builder{{display:grid;gap:8px}}.preview-side{{position:sticky;top:95px;align-self:start}}.phone-preview{{max-width:390px;margin:auto;border:9px solid #1f2937;border-radius:28px;overflow:hidden;background:#efeae2;box-shadow:0 18px 35px #0f172a22}}.phone-top{{background:#075e54;color:#fff;padding:17px;font-weight:800}}.chat-bg{{min-height:390px;padding:28px 14px;background:linear-gradient(#efeae2cc,#efeae2cc),repeating-linear-gradient(45deg,#ddd 0 1px,transparent 1px 14px)}}.message-bubble{{margin-left:auto;max-width:88%;background:#d9fdd3;border-radius:10px 2px 10px 10px;padding:12px;white-space:pre-wrap;box-shadow:0 1px 2px #0002}}.message-bubble small{{display:block;text-align:right;color:#667781;margin-top:5px}}.preview-buttons{{margin-left:auto;max-width:88%}}.preview-button{{background:#fff;color:#027eb5;text-align:center;padding:11px;border-top:1px solid #e5e7eb;font-weight:700}}@media(max-width:900px){{.channel-picker{{grid-template-columns:1fr}}.preview-side{{position:static}}}}
    .page-head{{display:flex;justify-content:space-between;align-items:center;margin-bottom:22px}}.page-head h1{{font-size:32px;margin:0;color:var(--orange)}}.metrics{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}}.metric{{border-top:3px solid #fed7aa}}.metric strong{{display:block;font-size:32px;margin-top:8px}}.section{{margin-top:22px}}.section-head{{display:flex;align-items:center;justify-content:space-between;gap:14px}}.search{{max-width:420px}}.wide{{overflow:auto}}.wide table{{min-width:900px}}.campaign-grid{{display:grid;grid-template-columns:1.2fr .8fr;gap:20px}}.track-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}.track-col{{background:#f8f9fa;border:1px solid var(--line);border-radius:12px;padding:14px;max-height:430px;overflow:auto}}.track-col h3{{margin-top:0}}.track-col i{{float:right;background:var(--orange-soft);color:#a6450c;border-radius:99px;padding:3px 9px;font-style:normal}}.track-card{{background:#fff;border:1px solid #e2e6ea;border-radius:9px;padding:11px;margin:9px 0;box-shadow:0 3px 8px #1f293708}}.track-card span{{display:block;color:var(--muted);font-size:12px}}.track-card p{{margin-bottom:0}}@media(max-width:900px){{.metrics,.track-grid{{grid-template-columns:1fr 1fr}}.campaign-grid{{grid-template-columns:1fr}}}}
    .connection-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px;margin:18px 0}}.connection-card{{border:1px solid var(--line);border-radius:14px;padding:16px;background:#fff;box-shadow:0 6px 18px #0f172a0d}}.connection-title{{display:flex;align-items:center;gap:10px}}.connection-title small{{display:block;color:var(--muted);margin-top:3px}}.status-dot{{width:14px;height:14px;border-radius:50%;display:inline-block;box-shadow:0 0 0 4px #e5e7eb}}.status-dot.online{{background:#22c55e;box-shadow:0 0 0 4px #dcfce7}}.status-dot.offline{{background:#ef4444;box-shadow:0 0 0 4px #fee2e2}}.connection-number{{font-size:18px;font-weight:800;margin:14px 0 6px}}.connection-card form{{margin-top:14px}}
    .campaign-list{{display:grid;gap:16px;margin-top:18px;max-height:650px;overflow-y:auto;overscroll-behavior:contain;padding:0 8px 8px 0;scrollbar-gutter:stable}}.campaign-list::-webkit-scrollbar{{width:10px}}.campaign-list::-webkit-scrollbar-track{{background:#f1f5f9;border-radius:10px}}.campaign-list::-webkit-scrollbar-thumb{{background:#f97316;border-radius:10px;border:2px solid #f1f5f9}}.campaign-columns{{display:grid;grid-template-columns:1.05fr 1.55fr .55fr 1.25fr;gap:18px;padding:0 14px 10px;color:var(--muted);font-size:12px;font-weight:800;border-bottom:1px solid var(--line)}}.campaign-columns span:last-child{{text-align:center}}.campaign-card{{display:grid;grid-template-columns:1.05fr 1.55fr .55fr 1.25fr;gap:18px;align-items:center;border:1px solid var(--line);border-radius:13px;padding:20px 14px;background:#fff;box-shadow:0 4px 14px #0f172a08}}.campaign-identity h3{{margin:0 0 7px;font-size:17px}}.campaign-identity p{{color:var(--muted);font-size:13px;margin:12px 0 0}}.origin-tag,.campaign-status{{display:inline-block;border-radius:6px;padding:5px 9px;background:#f1f5f9;color:#475569;font-size:12px;font-weight:800}}.campaign-status{{background:#e8eefc;color:#334e8c}}.campaign-summary>p{{line-height:1.65;margin:10px 0 12px}}.campaign-actions form{{margin:4px 5px 4px 0!important}}.campaign-actions button{{padding:9px 13px}}.campaign-progress{{text-align:center}}.progress-ring{{--value:calc(var(--progress)*1%);width:78px;height:78px;margin:0 auto 8px;border-radius:50%;display:grid;place-items:center;background:radial-gradient(circle,#fff 57%,transparent 59%),conic-gradient(var(--orange) var(--value),#e5e7eb 0)}}.progress-ring strong{{font-size:20px}}.campaign-progress small,.stat-box small{{display:block;color:var(--muted);margin-top:4px}}.campaign-stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}}.stat-box{{min-height:95px;border:1px solid var(--line);border-radius:9px;display:flex;flex-direction:column;align-items:center;justify-content:center;background:#fff}}.stat-box span{{font-size:18px}}.stat-box b{{font-size:18px;margin-top:4px}}.stat-box.sent{{color:#16a34a}}.stat-box.errors{{color:#ef4444}}.stat-box.pending{{color:#f59e0b}}.stat-box.replies{{color:#2563eb}}@media(max-width:1100px){{.campaign-columns{{display:none}}.campaign-card{{grid-template-columns:1fr 1.5fr}}.campaign-progress{{grid-column:1}}.campaign-stats{{grid-column:2}}}}@media(max-width:700px){{.campaign-card{{grid-template-columns:1fr}}.campaign-progress,.campaign-stats{{grid-column:auto}}}}
    .campaign-editor{{margin-top:9px;max-width:520px}}.campaign-editor summary{{cursor:pointer;color:#c2410c;font-weight:800}}.campaign-editor form{{display:grid;gap:6px;margin-top:8px;padding:12px;background:#fff7ed;border:1px solid #fed7aa;border-radius:10px}}.campaign-editor label{{margin:3px 0 0}}.campaign-editor textarea{{min-height:90px}}
    .campaign-media-preview{{display:block;width:calc(100% + 24px);max-height:280px;object-fit:cover;margin:-12px -12px 10px;border-radius:10px 2px 0 0;background:#111}}
    .insight-list{{display:grid;gap:10px;margin-top:18px}}.insight-campaign{{background:#fff;border:1px solid var(--line);border-left:6px solid #f97316;border-radius:13px;overflow:hidden;box-shadow:0 7px 20px #0f172a0a}}.insight-campaign.awaiting-reply{{border-left-color:#f97316}}.insight-campaign.replied{{border-left-color:#16a34a}}.insight-campaign.handled{{border-left-color:#8b5cf6}}.insight-head{{width:100%;display:grid;grid-template-columns:minmax(240px,1.6fr) 1fr .7fr auto;gap:14px;align-items:center;margin:0;padding:16px;background:#fff;color:var(--text);box-shadow:none;text-align:left}}.insight-head:hover{{background:#fffaf5}}.insight-head strong,.insight-head small{{display:block}}.insight-head small{{color:var(--muted);margin-top:4px}}.insight-chevron{{font-size:20px;color:var(--orange);transition:.2s}}.insight-campaign.open .insight-chevron{{transform:rotate(180deg)}}.insight-body{{padding:0 16px 16px;background:#fffaf7}}.insight-metrics{{display:grid;grid-template-columns:repeat(7,minmax(125px,1fr));gap:9px;padding-top:15px;overflow:auto}}.insight-metric{{min-width:125px;margin:0;padding:13px;text-align:left;background:#fff;color:var(--metric);border:1px solid color-mix(in srgb,var(--metric) 25%,#e5e7eb);box-shadow:none}}.insight-metric:hover{{background:#fff}}.insight-metric span{{font-size:20px}}.insight-metric b{{display:block;font-size:22px;margin:5px 0}}.insight-metric small{{color:var(--muted)}}.mini-progress{{height:5px;border-radius:9px;background:#e5e7eb;margin-top:9px;overflow:hidden}}.mini-progress i{{display:block;height:100%;background:var(--metric)}}.insight-results{{margin-top:13px;padding:14px;background:#fff;border:1px solid var(--line);border-radius:10px;max-height:390px;overflow:auto}}.insight-results table{{min-width:720px}}.opportunity-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:13px;margin-top:18px}}.opportunity-card{{margin:0;background:#fff;color:var(--text);border:1px solid var(--line);border-top:4px solid var(--orange);box-shadow:0 8px 22px #0f172a0a;text-align:left}}.opportunity-card:hover{{background:#fffaf7}}.opportunity-card h3{{margin:0 0 12px;color:#0b2340}}.opportunity-numbers{{display:grid;grid-template-columns:1fr 1fr;gap:9px}}.opportunity-numbers span{{font-size:12px;color:var(--muted)}}.opportunity-numbers b{{display:block;font-size:18px;color:var(--text)}}.opportunity-card time{{display:block;color:var(--muted);font-size:12px;margin-top:12px}}.opportunity-results{{margin-top:16px;overflow:auto}}@media(max-width:1050px){{.opportunity-grid{{grid-template-columns:repeat(2,1fr)}}.insight-head{{grid-template-columns:1fr 1fr auto}}.insight-head .insight-origin{{display:none}}}}@media(max-width:650px){{.opportunity-grid{{grid-template-columns:1fr}}.insight-head{{grid-template-columns:1fr auto}}.insight-head .insight-status{{display:none}}}}
    .official-history-scroll{{max-height:360px;overflow:auto;border:1px solid var(--line);border-radius:10px}}.official-history-scroll table{{min-width:900px}}.official-history-scroll th{{position:sticky;top:0;z-index:1;background:#fff}}
    #preview-media img,#preview-media video{{display:block;width:100%;max-height:250px;object-fit:cover;border-radius:8px;margin-bottom:10px;background:#111}}
    </style>{notice_html}<div data-view="inicio" id="inicio" class="page-head"><div><h1>Painel do 1Clik</h1><p class="muted">Resumo geral da operaÃ§Ã£o oficial e do WhatsApp Web.</p></div></div>
    <section data-view="inicio" class="metrics"><div class="card metric"><span>Campanhas</span><strong>{len(campaigns)}</strong></div><div class="card metric"><span>Contatos processados</span><strong>{total}</strong></div><div class="card metric"><span>Disparos enviados</span><strong>{sent_count}</strong></div><div class="card metric"><span>Respostas recebidas</span><strong>{responded_count}</strong></div><div class="card metric"><span>Entregues</span><strong>{delivered}</strong></div><div class="card metric"><span>Erros</span><strong class="fail">{failures}</strong></div><div class="card metric"><span>ConexÃµes ativas</span><strong>{len(active_channels)}</strong></div><div class="card metric"><span>Modelos / aprovados</span><strong>{len(templates)} / {approved_template_count}</strong></div></section>
    <section data-view="envios" id="campanhas" class="card section"><div class="section-head"><div><h2>ðŸ“£ Campanhas ({len(campaigns)})</h2><span class="muted">Gerencie suas campanhas de mensagens</span></div><a class="button" href="#campaign-builder">ï¼‹ Adicionar campanha</a></div><div class="campaign-columns"><span>Campanha</span><span>Resumo e aÃ§Ãµes</span><span>Progresso</span><span>Resultados</span></div><div class="campaign-list">{campaign_rows or '<p class="muted">Nenhuma campanha importada.</p>'}</div></section>
    <section data-view="envios" class="card section"><h2>Fila e histÃ³rico de envios oficiais</h2><div class="wide official-history-scroll"><table><tr><th>Campanha</th><th>NÃºmero original</th><th>Template</th><th>Resultado</th><th>Tentativas</th><th>HTTP</th><th>WAMID</th><th>Erro</th></tr>{send_attempt_rows or '<tr><td colspan="8" class="muted">Nenhum envio oficial preparado.</td></tr>'}</table></div></section>
    <section data-view="conversas" id="conversas" class="section"><div class="section-head"><div><h2>Conversas respondidas</h2><span class="muted">As respostas avulsas aparecem abaixo; clique em uma campanha para acompanhar os resultados vinculados.</span></div><input id="conversation-search" class="search" placeholder="Pesquisar campanha ou cliente"></div><div class="track-grid" style="grid-template-columns:1fr;margin:16px 0">{inbound_track}</div><div id="conversation-campaigns" class="insight-list"><p class="muted">Carregando campanhas...</p></div></section>
    <section data-view="conversas" class="card section"><div class="section-head"><div><h2>ðŸ“µ NÃºmeros nÃ£o entregÃ¡veis</h2><span class="muted">ProteÃ§Ã£o automÃ¡tica antes da importaÃ§Ã£o: 131026 permanente e 131049 temporÃ¡rio.</span></div><input id="suppression-search" class="search" placeholder="Pesquisar nÃºmero, cÃ³digo ou motivo"></div><div id="suppression-list" class="wide"><p class="muted">Carregando lista...</p></div></section>
    <section data-view="conexoes" id="conexoes" class="card section"><div class="section-head"><h2>WhatsApps conectados ({len(channels)})</h2><div style="display:flex;gap:10px;align-items:center"><input id="conn-search" class="search" placeholder="Pesquisar conexÃ£o"><a class="button" href="#nova-conexao">Conectar via QR Code</a></div></div><div class="connection-grid">{connection_cards}</div>
    <h3 id="nova-conexao">Adicionar conexÃ£o</h3><form method="post" action="/channel" class="grid" autocomplete="off"><div><label>Atualizar canal oficial existente (opcional)</label><select name="existing_meta_channel_id"><option value="0">Criar nova conexÃ£o</option>{meta_channel_options}</select><label>Nome da conexÃ£o</label><input name="nome" value="WhatsApp principal" autocomplete="off" required></div><div><label>NÃºmero do WhatsApp com DDD</label><input name="telefone" type="tel" inputmode="numeric" placeholder="Ex.: 91 999999999" autocomplete="off" required></div><div><label>Tipo de conexÃ£o</label><select id="channel-mode" name="mode"><option value="web">Sem API Oficial â€” QR Code</option><option value="meta">Com API Oficial â€” Meta</option><option value="manual">Manual</option></select></div><div id="meta-token-wrap" hidden><label>Access Token permanente</label><input type="password" name="meta_access_token" autocomplete="new-password"><label>Phone Number ID</label><input name="meta_phone_number_id"><label>WABA ID</label><input name="meta_waba_id"><label>App Secret (protegido)</label><input type="password" name="meta_app_secret" autocomplete="new-password"><label>VersÃ£o da Graph API</label><input name="meta_api_version" value="{html.escape(DEFAULT_GRAPH_API_VERSION)}"></div><p><button id="test-meta-connection" name="channel_action" value="test" class="secondary" hidden>Testar API Meta</button><button id="save-channel" name="channel_action" value="save">Salvar e abrir QR Code</button></p></form><div class="notice"><b>API Key interna do 1Clik</b><br><code>{html.escape(mask_secret(internal_api_key))}</code><br><span class="muted">Usada pela integraÃ§Ã£o interna. NÃ£o substitui o Access Token da Meta.</span></div></section>
    <section data-view="modelos" id="meta-template-builder" class="card section"><div class="section-head"><div><h2>Criar template oficial</h2><span class="muted">Crie no 1Clik, confira a visualizaÃ§Ã£o e envie para anÃ¡lise da Meta.</span></div></div><form id="meta-template-form" method="post" action="/meta/templates/create" class="campaign-grid"><div><label>Canal oficial</label><select name="channel_id" required><option value="">Selecione o canal</option>{meta_channel_options}</select><label>Nome interno do template</label><input name="template_name" pattern="[a-z0-9_]+" maxlength="512" placeholder="Ex.: omega_maraba_consulta_v2" required><p class="muted">Somente letras minÃºsculas, nÃºmeros e sublinhado.</p><label>Categoria</label><select name="category"><option value="MARKETING">Marketing</option><option value="UTILITY">Utilidade</option></select><input type="hidden" name="language" value="pt_BR"><label>Mensagem</label><textarea id="meta-template-body" name="body" maxlength="1024" placeholder="OlÃ¡, {{{{1}}}}! Digite sua mensagem..." required></textarea><p class="muted">Na API Oficial, use {{{{1}}}}, {{{{2}}}}, {{{{3}}}}. A primeira variÃ¡vel pode receber o primeiro nome do cliente.</p><label>Exemplos das variÃ¡veis</label><textarea id="meta-template-examples" name="example_values" placeholder="Adrian&#10;R$ 500,00"></textarea><p class="muted">Um exemplo por linha, na mesma ordem das variÃ¡veis. Esses exemplos servem apenas para a anÃ¡lise da Meta.</p><label>BotÃµes de resposta rÃ¡pida</label><div class="button-builder"><input class="meta-button-label" name="button_1" maxlength="25" placeholder="Ex.: Sim, tenho interesse"><input class="meta-button-label" name="button_2" maxlength="25" placeholder="Ex.: NÃ£o, obrigada"><input class="meta-button-label" name="button_3" maxlength="25" placeholder="Ex.: Bloquear contato"></div><label class="channel-choice" style="margin-top:16px"><input type="checkbox" name="confirm_submission" value="yes" required><span><b>Enviar este modelo para anÃ¡lise da Meta</b><small>Isso cria o template, mas nÃ£o envia nenhuma mensagem a clientes.</small></span></label><button type="submit">Criar e enviar para a Meta</button></div><aside class="preview-side"><h2>VisualizaÃ§Ã£o do template</h2><div class="phone-preview"><div class="phone-top">WhatsApp</div><div class="chat-bg"><div class="message-bubble"><div id="meta-preview-message">Seu template aparecerÃ¡ aqui.</div><small>prÃ©via</small></div><div id="meta-preview-buttons" class="preview-buttons"></div></div></div><p id="meta-template-counter" class="muted">0 de 1.024 caracteres</p></aside></form></section>
    <section data-view="modelos" class="card section"><div class="section-head"><h2>Templates oficiais â€” Meta</h2><form method="post" action="/meta/templates/sync"><select name="channel_id" required><option value="">Canal oficial</option>{meta_channel_options}</select><button>Sincronizar templates da Meta</button></form></div><p class="muted">PENDING estÃ¡ em anÃ¡lise. Somente APPROVED fica disponÃ­vel para campanhas.</p><div class="wide"><table><tr><th>Canal</th><th>Template</th><th>Idioma</th><th>Categoria</th><th>Status</th><th>Corpo</th><th>Ãšltima sincronizaÃ§Ã£o</th></tr>{official_template_rows or '<tr><td colspan="7" class="muted">Nenhum template oficial sincronizado.</td></tr>'}</table></div></section>
    <section data-view="envios" id="campaign-builder" class="card section"><div class="section-head"><h2>Criar campanha</h2><span class="muted">Monte a mensagem e confira como o cliente receberÃ¡</span></div><form id="campaign-form" method="post" action="/campaign" enctype="multipart/form-data" class="campaign-grid"><div><label>Nome da campanha</label><input name="nome" value="Campanha Omega" required><label>Forma de envio</label><select id="campaign-send-mode" name="send_mode"><option value="web">Sem API Oficial â€” QR Code</option><option value="meta">Com API Oficial â€” Meta</option></select><label>Uso dos nÃºmeros</label><select id="channel-strategy" name="channel_strategy"><option value="single">Usar um nÃºmero especÃ­fico</option><option value="rotation">RodÃ­zio entre vÃ¡rios nÃºmeros</option></select><input id="channel-ids-json" type="hidden" name="channel_ids_json" value="[]"><div id="channel-picker" class="channel-picker">{channel_selector_cards}</div><p id="channel-help" class="muted">Escolha um nÃºmero para esta campanha.</p><label>Template oficial aprovado</label><select id="official-template" name="official_template_id"><option value="0">Usar mensagem criada aqui / QR Code</option>{official_template_options}</select><label>RelaÃ§Ã£o das variÃ¡veis com a planilha</label><textarea name="variable_map_json" placeholder='Ex.: {{"1":"nome","2":"valor","3":"atendente"}}'></textarea><p class="muted">Use o nÃºmero da variÃ¡vel e o nome exato da coluna da planilha.</p><label>Mensagem</label><textarea id="campaign-message" name="campaign_message" placeholder="OlÃ¡, {{nome}}! Digite sua mensagem..."></textarea><label>OpÃ§Ãµes de resposta</label><div class="button-builder"><input class="button-label" placeholder="Ex.: Quero sim"><input class="button-label" placeholder="Ex.: Falar com atendente"><input class="button-label" placeholder="Ex.: NÃ£o tenho interesse"></div><input id="buttons-json" type="hidden" name="buttons_json" value="[]"><p class="muted"><b>QR Code:</b> serÃ£o enviadas como opÃ§Ãµes numeradas para o cliente responder 1, 2 ou 3. <b>API Oficial:</b> os botÃµes clicÃ¡veis vÃªm do template aprovado pela Meta.</p><label>Imagem do modelo oficial ou mÃ­dia do QR Code</label><input id="campaign-media-file" type="file" name="media_file" accept="image/*,video/*"><p id="campaign-media-status" class="muted">Para modelo oficial com imagem, carregue aqui a mesma imagem aprovada na Meta.</p><label>Lista CSV ou XLSX</label><input type="file" name="file" accept=".csv,.xlsx" required><button>Importar e preparar campanha</button></div><aside class="preview-side"><h2>PrÃ©via para o cliente</h2><div class="phone-preview"><div class="phone-top">WhatsApp</div><div class="chat-bg"><div class="message-bubble"><div id="preview-media" hidden></div><div id="preview-message">Sua mensagem aparecerÃ¡ aqui.</div><small>agora âœ“âœ“</small></div><div id="preview-buttons" class="preview-buttons"></div></div></div><h2>ProgramaÃ§Ã£o</h2><label>Intervalo entre envios (segundos)</label><input type="number" name="interval_seconds" value="30" min="1"><label>Limite mÃ¡ximo de envios</label><input type="number" name="max_sends" placeholder="Todos os contatos"><label>Data e hora de inÃ­cio (opcional)</label><input type="datetime-local" name="scheduled_at"><p class="muted">A campanha serÃ¡ preparada como rascunho para conferÃªncia antes do inÃ­cio.</p></aside></form></section>
    <section data-view="envios" id="envio" class="card section"><h2>Envio individual â€” com ou sem API Oficial</h2>
      <form method="post" action="/send" enctype="multipart/form-data">
       <label>Telefone com DDD</label><input name="phone" inputmode="tel" placeholder="(91) 99999-9999" required>
       <label>ConexÃ£o</label><select name="channel_id">{channel_options}</select>
       <label>Modelo salvo</label><select name="template_id">{template_options}</select>
       <label>Mensagem</label><textarea name="message" placeholder="Digite a mensagem..."></textarea>
       <label>Imagem, vÃ­deo ou PDF (opcional)</label><input type="file" name="media" accept="image/*,video/*,.pdf">
       <button type="submit">Enviar mensagem</button></form></section>
    <section data-view="modelos" id="modelos" class="card section"><h2>Modelos de mensagem</h2><form method="post" action="/template"><label>Nome</label><input name="nome" required><label>Mensagem</label><textarea name="mensagem" placeholder="Use {{nome}}, {{cpf}}, {{matricula}}, {{convenio}}, {{margem}}" required></textarea><button>Salvar modelo</button></form><div class="wide"><table><tr><th>Nome</th><th>Mensagem</th></tr>{template_rows}</table></div></section>
    <section data-view="conexoes" class="card section"><h2>Auditoria da API Oficial</h2><div class="wide"><table><tr><th>Data</th><th>AÃ§Ã£o</th><th>Resultado</th><th>Mensagem</th></tr>{meta_log_rows or '<tr><td colspan="4" class="muted">Nenhum evento tÃ©cnico registrado.</td></tr>'}</table></div></section>
    <script>document.getElementById('conn-search')?.addEventListener('input',e=>{{const q=e.target.value.toLowerCase();document.querySelectorAll('[data-conn-row]').forEach(r=>r.style.display=r.dataset.search.toLowerCase().includes(q)?'':'none')}});document.getElementById('track-search')?.addEventListener('input',e=>{{const q=e.target.value.toLowerCase();document.querySelectorAll('[data-track]').forEach(r=>r.style.display=r.dataset.track.toLowerCase().includes(q)?'':'none')}});const channelMode=document.getElementById('channel-mode'),metaWrap=document.getElementById('meta-token-wrap');channelMode?.addEventListener('change',()=>{{metaWrap.hidden=channelMode.value!=='meta'}});const campaignMode=document.getElementById('campaign-send-mode'),channelStrategy=document.getElementById('channel-strategy'),campaignMessage=document.getElementById('campaign-message'),officialTemplate=document.getElementById('official-template'),channelChoices=[...document.querySelectorAll('.channel-choice')],buttonInputs=[...document.querySelectorAll('.button-label')];function selectedChannels(){{return channelChoices.filter(x=>!x.hidden&&x.querySelector('input').checked).map(x=>x.querySelector('input').value)}}function syncChannels(){{channelChoices.forEach(choice=>{{choice.hidden=choice.dataset.channelMode!==campaignMode.value;if(choice.hidden)choice.querySelector('input').checked=false}});document.getElementById('official-template').closest('label')?.removeAttribute('hidden');officialTemplate.parentElement;document.getElementById('channel-help').textContent=channelStrategy.value==='rotation'?'Marque dois ou mais nÃºmeros para o rodÃ­zio.':'Escolha um nÃºmero especÃ­fico.';syncComposer()}}function syncComposer(){{let ids=selectedChannels();document.getElementById('channel-ids-json').value=JSON.stringify(ids);let message=campaignMessage?.value.trim()||'';let buttons=buttonInputs.map(x=>x.value.trim()).filter(Boolean);if(campaignMode?.value==='meta'&&officialTemplate?.selectedOptions[0]){{message=officialTemplate.selectedOptions[0].dataset.body||message;try{{const raw=JSON.parse(officialTemplate.selectedOptions[0].dataset.buttons||'[]');buttons=raw.map(x=>x.text||x.title).filter(Boolean)}}catch(e){{}}}}document.getElementById('preview-message').textContent=message||'Sua mensagem aparecerÃ¡ aqui.';document.getElementById('buttons-json').value=JSON.stringify(buttons);document.getElementById('preview-buttons').innerHTML=buttons.map(x=>'<div class="preview-button">'+String(x).replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]))+'</div>').join('')}}campaignMode?.addEventListener('change',syncChannels);channelStrategy?.addEventListener('change',()=>{{if(channelStrategy.value==='single')channelChoices.forEach((x,i)=>{{if(i&&x.querySelector('input').checked)x.querySelector('input').checked=false}});syncChannels()}});channelChoices.forEach(choice=>choice.querySelector('input').addEventListener('change',e=>{{if(channelStrategy.value==='single'&&e.target.checked)channelChoices.forEach(x=>{{if(x!==choice)x.querySelector('input').checked=false}});syncComposer()}}));campaignMessage?.addEventListener('input',syncComposer);officialTemplate?.addEventListener('change',syncComposer);buttonInputs.forEach(x=>x.addEventListener('input',syncComposer));document.getElementById('campaign-form')?.addEventListener('submit',e=>{{const ids=selectedChannels();if(!ids.length||(channelStrategy.value==='rotation'&&ids.length<2)){{e.preventDefault();alert(channelStrategy.value==='rotation'?'Escolha pelo menos dois nÃºmeros para o rodÃ­zio.':'Escolha o nÃºmero que enviarÃ¡ a campanha.');return}}syncComposer()}});function showView(){{const raw=(location.hash||'#inicio').slice(1);const view=['inicio','envios','conversas','conexoes','modelos'].includes(raw)?raw:'inicio';document.querySelectorAll('[data-view]').forEach(el=>el.hidden=el.dataset.view!==view);document.querySelectorAll('[data-nav]').forEach(el=>el.classList.toggle('active',el.dataset.nav===view));window.scrollTo(0,0)}}window.addEventListener('hashchange',showView);syncChannels();showView();</script>"""
    content += """<script>
    (() => {
      const newConnectionLink = document.querySelector('a[href="#nova-conexao"]');
      if (newConnectionLink) newConnectionLink.remove();
      const connectionName = document.querySelector('input[name="nome"]');
      if (connectionName && connectionName.value === 'WhatsApp principal') {
        connectionName.value = '';
        connectionName.placeholder = 'Ex.: ADRIAN LOJA';
      }
      const addCampaignButton = [...document.querySelectorAll('a.button')].find((item) => item.textContent.includes('Adicionar campanha'));
      addCampaignButton?.addEventListener('click', (event) => {
        event.preventDefault();
        if (location.hash !== '#envios') location.hash = 'envios';
        setTimeout(() => document.getElementById('campaign-builder')?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 80);
      });
      const campaignRunning = [...document.querySelectorAll('#campanhas [data-campaign-status]')].some((item) => item.textContent.trim().toLowerCase() === 'em processamento');
      if (campaignRunning) setInterval(() => location.reload(), 10000);
      const mode = document.getElementById('channel-mode');
      const meta = document.getElementById('meta-token-wrap');
      const test = document.getElementById('test-meta-connection');
      const save = document.getElementById('save-channel');
      const variableMap = document.querySelector('[name="variable_map_json"]');
      const variableLabel = variableMap?.previousElementSibling;
      const variableHelp = variableMap?.nextElementSibling;
      const responseHelp = document.getElementById('buttons-json')?.nextElementSibling;
      const metaTemplateBody = document.getElementById('meta-template-body');
      const metaTemplateExamples = document.getElementById('meta-template-examples');
      const metaTemplateButtons = [...document.querySelectorAll('.meta-button-label')];
      const escapeTemplateHtml = (value) => String(value).replace(/[&<>"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
      const syncMetaTemplatePreview = () => {
        let preview = metaTemplateBody?.value || '';
        const examples = (metaTemplateExamples?.value || '').split(/\r?\n/).map((value) => value.trim());
        preview = preview.replace(/\\{\\{(\\d+)\\}\\}/g, (match, position) => examples[Number(position) - 1] || match);
        const labels = metaTemplateButtons.map((input) => input.value.trim()).filter(Boolean);
        const previewText = document.getElementById('meta-preview-message');
        const previewButtons = document.getElementById('meta-preview-buttons');
        const counter = document.getElementById('meta-template-counter');
        if (previewText) previewText.textContent = preview.trim() || 'Seu template aparecerÃ¡ aqui.';
        if (previewButtons) previewButtons.innerHTML = labels.map((label) => '<div class="preview-button">' + escapeTemplateHtml(label) + '</div>').join('');
        if (counter) counter.textContent = `${metaTemplateBody?.value.length || 0} de 1.024 caracteres`;
      };
      metaTemplateBody?.addEventListener('input', syncMetaTemplatePreview);
      metaTemplateExamples?.addEventListener('input', syncMetaTemplatePreview);
      metaTemplateButtons.forEach((input) => input.addEventListener('input', syncMetaTemplatePreview));
      syncMetaTemplatePreview();
      const campaignMediaInput = document.querySelector('input[name="media_file"]');
      const previewMessageBox = document.getElementById('preview-message');
      let campaignMediaPreview = null;
      campaignMediaInput?.addEventListener('change', () => {
        if (campaignMediaPreview) {
          URL.revokeObjectURL(campaignMediaPreview.src);
          campaignMediaPreview.remove();
          campaignMediaPreview = null;
        }
        const file = campaignMediaInput.files?.[0];
        if (!file || !previewMessageBox) return;
        campaignMediaPreview = document.createElement(file.type.startsWith('video/') ? 'video' : 'img');
        campaignMediaPreview.className = 'campaign-media-preview';
        campaignMediaPreview.src = URL.createObjectURL(file);
        campaignMediaPreview.alt = 'Imagem selecionada para a campanha';
        if (campaignMediaPreview.tagName === 'VIDEO') campaignMediaPreview.controls = true;
        previewMessageBox.parentElement.insertBefore(campaignMediaPreview, previewMessageBox);
      });
      const responseLabel = [...document.querySelectorAll('#campaign-builder label')].find((item) => item.textContent.trim() === 'OpÃ§Ãµes de resposta');
      let interactionMode, pollQuestion, pollQuestionLabel, interactionModeLabel;
      if (responseLabel) {
        const modeLabel = document.createElement('label');
        modeLabel.textContent = 'Tipo de interaÃ§Ã£o';
        interactionMode = document.createElement('select');
        interactionMode.name = 'interaction_mode';
        interactionMode.id = 'interaction-mode';
        interactionMode.innerHTML = '<option value="numbered">OpÃ§Ãµes numeradas â€” cliente digita 1, 2 ou 3</option><option value="poll">Enquete clicÃ¡vel do WhatsApp</option><option value="official">BotÃµes clicÃ¡veis â€” API Oficial Meta</option>';
        pollQuestionLabel = document.createElement('label');
        pollQuestionLabel.textContent = 'Pergunta da enquete';
        pollQuestion = document.createElement('input');
        pollQuestion.name = 'poll_question';
        pollQuestion.value = 'Como deseja continuar?';
        responseLabel.parentElement.insertBefore(interactionModeLabel, responseLabel);
        responseLabel.parentElement.insertBefore(interactionMode, responseLabel);
        responseLabel.parentElement.insertBefore(pollQuestionLabel, responseLabel);
        responseLabel.parentElement.insertBefore(pollQuestion, responseLabel);
      }
      const syncCampaignExtras = () => {
        const official = document.getElementById('campaign-send-mode')?.value === 'meta';
        if (interactionMode && official && interactionMode.value !== 'official') interactionMode.value = 'official';
        const hasResponseOptions = [...document.querySelectorAll('.button-label')].some((input) => input.value.trim());
        if (interactionMode && !official) interactionMode.value = hasResponseOptions ? 'poll' : 'numbered';
        if (interactionMode) interactionMode.hidden = !official;
        if (interactionModeLabel) interactionModeLabel.hidden = !official;
        if (variableMap) variableMap.hidden = !official;
        if (variableLabel) variableLabel.hidden = !official;
        if (variableHelp) {
          variableHelp.hidden = !official;
          variableHelp.textContent = 'Somente API Oficial: relaciona {{1}}, {{2}} e {{3}} Ã s colunas da planilha.';
        }
        const poll = !official && interactionMode?.value === 'poll';
        if (pollQuestion) pollQuestion.hidden = !poll;
        if (pollQuestionLabel) pollQuestionLabel.hidden = !poll;
        if (responseHelp) responseHelp.textContent = poll
          ? 'A enquete serÃ¡ enviada como segunda mensagem, com opÃ§Ãµes clicÃ¡veis e apenas uma resposta permitida.'
          : official
            ? 'Os botÃµes clicÃ¡veis vÃªm do template aprovado pela Meta.'
            : 'As opÃ§Ãµes serÃ£o enviadas numeradas para o cliente responder 1, 2 ou 3.';
      };
      document.getElementById('campaign-send-mode')?.addEventListener('change', syncCampaignExtras);
      interactionMode?.addEventListener('change', syncCampaignExtras);
      document.querySelectorAll('.button-label').forEach((input) => input.addEventListener('input', syncCampaignExtras));
      syncCampaignExtras();
      const sync = () => {
        const isMeta = mode?.value === 'meta';
        if (meta) meta.hidden = !isMeta;
        if (test) test.hidden = !isMeta;
        if (save) save.textContent = isMeta ? 'Salvar conexÃ£o validada' : mode?.value === 'web' ? 'Salvar e abrir QR Code' : 'Salvar conexÃ£o';
      };
      mode?.addEventListener('change', sync);
      sync();
    })();
    </script>"""
    content += """<script>
    (() => {
      const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
      const money = (value) => Number(value || 0).toLocaleString('pt-BR', {style:'currency', currency:'BRL'});
      const labels = {
        sent:['ðŸ“¤','Enviados','#1685d1'], responded:['âœ…','Respondidas','#16a34a'],
        awaiting:['â³','Aguardando resposta','#f59e0b'], error:['âŒ','Erro','#dc2626'],
        blocked:['ðŸš«','Bloqueados','#7c3aed'], not_delivered:['ðŸ“µ','NÃ£o entregues','#64748b'],
        read:['ðŸ“–','Lidas','#0891b2']
      };
      const mediaInput = document.getElementById('campaign-media-file');
      const mediaPreview = document.getElementById('preview-media');
      const mediaStatus = document.getElementById('campaign-media-status');
      let mediaPreviewUrl = '';
      mediaInput?.addEventListener('change', () => {
        if (mediaPreviewUrl) URL.revokeObjectURL(mediaPreviewUrl);
        const file = mediaInput.files?.[0];
        if (!file) {
          mediaPreview.hidden = true;
          mediaPreview.innerHTML = '';
          mediaStatus.textContent = 'Nenhuma mÃ­dia selecionada.';
          return;
        }
        mediaPreviewUrl = URL.createObjectURL(file);
        const isVideo = file.type.startsWith('video/');
        mediaPreview.innerHTML = isVideo
          ? `<video src="${mediaPreviewUrl}" controls muted></video>`
          : `<img src="${mediaPreviewUrl}" alt="PrÃ©via da imagem">`;
        mediaPreview.hidden = false;
        mediaStatus.textContent = `${file.name} Â· ${(file.size / 1024 / 1024).toFixed(2)} MB carregado na prÃ©via.`;
      });
      let campaignsLoaded = false, opportunitiesLoaded = false, openCampaign = null, openOpportunity = null;
      const activateView = () => {
        const view = (location.hash || '#inicio').slice(1);
        if (view !== 'oportunidades') return;
        document.querySelectorAll('[data-view]').forEach(el => el.hidden = el.dataset.view !== view);
        document.querySelectorAll('[data-nav]').forEach(el => el.classList.toggle('active', el.dataset.nav === view));
        if (!opportunitiesLoaded) loadOpportunities();
      };
      window.addEventListener('hashchange', () => setTimeout(activateView, 0));
      setTimeout(activateView, 0);

      async function loadCampaigns() {
        const root = document.getElementById('conversation-campaigns');
        if (!root) return;
        try {
          const response = await fetch('/api/conversations/campaigns');
          const data = await response.json();
          const query = (document.getElementById('conversation-search')?.value || '').toLowerCase();
          const rows = data.campaigns.filter(c => `${c.nome} ${c.origem} ${c.status}`.toLowerCase().includes(query));
          root.innerHTML = rows.map(c => `<article class="insight-campaign ${escapeHtml(c.color)}" data-insight-campaign="${c.id}">
            <button class="insight-head" type="button" data-open-campaign="${c.id}">
              <span><strong>${escapeHtml(c.nome)}</strong><small>${escapeHtml((c.created_at || '').slice(0,16).replace('T',' '))} Â· ${c.total} contatos</small></span>
              <span class="insight-origin"><strong>${escapeHtml(c.origem || 'Lista')}</strong><small>Origem</small></span>
              <span class="insight-status"><strong>${escapeHtml(c.status || '')}</strong><small>Status</small></span><span class="insight-chevron">âŒ„</span>
            </button><div class="insight-body" hidden></div></article>`).join('') || '<p class="muted">Nenhuma campanha encontrada.</p>';
          campaignsLoaded = true;
        } catch (error) { root.innerHTML = '<div class="notice error">NÃ£o foi possÃ­vel carregar as campanhas.</div>'; }
      }
      function openCampaignPanel(id) {
        const card = document.querySelector(`[data-insight-campaign="${id}"]`);
        if (!card) return;
        document.querySelectorAll('.insight-campaign.open').forEach(other => {
          if (other !== card) { other.classList.remove('open'); other.querySelector('.insight-body').hidden = true; }
        });
        const body = card.querySelector('.insight-body'), opening = body.hidden;
        card.classList.toggle('open', opening); body.hidden = !opening; openCampaign = opening ? id : null;
        if (!opening || body.dataset.loaded) return;
        fetch('/api/conversations/campaigns').then(r => r.json()).then(data => {
          const campaign = data.campaigns.find(c => c.id === Number(id));
          body.innerHTML = `<div class="insight-metrics">${Object.entries(labels).map(([key, label]) => {
            const metric = campaign.metrics[key];
            return `<button type="button" class="insight-metric" style="--metric:${label[2]}" data-campaign-metric="${key}" data-campaign-id="${id}"><span>${label[0]}</span><b>${metric.count}</b><small>${label[1]} Â· ${metric.percent}%</small><div class="mini-progress"><i style="width:${Math.min(100,metric.percent)}%"></i></div></button>`;
          }).join('')}</div><div class="insight-results" hidden></div>`;
          body.dataset.loaded = '1';
        });
      }
      async function loadCampaignContacts(id, metric, button) {
        const body = button.closest('.insight-body'), results = body.querySelector('.insight-results');
        results.hidden = false; results.innerHTML = '<p class="muted">Carregando clientes...</p>';
        const search = document.getElementById('conversation-search')?.value || '';
        const data = await fetch(`/api/conversations/contacts?campaign_id=${id}&metric=${encodeURIComponent(metric)}&search=${encodeURIComponent(search)}`).then(r => r.json());
        results.innerHTML = `<div class="section-head"><h3>${labels[metric][0]} ${labels[metric][1]} (${data.total})</h3></div><div class="wide"><table><tr><th>Cliente cadastrado</th><th>Nome usado na mensagem</th><th>Telefone</th><th>Status</th><th>Ãšltima mensagem</th><th>Data</th><th>Meu atendimento</th></tr>${data.contacts.map(row => `<tr><td>${escapeHtml(row.nome || 'Cliente')}</td><td><b>${escapeHtml(row.name_used || 'â€”')}</b></td><td>${escapeHtml(row.telefone)}</td><td>${escapeHtml(row.message_status || row.status)}</td><td>${escapeHtml((row.message || row.last_error || '').slice(0,120))}</td><td>${escapeHtml((row.message_at || row.updated_at || '').slice(0,16).replace('T',' '))}</td><td><button type="button" class="${row.handled ? 'secondary' : 'primary'}" data-mark-handled="${row.id}" data-handled="${row.handled ? '1' : '0'}">${row.handled ? 'âœ“ Respondido por mim' : 'Marcar como respondido'}</button>${row.handled_at ? `<small class="muted">${escapeHtml(row.handled_at.slice(0,16).replace('T',' '))}</small>` : ''}</td></tr>`).join('') || '<tr><td colspan="7" class="muted">Nenhum cliente neste status.</td></tr>'}</table></div>`;
      }
      document.getElementById('conversation-campaigns')?.addEventListener('click', event => {
        const handled = event.target.closest('[data-mark-handled]');
        if (handled) {
          event.stopPropagation();
          const campaignId = handled.closest('[data-insight-campaign]').dataset.insightCampaign;
          fetch('/api/conversations/handled', {
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({item_id:Number(handled.dataset.markHandled),handled:handled.dataset.handled!=='1'})
          }).then(response => response.json()).then(data => {
            if (!data.ok) throw new Error(data.error || 'Falha ao atualizar.');
            loadCampaignContacts(campaignId, 'responded', handled).then(loadCampaigns);
          }).catch(error => alert(error.message));
          return;
        }
        const head = event.target.closest('[data-open-campaign]'); if (head) openCampaignPanel(head.dataset.openCampaign);
        const metric = event.target.closest('[data-campaign-metric]'); if (metric) loadCampaignContacts(metric.dataset.campaignId, metric.dataset.campaignMetric, metric);
      });
      let conversationTimer;
      document.getElementById('conversation-search')?.addEventListener('input', () => { clearTimeout(conversationTimer); conversationTimer=setTimeout(loadCampaigns,250); });
      loadCampaigns();

      async function loadSuppressions() {
        const root = document.getElementById('suppression-list');
        if (!root) return;
        const search = document.getElementById('suppression-search')?.value || '';
        try {
          const data = await fetch(`/api/suppressions?search=${encodeURIComponent(search)}`).then(r => r.json());
          root.innerHTML = `<table><tr><th>NÃºmero</th><th>Regra</th><th>CÃ³digo</th><th>Motivo</th><th>Ãšltima ocorrÃªncia</th><th>Bloqueado atÃ©</th><th>AÃ§Ã£o</th></tr>${data.numbers.map(row => `<tr><td><b>${escapeHtml(row.normalized_phone)}</b></td><td>${row.active ? (row.suppression_type === 'permanent' ? 'Permanente' : 'TemporÃ¡rio') : 'Reativado'}</td><td>${escapeHtml(row.error_code)}</td><td>${escapeHtml(row.reason)}</td><td>${escapeHtml((row.last_seen_at || '').slice(0,16).replace('T',' '))}</td><td>${escapeHtml((row.blocked_until || 'â€”').slice(0,16).replace('T',' '))}</td><td>${row.active ? `<button type="button" class="secondary" data-reactivate-phone="${escapeHtml(row.normalized_phone)}">Reativar</button>` : '<span class="muted">Liberado</span>'}</td></tr>`).join('') || '<tr><td colspan="7" class="muted">Nenhum nÃºmero registrado.</td></tr>'}</table>`;
        } catch (error) { root.innerHTML = '<div class="notice error">NÃ£o foi possÃ­vel carregar a lista.</div>'; }
      }
      document.getElementById('suppression-list')?.addEventListener('click', event => {
        const button = event.target.closest('[data-reactivate-phone]');
        if (!button) return;
        fetch('/api/suppressions/reactivate', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone:button.dataset.reactivatePhone})})
          .then(r => r.json()).then(data => { if (!data.ok) throw new Error(data.error || 'Falha ao reativar.'); loadSuppressions(); })
          .catch(error => alert(error.message));
      });
      let suppressionTimer;
      document.getElementById('suppression-search')?.addEventListener('input', () => { clearTimeout(suppressionTimer); suppressionTimer=setTimeout(loadSuppressions,250); });
      loadSuppressions();

      async function loadOpportunities() {
        const root = document.getElementById('opportunity-cards');
        try {
          const data = await fetch('/api/opportunities?limit=1').then(r => r.json());
          root.innerHTML = data.cards.map(card => `<button type="button" class="opportunity-card" data-opportunity="${escapeHtml(card.name)}"><h3>${escapeHtml(card.name)}</h3><div class="opportunity-numbers"><div><span>Consultas</span><b>${card.consultations}</b></div><div><span>Margem positiva</span><b>${card.positive}</b></div><div><span>Valor total</span><b>${money(card.total_margin)}</b></div><div><span>Valor mÃ©dio</span><b>${money(card.average_margin)}</b></div></div><time>Ãšltima atualizaÃ§Ã£o: ${escapeHtml((card.updated_at || 'Sem consultas').replace('T',' '))}</time></button>`).join('');
          opportunitiesLoaded = true;
        } catch (error) { root.innerHTML = '<div class="notice error">NÃ£o foi possÃ­vel acessar as margens do CRM.</div>'; }
      }
      async function loadOpportunityClients(category) {
        openOpportunity = category;
        const root = document.getElementById('opportunity-clients'), search = document.getElementById('opportunity-search')?.value || '';
        root.hidden = false; root.innerHTML = '<p class="muted">Carregando clientes...</p>';
        const data = await fetch(`/api/opportunities?category=${encodeURIComponent(category)}&search=${encodeURIComponent(search)}`).then(r => r.json());
        root.innerHTML = `<div class="section-head"><h3>${escapeHtml(category)} (${data.total})</h3><button type="button" class="secondary" data-close-opportunity>Fechar</button></div><div class="wide"><table><tr><th>Cliente</th><th>CPF</th><th>MatrÃ­cula</th><th>Margem</th><th>Status</th><th>Consulta</th></tr>${data.clients.map(row => `<tr><td>${escapeHtml(row.client_name || row.nome_cliente || 'Cliente')}</td><td>${escapeHtml(row.client_cpf || row.cpf_masked || '')}</td><td>${escapeHtml(row.matricula || '')}</td><td>${money(row.margin_value)}</td><td>${escapeHtml(row.status_margem || '')}</td><td>${escapeHtml((row.consulted_at || '').replace('T',' '))}</td></tr>`).join('') || '<tr><td colspan="6" class="muted">Nenhuma consulta nesta origem.</td></tr>'}</table></div>`;
      }
      document.getElementById('opportunity-cards')?.addEventListener('click', e => { const card=e.target.closest('[data-opportunity]'); if(card) loadOpportunityClients(card.dataset.opportunity); });
      document.getElementById('opportunity-clients')?.addEventListener('click', e => { if(e.target.closest('[data-close-opportunity]')) { e.currentTarget.hidden=true; openOpportunity=null; }});
      let opportunityTimer;
      document.getElementById('opportunity-search')?.addEventListener('input', () => { if(!openOpportunity)return; clearTimeout(opportunityTimer); opportunityTimer=setTimeout(()=>loadOpportunityClients(openOpportunity),250); });
    })();
    </script>"""
    return layout(content, username=username)


def parse_multipart(handler):
    content_type = handler.headers.get("Content-Type", "")
    length = int(handler.headers.get("Content-Length", "0") or 0)
    if length > 70 * 1024 * 1024:
        raise ValueError("Arquivo maior que 70 MB.")
    body = handler.rfile.read(length)
    message = BytesParser(policy=default).parsebytes((f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n").encode() + body)
    fields, files = {}, {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        filename = part.get_filename()
        if filename:
            files[name or "file"] = (Path(filename).name, part.get_payload(decode=True) or b"")
        elif name:
            fields[name] = (part.get_payload(decode=True) or b"").decode(part.get_content_charset() or "utf-8", errors="replace")
    return fields, files


def audit_meta(connection, action, status_value, message, user_id=None, channel_id=None, campaign_id=None, technical=None):
    connection.execute(
        "INSERT INTO meta_audit_logs(user_id,channel_id,campaign_id,action,status,message,technical_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (user_id, channel_id, campaign_id, action, status_value, str(message or "")[:500], json.dumps(technical or {}, ensure_ascii=False), now()),
    )


def validate_meta_credentials(access_token, phone_number_id, waba_id, api_version):
    if not all((access_token, phone_number_id, waba_id)):
        raise ValueError("Informe Access Token, Phone Number ID e WABA ID.")
    return MetaClient(access_token, api_version or DEFAULT_GRAPH_API_VERSION).validate_connection(phone_number_id, waba_id)


def build_official_template_payload(name, language, category, body, example_values=None, buttons=None):
    name = str(name or "").strip()
    language = str(language or "pt_BR").strip()
    category = str(category or "").strip().upper()
    body = str(body or "").strip()
    examples = [str(value).strip() for value in (example_values or [])]
    quick_replies = [str(value).strip() for value in (buttons or []) if str(value).strip()]
    if not re.fullmatch(r"[a-z0-9_]{1,512}", name):
        raise ValueError("O nome deve usar somente letras minÃºsculas, nÃºmeros e sublinhado.")
    if language != "pt_BR":
        raise ValueError("Selecione um idioma permitido.")
    if category not in ("MARKETING", "UTILITY"):
        raise ValueError("Selecione a categoria Marketing ou Utilidade.")
    if not body:
        raise ValueError("Digite o texto do template.")
    if len(body) > 1024:
        raise ValueError("O texto do template pode ter no mÃ¡ximo 1.024 caracteres.")
    if re.search(r"\{\{[^}]*[A-Za-zÃ€-Ã¿][^}]*\}\}", body):
        raise ValueError("Na Meta, use variÃ¡veis numeradas: {{1}}, {{2}}, {{3}}.")
    positions = sorted({int(value) for value in re.findall(r"\{\{(\d+)\}\}", body)})
    if positions != list(range(1, max(positions, default=0) + 1)):
        raise ValueError("As variÃ¡veis devem ser sequenciais, comeÃ§ando por {{1}}.")
    if positions and (len(examples) < len(positions) or any(not examples[index - 1] for index in positions)):
        raise ValueError("Informe um exemplo para cada variÃ¡vel usada no texto.")
    if len(quick_replies) > 3:
        raise ValueError("Use no mÃ¡ximo 3 botÃµes de resposta.")
    if any(len(label) > 25 for label in quick_replies):
        raise ValueError("Cada botÃ£o pode ter no mÃ¡ximo 25 caracteres.")
    if len({label.casefold() for label in quick_replies}) != len(quick_replies):
        raise ValueError("Os textos dos botÃµes nÃ£o podem ser repetidos.")
    components = [{"type": "BODY", "text": body}]
    if positions:
        components[0]["example"] = {"body_text": [[examples[index - 1] for index in positions]]}
    if quick_replies:
        components.append({"type": "BUTTONS", "buttons": [{"type": "QUICK_REPLY", "text": label} for label in quick_replies]})
    return {"name": name, "language": language, "category": category, "components": components}


def start_campaign_worker():
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(
        [sys.executable, str(BASE_DIR / "campaign_worker.py")],
        cwd=str(BASE_DIR), creationflags=flags,
    )


def start_web_campaign_worker():
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(
        [sys.executable, str(BASE_DIR / "web_campaign_worker.py")],
        cwd=str(BASE_DIR), creationflags=flags,
    )


def scheduled_campaign_loop():
    """Start scheduled campaigns automatically when their time arrives."""
    while True:
        due_modes = set()
        try:
            current = now()
            with db() as connection:
                due = connection.execute(
                    "SELECT id,send_mode FROM whatsapp_campaigns WHERE status='agendada' AND scheduled_at IS NOT NULL AND scheduled_at<=?",
                    (current,),
                ).fetchall()
                for campaign in due:
                    connection.execute(
                        "UPDATE whatsapp_campaigns SET status='em processamento',started_at=COALESCE(started_at,?),updated_at=? WHERE id=? AND status='agendada'",
                        (current, current, campaign["id"]),
                    )
                    due_modes.add(campaign["send_mode"])
            if "web" in due_modes:
                start_web_campaign_worker()
            if "meta" in due_modes:
                start_campaign_worker()
        except Exception:
            traceback.print_exc()
        time.sleep(5)


def normalize_brazil_phone(value):
    digits = "".join(char for char in str(value or "") if char.isdigit())
    if digits.startswith("55"):
        national = digits[2:]
    else:
        national = digits
    if len(national) not in (10, 11) or national[:2] == "00" or national[2] == "0":
        return ""
    return "55" + national


def brazil_phone_variants(value):
    normalized = normalize_brazil_phone(value)
    if not normalized:
        return []
    variants = [normalized]
    national = normalized[2:]
    if len(national) == 10:
        variants.append("55" + national[:2] + "9" + national[2:])
    elif len(national) == 11 and national[2] == "9":
        variants.append("55" + national[:2] + national[3:])
    return list(dict.fromkeys(variants))


def render_official_template(body, variable_map, row):
    rendered = str(body or "")
    values = []
    body_positions = [str(item) for item in variable_map if str(item).isdigit()]
    named_positions = [str(item) for item in variable_map if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(item))]
    for position in sorted(body_positions, key=int) + named_positions:
        column = variable_map[position]
        value = campaign_variable_value(column, row.get(column, ""))
        rendered = rendered.replace("{{" + str(position) + "}}", value)
        values.append(value)
    return rendered, values


def render_attempt_template(body, variables_json):
    """Render the exact text represented by one Meta send attempt."""
    rendered = str(body or "")
    try:
        variables = json.loads(variables_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        variables = {}
    for position, value in enumerate(variables.get("body", []), start=1):
        text = value.get("text", "") if isinstance(value, dict) else value
        rendered = rendered.replace("{{" + str(position) + "}}", str(text or ""))
    return rendered


def resolve_official_variables(specs, variable_map, row):
    resolved = {"header": [], "body": [], "buttons": {}}
    for spec in specs:
        canonical = spec["key"]
        alias = str(spec["position"]) if spec["component"] == "body" else canonical
        column = variable_map.get(canonical, variable_map.get(alias))
        if not column:
            raise ValueError(f"Relacione a variÃ¡vel {canonical}.")
        value = campaign_variable_value(column, row.get(column, ""))
        if spec["component"] == "button":
            resolved["buttons"].setdefault(str(spec["button_index"]), []).append(value)
        elif spec.get("parameter_name"):
            resolved[spec["component"]].append({"parameter_name": spec["parameter_name"], "text": value})
        else:
            resolved[spec["component"]].append(value)
    return resolved


def _meta_webhook_values(payload):
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            if str(change.get("field") or "") == "messages":
                yield change.get("value") or {}


def _meta_inbound_text(message):
    message_type = str(message.get("type") or "")
    if message_type == "text":
        return str((message.get("text") or {}).get("body") or "")
    if message_type == "button":
        button = message.get("button") or {}
        return str(button.get("text") or button.get("payload") or "")
    if message_type == "interactive":
        interactive = message.get("interactive") or {}
        reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
        return str(reply.get("title") or reply.get("id") or "")
    return f"[{message_type or 'mensagem'}]"


def verify_and_store_meta_webhook(raw_body, signature):
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Webhook da Meta com JSON invÃ¡lido.") from exc
    values = list(_meta_webhook_values(payload))
    phone_number_id = next((
        str((value.get("metadata") or {}).get("phone_number_id") or "")
        for value in values if (value.get("metadata") or {}).get("phone_number_id")
    ), "")
    if not phone_number_id:
        raise ValueError("Webhook sem Phone Number ID.")
    with db() as connection:
        channel = connection.execute(
            "SELECT * FROM whatsapp_channels WHERE mode='meta' AND active=1 AND meta_phone_number_id=?",
            (phone_number_id,),
        ).fetchone()
    if not channel:
        raise ValueError("Webhook recebido para um nÃºmero nÃ£o cadastrado.")
    secret = CredentialVault(META_KEY_PATH).decrypt(channel["meta_app_secret_enc"])
    if not secret:
        raise PermissionError("Cadastre o App Secret da Meta no canal oficial antes de ativar o webhook.")
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    if not signature or not hmac.compare_digest(expected, signature):
        raise PermissionError("Assinatura do webhook invÃ¡lida.")

    processed = 0
    with db() as connection:
        for value in values:
            contacts = {
                str(contact.get("wa_id") or ""): str((contact.get("profile") or {}).get("name") or "")
                for contact in value.get("contacts") or []
            }
            for message in value.get("messages") or []:
                provider_id = str(message.get("id") or "")
                phone = str(message.get("from") or "")
                event_key = "message:" + provider_id
                inserted = connection.execute(
                    """INSERT OR IGNORE INTO meta_webhook_events
                       (event_key,channel_id,event_type,provider_message_id,phone,payload_json,received_at)
                       VALUES(?,?,?,?,?,?,?)""",
                    (event_key, channel["id"], "message", provider_id, phone, json.dumps(message, ensure_ascii=False), now()),
                ).rowcount
                if not inserted:
                    continue
                phone_variants = brazil_phone_variants(phone)
                lookup_phones = (phone_variants + ["", ""])[:2]
                item = connection.execute(
                    """SELECT i.*,c.id AS campaign_id
                         FROM whatsapp_campaign_items i
                         JOIN whatsapp_campaigns c ON c.id=i.campaign_id
                         JOIN meta_campaign_send_attempts a ON a.item_id=i.id AND a.channel_id=?
                        WHERE i.normalized_phone IN (?,?)
                        ORDER BY a.updated_at DESC LIMIT 1""",
                    (channel["id"], lookup_phones[0], lookup_phones[1]),
                ).fetchone()
                display_phone = item["telefone"] if item else format_phone(
                    (phone_variants[1] if len(phone_variants) > 1 and len(phone_variants[1]) == 13 else phone)[2:]
                    if phone.startswith("55") else phone
                )
                text = _meta_inbound_text(message)
                timestamp = now()
                connection.execute(
                    """INSERT INTO whatsapp_messages
                       (client_id,channel_id,direction,modalidade,status,telefone,telefone_conectado,
                        mensagem,provider_message_id,raw_json,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (item["id"] if item else None, channel["id"], "inbound", "meta", "respondido",
                     display_phone, channel["telefone"], text, provider_id,
                     json.dumps({"message": message, "contact_name": contacts.get(phone, "")}, ensure_ascii=False),
                     timestamp, timestamp),
                )
                campaign_id = item["campaign_id"] if item else None
                audit_meta(
                    connection, "meta_inbound", "recebido",
                    f"Resposta recebida: {text[:180]}", None, channel["id"], campaign_id,
                    {"provider_message_id": provider_id, "phone": phone, "type": message.get("type")},
                )
                connection.execute(
                    "UPDATE meta_webhook_events SET processed_at=? WHERE event_key=?",
                    (timestamp, event_key),
                )
                processed += 1
            for status_event in value.get("statuses") or []:
                provider_id = str(status_event.get("id") or "")
                status_value = str(status_event.get("status") or "unknown")
                event_key = f"status:{provider_id}:{status_value}"
                inserted = connection.execute(
                    """INSERT OR IGNORE INTO meta_webhook_events
                       (event_key,channel_id,event_type,provider_message_id,phone,payload_json,received_at)
                       VALUES(?,?,?,?,?,?,?)""",
                    (event_key, channel["id"], "status", provider_id,
                     str(status_event.get("recipient_id") or ""),
                     json.dumps(status_event, ensure_ascii=False), now()),
                ).rowcount
                if not inserted:
                    continue
                attempt = connection.execute(
                    """SELECT a.*,i.telefone,c.preview_text,t.body_text
                         FROM meta_campaign_send_attempts a
                         JOIN whatsapp_campaign_items i ON i.id=a.item_id
                         JOIN whatsapp_campaigns c ON c.id=a.campaign_id
                         JOIN meta_official_templates t ON t.id=a.template_id
                        WHERE a.wamid=? LIMIT 1""",
                    (provider_id,),
                ).fetchone()
                if attempt:
                    existing = connection.execute(
                        "SELECT id FROM whatsapp_messages WHERE provider_message_id=? AND direction='outbound' ORDER BY id DESC LIMIT 1",
                        (provider_id,),
                    ).fetchone()
                    timestamp = now()
                    if existing:
                        connection.execute(
                            "UPDATE whatsapp_messages SET status=?,raw_json=?,updated_at=? WHERE id=?",
                            (status_value, json.dumps(status_event, ensure_ascii=False), timestamp, existing["id"]),
                        )
                    else:
                        sent_text = render_attempt_template(attempt["body_text"], attempt["variables_json"])
                        connection.execute(
                            """INSERT INTO whatsapp_messages
                               (channel_id,direction,modalidade,status,telefone,telefone_conectado,mensagem,
                                provider_message_id,raw_json,created_at,updated_at)
                               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                            (channel["id"], "outbound", "meta", status_value, attempt["telefone"],
                             channel["telefone"], sent_text or attempt["preview_text"] or "", provider_id,
                             json.dumps(status_event, ensure_ascii=False), timestamp, timestamp),
                        )
                    errors = status_event.get("errors") or []
                    if status_value == "failed" and errors:
                        error = errors[0] or {}
                        error_code = str(error.get("code") or "")
                        if error_code in ("131026", "131049"):
                            kind = "permanent" if error_code == "131026" else "temporary"
                            reason = ("NÃºmero nÃ£o entregÃ¡vel pela Meta (131026)." if kind == "permanent"
                                      else "Bloqueio temporÃ¡rio de entrega da Meta (131049).")
                            blocked_until = None if kind == "permanent" else (datetime.now() + timedelta(hours=24)).isoformat(timespec="seconds")
                            previous = connection.execute(
                                "SELECT suppression_type FROM meta_phone_suppressions WHERE normalized_phone=?",
                                (attempt["normalized_phone"],),
                            ).fetchone()
                            if not previous or previous["suppression_type"] != "permanent":
                                connection.execute(
                                    """INSERT INTO meta_phone_suppressions(
                                           normalized_phone,suppression_type,error_code,reason,source_campaign_id,source_item_id,
                                           first_seen_at,last_seen_at,blocked_until,active,reactivated_at,reactivated_by_user_id
                                       ) VALUES(?,?,?,?,?,?,?,?,?,1,NULL,NULL)
                                       ON CONFLICT(normalized_phone) DO UPDATE SET
                                           suppression_type=excluded.suppression_type,error_code=excluded.error_code,
                                           reason=excluded.reason,source_campaign_id=excluded.source_campaign_id,
                                           source_item_id=excluded.source_item_id,last_seen_at=excluded.last_seen_at,
                                           blocked_until=excluded.blocked_until,active=1,
                                           reactivated_at=NULL,reactivated_by_user_id=NULL""",
                                    (attempt["normalized_phone"], kind, error_code, reason,
                                     attempt["campaign_id"], attempt["item_id"], timestamp, timestamp, blocked_until),
                                )
                connection.execute(
                    "UPDATE meta_webhook_events SET processed_at=? WHERE event_key=?",
                    (now(), event_key),
                )
                processed += 1
    return processed


def _relay_settings():
    with db() as connection:
        rows = connection.execute(
            """SELECT setting_key,setting_value FROM app_settings
                 WHERE setting_key IN ('meta_webhook_relay_url','meta_webhook_relay_key')"""
        ).fetchall()
    values = {row["setting_key"]: row["setting_value"] for row in rows}
    return values.get("meta_webhook_relay_url", "").rstrip("/"), values.get("meta_webhook_relay_key", "")


def _relay_request(url, key, action, payload=None):
    endpoint = f"{url}/?action={action}"
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        endpoint,
        data=data,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Relay-Key": key,
            "User-Agent": "1Clik-Webhook-Relay/1.0",
        },
        method="GET" if payload is None else "POST",
    )
    with urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def meta_webhook_relay_loop():
    while True:
        try:
            relay_url, relay_key = _relay_settings()
            if relay_url and relay_key:
                result = _relay_request(relay_url, relay_key, "pull")
                acknowledged = []
                for event in result.get("events") or []:
                    event_id = str(event.get("id") or "")
                    raw_body = base64.b64decode(str(event.get("body_b64") or ""), validate=True)
                    signature = str(event.get("signature") or "")
                    try:
                        verify_and_store_meta_webhook(raw_body, signature)
                    except PermissionError:
                        # Preserve queued events until the App Secret is configured.
                        break
                    except ValueError:
                        # Invalid or unrelated events must not block the queue forever.
                        acknowledged.append(event_id)
                    else:
                        acknowledged.append(event_id)
                if acknowledged:
                    _relay_request(relay_url, relay_key, "ack", {"ids": acknowledged})
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
            pass
        time.sleep(10)


class Handler(BaseHTTPRequestHandler):
    def send_html(self, body, code=200):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, payload, code=200):
        data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, url, cookie_value=None, clear_cookie=False):
        self.send_response(302)
        self.send_header("Location", url)
        if cookie_value:
            self.send_header("Set-Cookie", f"{SESSION_COOKIE}={cookie_value}; Path=/; HttpOnly; SameSite=Lax; Max-Age=43200")
        elif clear_cookie:
            self.send_header("Set-Cookie", f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0")
        self.end_headers()

    def form_fields(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        return {key: values[0] for key, values in parse_qs(raw).items()}

    def session_token(self):
        jar = cookies.SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
            return jar[SESSION_COOKIE].value if SESSION_COOKIE in jar else ""
        except Exception:
            return ""

    def current_user(self):
        token = self.session_token()
        if not token:
            return None
        with db() as connection:
            row = connection.execute("""SELECT u.*,s.last_seen FROM app_sessions s JOIN app_users u ON u.id=s.user_id
                WHERE s.token=? AND u.active=1""", (token,)).fetchone()
            if not row:
                return None
            try:
                if datetime.fromisoformat(row["last_seen"]) < datetime.now() - timedelta(hours=12):
                    connection.execute("DELETE FROM app_sessions WHERE token=?", (token,))
                    return None
            except Exception:
                return None
            connection.execute("UPDATE app_sessions SET last_seen=? WHERE token=?", (now(), token))
            return row

    def require_user(self):
        user = self.current_user()
        if not user:
            self.redirect("/login")
            return None
        return user

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/meta/webhook":
            query = parse_qs(parsed.query)
            mode = (query.get("hub.mode") or [""])[0]
            supplied_token = (query.get("hub.verify_token") or [""])[0]
            challenge = (query.get("hub.challenge") or [""])[0]
            with db() as connection:
                row = connection.execute(
                    "SELECT setting_value FROM app_settings WHERE setting_key='meta_webhook_verify_token'"
                ).fetchone()
            expected_token = row["setting_value"] if row else ""
            if mode == "subscribe" and expected_token and hmac.compare_digest(supplied_token, expected_token):
                data = challenge.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_json({"error": "Falha na verificaÃ§Ã£o do webhook."}, 403)
            return
        if path.startswith("/api/"):
            user = self.require_user()
            if not user:
                return
            query = parse_qs(parsed.query)
            try:
                if path == "/api/conversations/campaigns":
                    with db() as connection:
                        self.send_json({"campaigns": campaign_summaries(connection)})
                    return
                if path == "/api/conversations/contacts":
                    campaign_id = int((query.get("campaign_id") or ["0"])[0])
                    metric = (query.get("metric") or ["responded"])[0]
                    search = (query.get("search") or [""])[0]
                    limit = int((query.get("limit") or ["100"])[0])
                    offset = int((query.get("offset") or ["0"])[0])
                    with db() as connection:
                        contacts, total = campaign_contacts(connection, campaign_id, metric, search, limit, offset)
                    self.send_json({"contacts": contacts, "total": total})
                    return
                if path == "/api/suppressions":
                    search = (query.get("search") or [""])[0].strip()
                    with db() as connection:
                        rows = connection.execute(
                            """SELECT normalized_phone,suppression_type,error_code,reason,first_seen_at,last_seen_at,
                                      blocked_until,active,reactivated_at
                                 FROM meta_phone_suppressions
                                WHERE (?='' OR normalized_phone LIKE ? OR reason LIKE ? OR error_code LIKE ?)
                                ORDER BY active DESC,last_seen_at DESC""",
                            (search, f"%{search}%", f"%{search}%", f"%{search}%"),
                        ).fetchall()
                    self.send_json({"numbers": [dict(row) for row in rows]})
                    return
                if path == "/api/opportunities":
                    category = (query.get("category") or [None])[0]
                    search = (query.get("search") or [""])[0]
                    limit = int((query.get("limit") or ["100"])[0])
                    offset = int((query.get("offset") or ["0"])[0])
                    self.send_json(opportunity_data(category, search, limit, offset))
                    return
                self.send_json({"error": "Endpoint nÃ£o encontrado."}, 404)
            except (ValueError, sqlite3.Error) as exc:
                self.send_json({"error": str(exc)}, 400)
            return
        if path == "/health":
            data = json.dumps({"ok": True, "app": "1Clik", "database": str(DB_PATH)}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
        elif path == "/login":
            if self.current_user():
                self.redirect("/")
            else:
                self.send_html(login_page())
        elif path == "/esqueci-senha":
            self.send_html(forgot_page())
        elif path == "/redefinir-senha":
            token = (parse_qs(parsed.query).get("token") or [""])[0]
            self.send_html(reset_page(token) if token else login_page("Link de recuperaÃ§Ã£o invÃ¡lido.", True), 200 if token else 400)
        elif path == "/logout":
            token = self.session_token()
            if token:
                with db() as connection:
                    connection.execute("DELETE FROM app_sessions WHERE token=?", (token,))
            self.redirect("/login", clear_cookie=True)
        elif path == "/":
            user = self.require_user()
            if user:
                self.send_html(dashboard(username=user["username"]))
        else:
            user = self.require_user()
            if user:
                self.send_html(layout("<h1>PÃ¡gina nÃ£o encontrada</h1>", username=user["username"]), 404)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == "/meta/webhook":
                length = int(self.headers.get("Content-Length", "0") or 0)
                if length <= 0 or length > 2_000_000:
                    self.send_json({"error": "Payload de webhook invÃ¡lido."}, 400)
                    return
                raw_body = self.rfile.read(length)
                signature = self.headers.get("X-Hub-Signature-256", "")
                try:
                    processed = verify_and_store_meta_webhook(raw_body, signature)
                except PermissionError as exc:
                    self.send_json({"error": str(exc)}, 403)
                    return
                except ValueError as exc:
                    self.send_json({"error": str(exc)}, 400)
                    return
                self.send_json({"ok": True, "processed": processed})
                return
            if path == "/login":
                fields = self.form_fields()
                username = fields.get("username", "").strip()
                password = fields.get("password", "")
                with db() as connection:
                    user = connection.execute("SELECT * FROM app_users WHERE username=? AND active=1", (username,)).fetchone()
                    if not user or not verify_password(password, user["password_hash"]):
                        self.send_html(login_page("Login ou senha invÃ¡lidos.", True), 401)
                        return
                    token = secrets.token_urlsafe(40)
                    connection.execute("INSERT INTO app_sessions(token,user_id,created_at,last_seen) VALUES(?,?,?,?)", (token, user["id"], now(), now()))
                self.redirect("/", cookie_value=token)
                return
            if path == "/esqueci-senha":
                identity = self.form_fields().get("identity", "").strip()
                with db() as connection:
                    user = connection.execute("SELECT * FROM app_users WHERE active=1 AND (username=? OR lower(email)=lower(?))", (identity, identity)).fetchone()
                    if user:
                        token = secrets.token_urlsafe(36)
                        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
                        expires = (datetime.now() + timedelta(minutes=30)).isoformat(timespec="seconds")
                        connection.execute("INSERT INTO password_reset_tokens(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)", (token_hash, user["id"], expires, now()))
                        send_password_reset_email(user["email"], token)
                self.send_html(forgot_page("Se os dados estiverem corretos, o link de recuperaÃ§Ã£o foi enviado."))
                return
            if path == "/redefinir-senha":
                fields = self.form_fields()
                token = fields.get("token", "")
                password = fields.get("password", "")
                confirmation = fields.get("confirm_password", "")
                if len(password) < 8 or password != confirmation:
                    self.send_html(reset_page(token, "As senhas devem ser iguais e ter pelo menos 8 caracteres.", True), 400)
                    return
                token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
                with db() as connection:
                    reset = connection.execute("SELECT * FROM password_reset_tokens WHERE token_hash=? AND used_at IS NULL", (token_hash,)).fetchone()
                    if not reset or datetime.fromisoformat(reset["expires_at"]) < datetime.now():
                        self.send_html(login_page("Link de recuperaÃ§Ã£o invÃ¡lido ou expirado.", True), 400)
                        return
                    connection.execute("UPDATE app_users SET password_hash=?,updated_at=? WHERE id=?", (hash_password(password), now(), reset["user_id"]))
                    connection.execute("UPDATE password_reset_tokens SET used_at=? WHERE token_hash=?", (now(), token_hash))
                    connection.execute("DELETE FROM app_sessions WHERE user_id=?", (reset["user_id"],))
                self.send_html(login_page("Senha redefinida. Entre com a nova senha."))
                return
            user = self.require_user()
            if not user:
                return
            if path == "/api/conversations/handled":
                length = int(self.headers.get("Content-Length", "0") or 0)
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                item_id = int(payload.get("item_id") or 0)
                handled = 1 if payload.get("handled", True) else 0
                with db() as connection:
                    item = connection.execute(
                        "SELECT id,campaign_id FROM whatsapp_campaign_items WHERE id=?",
                        (item_id,),
                    ).fetchone()
                    if not item:
                        self.send_json({"error": "Contato da campanha nÃ£o encontrado."}, 404)
                        return
                    connection.execute(
                        """INSERT INTO conversation_handled(
                               campaign_item_id,campaign_id,handled,handled_by_user_id,handled_at,updated_at
                           ) VALUES(?,?,?,?,?,?)
                           ON CONFLICT(campaign_item_id) DO UPDATE SET
                               handled=excluded.handled,
                               handled_by_user_id=excluded.handled_by_user_id,
                               handled_at=excluded.handled_at,
                               updated_at=excluded.updated_at""",
                        (item_id, item["campaign_id"], handled, user["id"], now() if handled else None, now()),
                    )
                self.send_json({"ok": True, "item_id": item_id, "handled": bool(handled)})
                return
            if path == "/api/suppressions/reactivate":
                length = int(self.headers.get("Content-Length", "0") or 0)
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                phone = normalize_brazil_phone(payload.get("phone"))
                if not phone:
                    self.send_json({"error": "NÃºmero invÃ¡lido."}, 400)
                    return
                with db() as connection:
                    changed = connection.execute(
                        """UPDATE meta_phone_suppressions
                              SET active=0,reactivated_at=?,reactivated_by_user_id=?
                            WHERE normalized_phone=? AND active=1""",
                        (now(), user["id"], phone),
                    ).rowcount
                self.send_json({"ok": True, "phone": phone, "reactivated": bool(changed)})
                return
            if path == "/connect":
                result = start()
                self.send_html(dashboard("Navegador do 1Clik aberto. Escaneie o QR Code se for solicitado." if result.get("running") else result.get("error", "NÃ£o foi possÃ­vel abrir."), not result.get("running")))
                return
            if path == "/channel/connect":
                fields = self.form_fields()
                channel_id = int(fields.get("channel_id", "0") or 0)
                with db() as connection:
                    channel = connection.execute("SELECT * FROM whatsapp_channels WHERE id=? AND mode='web' AND active=1", (channel_id,)).fetchone()
                if not channel:
                    raise ValueError("ConexÃ£o por QR Code nÃ£o encontrada.")
                result = start(channel["web_cdp_port"], channel["web_profile_dir"])
                message = "QR Code aberto para esta conexÃ£o. Escaneie pelo celular." if result.get("running") else result.get("error", "NÃ£o foi possÃ­vel abrir o QR Code.")
                self.send_html(dashboard(message, not result.get("running"), user["username"])); return
            if path == "/channel":
                fields, _ = parse_multipart(self) if "multipart/form-data" in self.headers.get("Content-Type", "") else (parse_qs(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode("utf-8", errors="replace")), {})
                value = lambda key, default="": (fields.get(key, [default])[0] if isinstance(fields.get(key), list) else fields.get(key, default)).strip()
                channel_name = value("nome")
                channel_phone = format_phone(value("telefone"))
                if not channel_name:
                    raise ValueError("Informe o nome da conexÃ£o.")
                if channel_phone and len("".join(char for char in channel_phone if char.isdigit())) not in (10, 11):
                    raise ValueError("Informe um nÃºmero vÃ¡lido com DDD.")
                mode = value("mode", "web")
                channel_action = value("channel_action", "save")
                existing_meta_channel_id = int(value("existing_meta_channel_id", "0") or 0)
                if mode not in ("web", "meta", "manual"):
                    raise ValueError("Tipo de conexÃ£o invÃ¡lido.")
                if mode == "meta" and not all((value("meta_access_token"), value("meta_phone_number_id"), value("meta_waba_id"))):
                    raise ValueError("Para a API Oficial, informe Access Token, Phone Number ID e WABA ID.")
                if mode == "meta" and user["role"] != "master":
                    self.send_html(dashboard("Somente o usuÃ¡rio master pode alterar credenciais da Meta.", True, user["username"]), 403); return
                connection_details = None
                if mode == "meta":
                    try:
                        connection_details = validate_meta_credentials(value("meta_access_token"), value("meta_phone_number_id"), value("meta_waba_id"), value("meta_api_version", DEFAULT_GRAPH_API_VERSION))
                    except MetaApiError as exc:
                        with db() as connection:
                            audit_meta(connection, "connection_test", "falhou", str(exc), user["id"], technical={"http_status": exc.http_status, "error_code": exc.error_code})
                        self.send_html(dashboard("A conexÃ£o com a Meta nÃ£o foi validada. Confira o token e os identificadores.", True, user["username"]), 400); return
                    if channel_action == "test":
                        with db() as connection:
                            audit_meta(connection, "connection_test", "sucesso", "Credenciais validadas pela Meta.", user["id"], technical=connection_details)
                        summary = f"ConexÃ£o validada: {connection_details.get('display_phone_number') or 'nÃºmero confirmado'} â€” {connection_details.get('verified_name') or 'nome confirmado'} â€” qualidade {connection_details.get('quality_rating') or 'nÃ£o informada'} â€” token vÃ¡lido."
                        self.send_html(dashboard(summary, False, user["username"])); return
                with db() as connection:
                    next_port = 9341 + connection.execute("SELECT COUNT(*) FROM whatsapp_channels WHERE mode='web'").fetchone()[0]
                    timestamp = now()
                    vault = CredentialVault(META_KEY_PATH)
                    token = value("meta_access_token") if mode == "meta" else ""
                    app_secret = value("meta_app_secret") if mode == "meta" else ""
                    if mode == "meta" and existing_meta_channel_id:
                        existing = connection.execute("SELECT id FROM whatsapp_channels WHERE id=? AND mode='meta'", (existing_meta_channel_id,)).fetchone()
                        if not existing:
                            raise ValueError("Canal oficial existente nÃ£o encontrado.")
                        connection.execute("""UPDATE whatsapp_channels SET nome=?,telefone=?,meta_phone_number_id=?,meta_access_token='',meta_access_token_enc=?,meta_token_masked=?,meta_api_version=?,official_number=1,meta_waba_id=?,meta_app_secret='',meta_app_secret_enc=?,meta_connection_status='validada',meta_connection_details_json=?,meta_last_tested_at=?,updated_at=? WHERE id=?""",
                            (channel_name, channel_phone, value("meta_phone_number_id"), vault.encrypt(token), mask_secret(token), value("meta_api_version", DEFAULT_GRAPH_API_VERSION) or DEFAULT_GRAPH_API_VERSION, value("meta_waba_id"), vault.encrypt(app_secret), json.dumps(connection_details or {}, ensure_ascii=False), timestamp, timestamp, existing_meta_channel_id))
                        audit_meta(connection, "credentials_change", "sucesso", "Credenciais oficiais substituÃ­das e protegidas.", user["id"], existing_meta_channel_id)
                        connection.commit()
                        self.send_html(dashboard("Credenciais oficiais atualizadas e validadas.", username=user["username"])); return
                    cursor = connection.execute("""INSERT INTO whatsapp_channels(nome,telefone,mode,meta_phone_number_id,meta_access_token,meta_access_token_enc,meta_token_masked,meta_api_version,official_number,active,created_at,updated_at,web_cdp_port,web_profile_dir,rotation_enabled,meta_waba_id,meta_app_secret,meta_app_secret_enc,meta_connection_status,meta_connection_details_json,meta_last_tested_at)
                                          VALUES(?,?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?,?,?)""", (channel_name, channel_phone, mode, value("meta_phone_number_id"), "", vault.encrypt(token), mask_secret(token) if token else "", value("meta_api_version", DEFAULT_GRAPH_API_VERSION) or DEFAULT_GRAPH_API_VERSION, 1 if mode == "meta" else 0, timestamp, timestamp, next_port if mode == "web" else None, str(DATA_DIR / "profiles" / uuid.uuid4().hex) if mode == "web" else None, 1 if mode == "web" else 0, value("meta_waba_id"), "", vault.encrypt(app_secret), "validada" if mode == "meta" else "ativa", json.dumps(connection_details or {}, ensure_ascii=False), timestamp if mode == "meta" else None))
                    connection.commit()
                    saved = connection.execute("SELECT id FROM whatsapp_channels WHERE id=?", (cursor.lastrowid,)).fetchone()
                    if not saved:
                        raise RuntimeError("A conexÃ£o nÃ£o foi confirmada no banco.")
                    audit_meta(connection, "credentials_change", "sucesso", "ConexÃ£o cadastrada com credenciais protegidas.", user["id"], cursor.lastrowid)
                if mode == "web":
                    with db() as connection:
                        web_channel = connection.execute("SELECT * FROM whatsapp_channels WHERE id=?", (cursor.lastrowid,)).fetchone()
                    result = start(web_channel["web_cdp_port"], web_channel["web_profile_dir"])
                    message = "WhatsApp adicionado. Escaneie o QR Code aberto no navegador." if result.get("running") else result.get("error", "ConexÃ£o salva, mas o QR Code nÃ£o abriu.")
                    self.send_html(dashboard(message, not result.get("running"), user["username"])); return
                self.send_html(dashboard("ConexÃ£o adicionada e confirmada no 1Clik.", username=user["username"])); return
            if path == "/meta/templates/sync":
                if user["role"] != "master":
                    self.send_html(dashboard("Somente o usuÃ¡rio master pode sincronizar templates.", True, user["username"]), 403); return
                fields = self.form_fields()
                channel_id = int(fields.get("channel_id", "0") or 0)
                with db() as connection:
                    channel = connection.execute("SELECT * FROM whatsapp_channels WHERE id=? AND mode='meta' AND active=1", (channel_id,)).fetchone()
                if not channel:
                    raise ValueError("Selecione um canal oficial ativo.")
                try:
                    token = CredentialVault(META_KEY_PATH).decrypt(channel["meta_access_token_enc"])
                    templates_meta = MetaClient(token, channel["meta_api_version"] or DEFAULT_GRAPH_API_VERSION).list_templates(channel["meta_waba_id"])
                except MetaApiError as exc:
                    with db() as connection:
                        audit_meta(connection, "template_sync", "falhou", str(exc), user["id"], channel_id, technical={"http_status": exc.http_status, "error_code": exc.error_code})
                    self.send_html(dashboard("NÃ£o foi possÃ­vel sincronizar os templates. Teste novamente a conexÃ£o com a Meta.", True, user["username"]), 400); return
                synced_at = now()
                with db() as connection:
                    for template in templates_meta:
                        components = template.get("components") or []
                        buttons = next((item.get("buttons") or [] for item in components if str(item.get("type") or "").upper() == "BUTTONS"), [])
                        connection.execute("""INSERT INTO meta_official_templates(channel_id,meta_template_id,name,language,category,status,header_text,body_text,footer_text,buttons_json,components_json,variables_json,synced_at)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                            ON CONFLICT(channel_id,name,language) DO UPDATE SET meta_template_id=excluded.meta_template_id,category=excluded.category,status=excluded.status,header_text=excluded.header_text,body_text=excluded.body_text,footer_text=excluded.footer_text,buttons_json=excluded.buttons_json,components_json=excluded.components_json,variables_json=excluded.variables_json,synced_at=excluded.synced_at""",
                            (channel_id, str(template.get("id") or ""), str(template.get("name") or ""), str(template.get("language") or ""), str(template.get("category") or ""), str(template.get("status") or "UNKNOWN").upper(), component_text(components, "HEADER"), component_text(components, "BODY"), component_text(components, "FOOTER"), json.dumps(buttons, ensure_ascii=False), json.dumps(components, ensure_ascii=False), json.dumps(template_variable_specs(components), ensure_ascii=False), synced_at))
                    audit_meta(connection, "template_sync", "sucesso", f"{len(templates_meta)} templates sincronizados.", user["id"], channel_id)
                self.send_html(dashboard(f"SincronizaÃ§Ã£o concluÃ­da: {len(templates_meta)} templates encontrados.", username=user["username"])); return
            if path == "/meta/templates/create":
                if user["role"] != "master":
                    self.send_html(dashboard("Somente o usuÃ¡rio master pode criar templates oficiais.", True, user["username"]), 403); return
                fields = self.form_fields()
                if fields.get("confirm_submission") != "yes":
                    raise ValueError("Confirme o envio do modelo para anÃ¡lise da Meta.")
                channel_id = int(fields.get("channel_id", "0") or 0)
                payload = build_official_template_payload(
                    fields.get("template_name", ""),
                    fields.get("language", "pt_BR"),
                    fields.get("category", ""),
                    fields.get("body", ""),
                    str(fields.get("example_values", "")).splitlines(),
                    [fields.get(f"button_{index}", "") for index in range(1, 4)],
                )
                with db() as connection:
                    channel = connection.execute("SELECT * FROM whatsapp_channels WHERE id=? AND mode='meta' AND active=1", (channel_id,)).fetchone()
                if not channel:
                    raise ValueError("Selecione um canal oficial ativo.")
                try:
                    token = CredentialVault(META_KEY_PATH).decrypt(channel["meta_access_token_enc"])
                    response, http_status = MetaClient(token, channel["meta_api_version"] or DEFAULT_GRAPH_API_VERSION).request(
                        "POST", f"{channel['meta_waba_id']}/message_templates", payload=payload
                    )
                except MetaApiError as exc:
                    with db() as connection:
                        audit_meta(connection, "template_create", "falhou", str(exc), user["id"], channel_id, technical={"http_status": exc.http_status, "error_code": exc.error_code, "template_name": payload["name"]})
                    self.send_html(dashboard(f"A Meta nÃ£o aceitou o template: {exc}", True, user["username"]), 400); return
                components = payload["components"]
                quick_replies = next((item["buttons"] for item in components if item["type"] == "BUTTONS"), [])
                status_value = str(response.get("status") or "PENDING").upper()
                created_at = now()
                with db() as connection:
                    connection.execute("""INSERT INTO meta_official_templates(channel_id,meta_template_id,name,language,category,status,header_text,body_text,footer_text,buttons_json,components_json,variables_json,synced_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(channel_id,name,language) DO UPDATE SET meta_template_id=excluded.meta_template_id,category=excluded.category,status=excluded.status,body_text=excluded.body_text,buttons_json=excluded.buttons_json,components_json=excluded.components_json,variables_json=excluded.variables_json,synced_at=excluded.synced_at""",
                        (channel_id, str(response.get("id") or ""), payload["name"], payload["language"], payload["category"], status_value, "", fields.get("body", "").strip(), "", json.dumps(quick_replies, ensure_ascii=False), json.dumps(components, ensure_ascii=False), json.dumps(template_variable_specs(components), ensure_ascii=False), created_at))
                    audit_meta(connection, "template_create", "sucesso", f"Template {payload['name']} enviado para anÃ¡lise. Status: {status_value}.", user["id"], channel_id, technical={"http_status": http_status, "meta_template_id": response.get("id")})
                self.send_html(dashboard(f"Template {payload['name']} criado e enviado para anÃ¡lise da Meta. Status: {status_value}. Nenhuma mensagem foi disparada.", username=user["username"])); return
            if path == "/template":
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode("utf-8", errors="replace")
                fields = parse_qs(raw)
                name, message = (fields.get("nome") or [""])[0].strip(), (fields.get("mensagem") or [""])[0].strip()
                if not name or not message: raise ValueError("Informe nome e mensagem do modelo.")
                with db() as connection:
                    timestamp = now(); connection.execute("INSERT INTO whatsapp_templates(nome,mensagem,active,created_at,updated_at) VALUES(?,?,1,?,?)", (name, message, timestamp, timestamp))
                self.send_html(dashboard("Modelo salvo no 1Clik independente.")); return
            if path == "/campaign":
                fields, files = parse_multipart(self)
                list_file = files.get("file")
                if not list_file or not list_file[1]: raise ValueError("Selecione uma lista CSV ou XLSX.")
                filename, content = list_file
                campaign_media_path = ""
                media_upload = files.get("media_file")
                if media_upload and media_upload[1]:
                    media_name, media_content = media_upload
                    media_suffix = Path(media_name).suffix.lower()
                    if media_suffix not in ALLOWED_MEDIA:
                        raise ValueError("Formato de imagem ou vÃ­deo nÃ£o permitido.")
                    saved_media_name = f"campaign_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}{media_suffix}"
                    saved_media = UPLOAD_DIR / saved_media_name
                    saved_media.write_bytes(media_content)
                    campaign_media_path = str(saved_media)
                rows = []
                if Path(filename).suffix.lower() == ".csv":
                    text_data = content.decode("utf-8-sig", errors="replace")
                    rows = list(csv.DictReader(io.StringIO(text_data)))
                elif Path(filename).suffix.lower() == ".xlsx":
                    from openpyxl import load_workbook
                    workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
                    sheet = workbook.active
                    values = list(sheet.iter_rows(values_only=True))
                    headers = [str(item or "").strip() for item in (values[0] if values else [])]
                    rows = [dict(zip(headers, row)) for row in values[1:]]
                else:
                    raise ValueError("Use uma lista CSV ou XLSX.")
                # Excel tables often contain a formatted trailing row with no
                # values. It is not a contact and must not become a fake error.
                rows = [
                    row for row in rows
                    if any(str(value if value is not None else "").strip() for value in row.values())
                ]
                def pick(row, *names):
                    lowered = {str(key).strip().lower().replace(" ", "_"): value for key, value in row.items()}
                    for name in names:
                        value = lowered.get(name)
                        if value not in (None, ""): return str(value)
                    return ""
                timestamp, campaign_name = now(), fields.get("nome", "Campanha 1Clik").strip()
                send_mode = fields.get("send_mode", "web").strip().lower()
                channel_strategy = fields.get("channel_strategy", "single").strip().lower()
                try:
                    channel_ids = [int(item) for item in json.loads(fields.get("channel_ids_json", "[]") or "[]")]
                    interaction_buttons = [str(item).strip() for item in json.loads(fields.get("buttons_json", "[]") or "[]") if str(item).strip()]
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise ValueError("A seleÃ§Ã£o de nÃºmeros ou botÃµes estÃ¡ invÃ¡lida.") from exc
                channel_ids = list(dict.fromkeys(channel_ids))
                interaction_mode = fields.get("interaction_mode", "numbered").strip().lower()
                poll_question = fields.get("poll_question", "Como deseja continuar?").strip() or "Como deseja continuar?"
                if interaction_mode not in ("numbered", "poll", "official"):
                    raise ValueError("Escolha opÃ§Ãµes numeradas, enquete ou botÃµes da API Oficial.")
                if interaction_mode == "official" and send_mode != "meta":
                    raise ValueError("BotÃµes clicÃ¡veis exigem API Oficial e template aprovado pela Meta.")
                if interaction_mode == "poll" and len(interaction_buttons) < 2:
                    raise ValueError("A enquete precisa de pelo menos duas opÃ§Ãµes.")
                if send_mode not in ("web", "meta") or channel_strategy not in ("single", "rotation"):
                    raise ValueError("Escolha uma forma de envio e uma estratÃ©gia vÃ¡lidas.")
                if not channel_ids or (channel_strategy == "single" and len(channel_ids) != 1) or (channel_strategy == "rotation" and len(channel_ids) < 2):
                    raise ValueError("Escolha um nÃºmero especÃ­fico ou pelo menos dois nÃºmeros para o rodÃ­zio.")
                channel_id = channel_ids[0]
                message = fields.get("campaign_message", "").strip()
                official_template_id = int(fields.get("official_template_id", "0") or 0) or None
                interval_seconds = max(1, int(fields.get("interval_seconds", "30") or 30))
                max_sends = int(fields.get("max_sends", "0") or 0) or None
                scheduled_at = fields.get("scheduled_at", "").strip() or None
                variable_map = {}
                official_template = None
                channel = None
                with db() as connection:
                    selected_channels = connection.execute(f"SELECT * FROM whatsapp_channels WHERE active=1 AND id IN ({','.join('?' for _ in channel_ids)})", channel_ids).fetchall()
                    if len(selected_channels) != len(channel_ids) or any(row["mode"] != send_mode for row in selected_channels):
                        raise ValueError("Todos os nÃºmeros escolhidos devem estar ativos e usar a forma de envio selecionada.")
                    channels_by_id = {row["id"]: row for row in selected_channels}
                    channel = channels_by_id[channel_id]
                    if channel and channel["mode"] == "meta":
                        if user["role"] != "master":
                            raise ValueError("Somente o usuÃ¡rio master pode criar campanhas oficiais.")
                        try:
                            variable_map = json.loads(fields.get("variable_map_json", "{}") or "{}")
                        except json.JSONDecodeError as exc:
                            raise ValueError('O mapa de variÃ¡veis deve seguir o formato {"1":"nome"}.') from exc
                        if not isinstance(variable_map, dict):
                            raise ValueError("O mapa de variÃ¡veis precisa ser um objeto JSON.")
                        official_template = connection.execute("SELECT * FROM meta_official_templates WHERE id=? AND channel_id=? AND upper(status)='APPROVED'", (official_template_id, channel_id)).fetchone()
                        if not official_template:
                            raise ValueError("Selecione um template oficial aprovado deste canal.")
                        template_components = json.loads(official_template["components_json"] or "[]")
                        media_header = next((
                            component for component in template_components
                            if str(component.get("type") or "").upper() == "HEADER"
                            and str(component.get("format") or "").upper() in ("IMAGE", "VIDEO", "DOCUMENT")
                        ), None)
                        if media_header and not campaign_media_path:
                            raise ValueError("Este modelo oficial exige imagem. Carregue a mesma imagem aprovada na Meta antes de preparar a campanha.")
                        if media_header:
                            expected_format = str(media_header.get("format") or "").upper()
                            actual_mime = mimetypes.guess_type(campaign_media_path)[0] or ""
                            if expected_format == "IMAGE" and not actual_mime.startswith("image/"):
                                raise ValueError("O cabeÃ§alho deste modelo exige uma imagem.")
                            if expected_format == "VIDEO" and not actual_mime.startswith("video/"):
                                raise ValueError("O cabeÃ§alho deste modelo exige um vÃ­deo.")
                        variable_specs = json.loads(official_template["variables_json"] or "[]")
                        if not variable_specs:
                            variable_specs = template_variable_specs(json.loads(official_template["components_json"] or "[]"))
                        if variable_specs and not isinstance(variable_specs[0], dict):
                            variable_specs = [{"key": f"BODY:{value}", "component": "body", "position": int(value)} for value in variable_specs]
                        missing_positions = [spec["key"] for spec in variable_specs if spec["key"] not in variable_map and not (spec["component"] == "body" and str(spec["position"]) in variable_map)]
                        if missing_positions:
                            raise ValueError("Relacione todas as variÃ¡veis do template: " + ", ".join(missing_positions))
                        headers_available = set(rows[0].keys()) if rows else set()
                        missing_columns = [str(column) for column in variable_map.values() if column not in headers_available]
                        if missing_columns:
                            raise ValueError("Colunas nÃ£o encontradas na planilha: " + ", ".join(missing_columns))
                        preview_values = resolve_official_variables(variable_specs, variable_map, rows[0] if rows else {})
                        preview_text = str(official_template["body_text"] or "")
                        for position, value in enumerate(preview_values["body"], start=1):
                            preview_value = str(value.get("text") or "") if isinstance(value, dict) else str(value)
                            parameter_name = str(value.get("parameter_name") or "") if isinstance(value, dict) else ""
                            if parameter_name:
                                preview_text = preview_text.replace("{{" + parameter_name + "}}", preview_value)
                            preview_text = preview_text.replace("{{" + str(position) + "}}", preview_value)
                        campaign_status = "rascunho"
                    else:
                        if interaction_buttons and interaction_mode == "numbered":
                            message = message + ("\n\n" if message else "") + "\n".join(f"{index}. {label}" for index, label in enumerate(interaction_buttons, start=1))
                        preview_text = message
                        campaign_status = "importada"
                    schedule = {"interval_seconds": interval_seconds, "max_sends": max_sends, "scheduled_at": scheduled_at, "channel_ids": channel_ids, "strategy": channel_strategy}
                    cursor = connection.execute("""INSERT INTO whatsapp_campaigns(nome,origem,status,channel_id,total,enviados,falhas,created_at,updated_at,mensagem,mensagens_json,schedule_json,official_template_id,template_language,variable_map_json,interval_seconds,max_sends,scheduled_at,preview_text,send_mode,channel_strategy,channel_ids_json,buttons_json,interaction_mode,poll_question)
                                                   VALUES(?,?,?,?,?,0,0,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (campaign_name, filename, campaign_status, channel_id, len(rows), timestamp, timestamp, message, json.dumps([message], ensure_ascii=False), json.dumps(schedule, ensure_ascii=False), official_template_id, official_template["language"] if official_template else None, json.dumps(variable_map, ensure_ascii=False), interval_seconds, max_sends, scheduled_at, preview_text, send_mode, channel_strategy, json.dumps(channel_ids), json.dumps(interaction_buttons, ensure_ascii=False), interaction_mode, poll_question))
                    campaign_id = cursor.lastrowid
                    suppressed_count = 0
                    if campaign_media_path:
                        connection.execute("UPDATE whatsapp_campaigns SET media_path=? WHERE id=?", (campaign_media_path, campaign_id))
                    for row in rows:
                        original_phone = pick(row,"telefone","contato","contacto","phone","celular","whatsapp")
                        normalized_phone = normalize_brazil_phone(original_phone)
                        suppression = connection.execute(
                            """SELECT * FROM meta_phone_suppressions
                                WHERE normalized_phone=? AND active=1
                                  AND (suppression_type='permanent' OR blocked_until>?)""",
                            (normalized_phone, timestamp),
                        ).fetchone() if normalized_phone else None
                        item_status = "suprimido" if suppression else ("pendente" if normalized_phone else "ignorado")
                        suppression_error = suppression["reason"] if suppression else None
                        if suppression:
                            suppressed_count += 1
                        variables = resolve_official_variables(variable_specs, variable_map, row) if official_template else {
                            "nome": first_name(pick(row, "nome", "name")),
                            "message": personalize_message(message, row),
                        }
                        item_cursor = connection.execute("""INSERT INTO whatsapp_campaign_items(campaign_id,nome,cpf,telefone,matricula,convenio,margem,ultima_consulta,produto_interesse,observacoes,status,created_at,updated_at,original_phone,normalized_phone,variables_json,last_error)
                                              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (campaign_id, pick(row,"nome","name"), pick(row,"cpf"), format_phone(original_phone), pick(row,"matricula"), pick(row,"convenio"), pick(row,"margem"), pick(row,"ultima_consulta"), pick(row,"produto_interesse"), pick(row,"observacao","observacoes"), item_status, timestamp, timestamp, original_phone, normalized_phone, json.dumps(variables, ensure_ascii=False), suppression_error or (None if normalized_phone else "NÃºmero invÃ¡lido")))
                        if official_template:
                            attempt_channel_id = channel_ids[(item_cursor.lastrowid - 1) % len(channel_ids)] if channel_strategy == "rotation" else channel_id
                            attempt_template_id = official_template_id
                            if attempt_channel_id != channel_id:
                                equivalent = connection.execute("SELECT id FROM meta_official_templates WHERE channel_id=? AND name=? AND language=? AND upper(status)='APPROVED'", (attempt_channel_id, official_template["name"], official_template["language"])).fetchone()
                                if not equivalent:
                                    raise ValueError(f"O template {official_template['name']} nÃ£o estÃ¡ aprovado em todos os nÃºmeros selecionados.")
                                attempt_template_id = equivalent["id"]
                            connection.execute("""INSERT INTO meta_campaign_send_attempts(campaign_id,item_id,channel_id,template_id,original_phone,normalized_phone,variables_json,status,error_message,created_at,updated_at)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (campaign_id, item_cursor.lastrowid, attempt_channel_id, attempt_template_id, original_phone, normalized_phone, json.dumps(variables, ensure_ascii=False), item_status, suppression_error or (None if normalized_phone else "NÃºmero invÃ¡lido"), timestamp, timestamp))
                    if official_template:
                        audit_meta(connection, "campaign_create", "sucesso", f"Campanha oficial criada em rascunho com {len(rows)} contatos.", user["id"], channel_id, campaign_id)
                filtered_note = f" {suppressed_count} nÃºmero(s) nÃ£o entregÃ¡vel(is) foram excluÃ­dos antes do disparo." if suppressed_count else ""
                notice = (f"Campanha oficial preparada com {len(rows)} contatos. Confira a prÃ©via antes de iniciar." if official_template else f"Campanha importada com {len(rows)} contatos.") + filtered_note
                self.send_html(dashboard(notice, username=user["username"])); return
            if path == "/campaign/media":
                fields, files = parse_multipart(self)
                campaign_id = int(fields.get("campaign_id", "0") or 0)
                upload = files.get("media")
                if not upload or not upload[1]:
                    raise ValueError("Selecione uma imagem ou vÃ­deo.")
                original_name, media_content = upload
                suffix = Path(original_name).suffix.lower()
                if suffix not in ALLOWED_MEDIA:
                    raise ValueError("Formato de imagem ou vÃ­deo nÃ£o permitido.")
                saved_name = f"campaign_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}{suffix}"
                destination = UPLOAD_DIR / saved_name
                destination.write_bytes(media_content)
                with db() as connection:
                    campaign = connection.execute("SELECT id FROM whatsapp_campaigns WHERE id=?", (campaign_id,)).fetchone()
                    if not campaign:
                        raise ValueError("Campanha nÃ£o encontrada.")
                    connection.execute("UPDATE whatsapp_campaigns SET media_path=?,updated_at=? WHERE id=?", (str(destination), now(), campaign_id))
                self.redirect("/#envios"); return
            if path == "/campaign/edit":
                fields, files = parse_multipart(self)
                campaign_id = int(fields.get("campaign_id", "0") or 0)
                campaign_name = fields.get("nome", "").strip()
                base_message = fields.get("mensagem", "").strip()
                interval_seconds = max(1, int(fields.get("interval_seconds", "30") or 30))
                max_sends = int(fields.get("max_sends", "0") or 0) or None
                interaction_buttons = [line.strip() for line in fields.get("buttons", "").splitlines() if line.strip()]
                interaction_mode = fields.get("interaction_mode", "numbered").strip().lower()
                poll_question = fields.get("poll_question", "Como deseja continuar?").strip() or "Como deseja continuar?"
                if interaction_mode not in ("numbered", "poll", "official"):
                    raise ValueError("Escolha opÃ§Ãµes numeradas, enquete ou botÃµes da API Oficial.")
                if interaction_mode == "poll" and len(interaction_buttons) < 2:
                    raise ValueError("A enquete precisa de pelo menos duas opÃ§Ãµes.")
                if not campaign_name or not base_message:
                    raise ValueError("Informe o nome e a mensagem da campanha.")
                message_with_options = base_message + (("\n\n" + "\n".join(f"{index}. {label}" for index, label in enumerate(interaction_buttons, 1))) if interaction_buttons and interaction_mode == "numbered" else "")
                media_path = None
                upload = files.get("media")
                if upload and upload[1]:
                    original_name, media_content = upload
                    suffix = Path(original_name).suffix.lower()
                    if suffix not in ALLOWED_MEDIA:
                        raise ValueError("Formato de imagem ou vÃ­deo nÃ£o permitido.")
                    saved_name = f"campaign_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}{suffix}"
                    destination = UPLOAD_DIR / saved_name
                    destination.write_bytes(media_content)
                    media_path = str(destination)
                with db() as connection:
                    campaign = connection.execute("SELECT * FROM whatsapp_campaigns WHERE id=?", (campaign_id,)).fetchone()
                    if not campaign:
                        raise ValueError("Campanha nÃ£o encontrada.")
                    new_status = "pausada" if str(campaign["status"] or "").lower() == "em processamento" else campaign["status"]
                    connection.execute("UPDATE whatsapp_campaigns SET nome=?,mensagem=?,preview_text=?,buttons_json=?,interaction_mode=?,poll_question=?,interval_seconds=?,max_sends=?,status=?,media_path=COALESCE(?,media_path),updated_at=? WHERE id=?",
                                       (campaign_name, message_with_options, message_with_options, json.dumps(interaction_buttons, ensure_ascii=False), interaction_mode, poll_question, interval_seconds, max_sends, new_status, media_path, now(), campaign_id))
                    items = connection.execute("SELECT id,nome,variables_json FROM whatsapp_campaign_items WHERE campaign_id=? AND status IN ('pendente','processando')", (campaign_id,)).fetchall()
                    for item in items:
                        name_value = first_name(item["nome"])
                        personalized = message_with_options.replace("{{nome}}", name_value).replace("{{NOME}}", name_value)
                        variables = json.loads(item["variables_json"] or "{}")
                        if not isinstance(variables, dict): variables = {}
                        variables.update({"nome": name_value, "message": personalized})
                        connection.execute("UPDATE whatsapp_campaign_items SET status='pendente',variables_json=?,updated_at=? WHERE id=?", (json.dumps(variables, ensure_ascii=False), now(), item["id"]))
                self.redirect("/#envios"); return
            if path == "/campaign/action":
                if user["role"] != "master":
                    self.send_html(dashboard("Somente o usuÃ¡rio master pode controlar campanhas oficiais.", True, user["username"]), 403); return
                fields = self.form_fields()
                campaign_id = int(fields.get("campaign_id", "0") or 0)
                action = fields.get("action", "")
                with db() as connection:
                    campaign = connection.execute("SELECT * FROM whatsapp_campaigns WHERE id=?", (campaign_id,)).fetchone()
                    if not campaign:
                        raise ValueError("Campanha nÃ£o encontrada.")
                    current = str(campaign["status"] or "").lower()
                    if action == "start" and current in ("rascunho", "agendada", "importada"):
                        scheduled = campaign["scheduled_at"]
                        new_status = "agendada" if scheduled and datetime.fromisoformat(scheduled) > datetime.now() else "em processamento"
                        connection.execute("UPDATE whatsapp_campaigns SET status=?,started_at=CASE WHEN ?='em processamento' THEN COALESCE(started_at,?) ELSE started_at END,updated_at=? WHERE id=?", (new_status, new_status, now(), now(), campaign_id))
                        audit_meta(connection, "campaign_start", "sucesso", f"Campanha alterada para {new_status}.", user["id"], campaign["channel_id"], campaign_id)
                    elif action == "pause" and current == "em processamento":
                        new_status = "pausada"
                        connection.execute("UPDATE whatsapp_campaigns SET status=?,updated_at=? WHERE id=?", (new_status, now(), campaign_id))
                        audit_meta(connection, "campaign_pause", "sucesso", "Campanha pausada.", user["id"], campaign["channel_id"], campaign_id)
                    elif action == "continue" and current == "pausada":
                        new_status = "em processamento"
                        connection.execute("UPDATE whatsapp_campaigns SET status=?,updated_at=? WHERE id=?", (new_status, now(), campaign_id))
                        audit_meta(connection, "campaign_resume", "sucesso", "Campanha retomada.", user["id"], campaign["channel_id"], campaign_id)
                    elif action == "cancel" and current in ("rascunho", "agendada", "em processamento", "pausada"):
                        new_status = "cancelada"
                        connection.execute("UPDATE whatsapp_campaigns SET status=?,cancel_requested=1,completed_at=?,updated_at=? WHERE id=?", (new_status, now(), now(), campaign_id))
                        connection.execute("UPDATE meta_campaign_send_attempts SET status='cancelado',updated_at=? WHERE campaign_id=? AND status IN ('pendente','processando')", (now(), campaign_id))
                        connection.execute("UPDATE whatsapp_campaign_items SET status='cancelado',updated_at=? WHERE campaign_id=? AND status IN ('pendente','processando')", (now(), campaign_id))
                        audit_meta(connection, "campaign_cancel", "sucesso", "Campanha cancelada; itens nÃ£o enviados foram cancelados.", user["id"], campaign["channel_id"], campaign_id)
                    else:
                        raise ValueError("Esta aÃ§Ã£o nÃ£o Ã© vÃ¡lida para o estado atual da campanha.")
                if action in ("start", "continue"):
                    start_web_campaign_worker() if campaign["send_mode"] == "web" else start_campaign_worker()
                # Keep campaign controls visible after start/pause/resume/cancel.
                # The fragment makes the dashboard reopen the Envios view.
                self.redirect("/#envios"); return
            if path == "/campaign/delete":
                if user["role"] != "master":
                    self.send_html(dashboard("Somente o usuÃ¡rio master pode excluir campanhas.", True, user["username"]), 403); return
                fields = self.form_fields()
                campaign_id = int(fields.get("campaign_id", "0") or 0)
                with db() as connection:
                    campaign = connection.execute("SELECT id,nome,media_path FROM whatsapp_campaigns WHERE id=?", (campaign_id,)).fetchone()
                    if not campaign:
                        raise ValueError("Campanha nÃ£o encontrada.")
                    connection.execute("DELETE FROM meta_campaign_send_attempts WHERE campaign_id=?", (campaign_id,))
                    connection.execute("DELETE FROM whatsapp_campaign_items WHERE campaign_id=?", (campaign_id,))
                    connection.execute("DELETE FROM meta_audit_logs WHERE campaign_id=?", (campaign_id,))
                    connection.execute("DELETE FROM whatsapp_campaigns WHERE id=?", (campaign_id,))
                media_path = Path(campaign["media_path"]) if campaign["media_path"] else None
                if media_path and media_path.is_file() and UPLOAD_DIR in media_path.resolve().parents:
                    media_path.unlink()
                self.redirect("/#envios"); return
            if path == "/send":
                fields, files = parse_multipart(self)
                uploaded = files.get("media")
                phone, message = format_phone(fields.get("phone", "")), fields.get("message", "").strip()
                channel_id = int(fields.get("channel_id", "0") or 0) or None
                template_id = int(fields.get("template_id", "0") or 0) or None
                if template_id and not message:
                    with db() as connection:
                        template = connection.execute("SELECT mensagem FROM whatsapp_templates WHERE id=? AND active=1", (template_id,)).fetchone()
                    if template:
                        message = template["mensagem"] or ""
                media_path, media_name = "", ""
                if uploaded and uploaded[1]:
                    original_name, content = uploaded
                    suffix = Path(original_name).suffix.lower()
                    if suffix not in ALLOWED_MEDIA:
                        raise ValueError("Formato de arquivo nÃ£o permitido.")
                    media_name = f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}{suffix}"
                    destination = UPLOAD_DIR / media_name
                    destination.write_bytes(content)
                    media_path = str(destination)
                if not message and not media_path:
                    raise ValueError("Digite uma mensagem ou selecione uma mÃ­dia.")
                channel = None
                if channel_id:
                    with db() as connection:
                        channel = connection.execute("SELECT * FROM whatsapp_channels WHERE id=? AND active=1", (channel_id,)).fetchone()
                if channel and channel["mode"] == "meta":
                    if media_path:
                        raise ValueError("O envio de mÃ­dia pela API Oficial ainda nÃ£o estÃ¡ configurado. Envie texto ou escolha um canal por QR Code.")
                    result = send_meta(channel, phone, message)
                    modality = "meta"
                elif channel and channel["mode"] == "manual":
                    raise ValueError("Escolha um canal por QR Code ou pela API Oficial para realizar o envio automÃ¡tico.")
                else:
                    result = send(
                        phone,
                        message,
                        media_path,
                        channel["web_cdp_port"] if channel else None,
                        channel["web_profile_dir"] if channel else None,
                    )
                    modality = "web"
                with db() as connection:
                    imported = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='whatsapp_messages'").fetchone()
                    if imported:
                        timestamp = now()
                        connection.execute("""INSERT INTO whatsapp_messages(client_id,user_id,channel_id,direction,modalidade,status,telefone,telefone_conectado,template_id,mensagem,provider_message_id,raw_json,created_at,updated_at)
                                              VALUES(NULL,NULL,?,'outbound',?,?,?,NULL,?,?,?,?,?,?)""",
                                           (channel_id, modality, result.get("status", "erro"), phone, template_id, message, result.get("provider_message_id"), json.dumps(result, ensure_ascii=False), timestamp, timestamp))
                    else:
                        connection.execute("INSERT INTO messages(phone,message,media_name,status,error,created_at) VALUES(?,?,?,?,?,?)", (phone, message, media_name, result.get("status", "erro"), result.get("error", ""), now()))
                text = "Mensagem enviada com sucesso." if result.get("ok") else f"Envio nÃ£o concluÃ­do: {result.get('error') or result.get('status')}"
                self.send_html(dashboard(text, not result.get("ok")))
                return
            self.send_html(layout("<h1>PÃ¡gina nÃ£o encontrada</h1>"), 404)
        except ValueError as exc:
            self.send_html(dashboard(str(exc), True), 400)
        except Exception as exc:
            traceback.print_exc()
            self.send_html(dashboard(f"Falha interna: {exc}", True), 500)

    def log_message(self, fmt, *args):
        print(f"[{now()}] {self.address_string()} {fmt % args}")


if __name__ == "__main__":
    initialize()
    with db() as startup_connection:
        active_modes = {row["send_mode"] for row in startup_connection.execute("SELECT DISTINCT send_mode FROM whatsapp_campaigns WHERE status='em processamento'")}
    if "meta" in active_modes:
        start_campaign_worker()
    if "web" in active_modes:
        start_web_campaign_worker()
    Thread(target=scheduled_campaign_loop, name="1clik-scheduler", daemon=True).start()
    Thread(target=meta_webhook_relay_loop, name="1clik-meta-webhook-relay", daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
    print(f"1Clik ativo em {url}")
    if "--no-browser" not in sys.argv:
        Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

