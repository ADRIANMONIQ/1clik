import json

import app
from meta_api import CredentialVault, MetaClient


CHANNEL_ID = 12
TEMPLATE_NAME = "omega_maraba_consulta_v1"
LANGUAGE = "pt_BR"
BODY_TEXT = """Olá, {{1}}! 😊
Tudo bem?
Meu nome é Adrian Monique e faço parte da equipe da Ômega Créditos.

Temos ótimas condições de consignação para servidores efetivos da Prefeitura de Marabá.

Para sua segurança, seguem nossos canais oficiais:
🌐 omegacreditos.com
📷 Instagram: @omega.credigital

Caso você autorize, faço uma consulta gratuita e sem compromisso.
Posso verificar para você?"""
BUTTONS = [
    {"type": "QUICK_REPLY", "text": "Sim, tenho interesse"},
    {"type": "QUICK_REPLY", "text": "Não, obrigada"},
    {"type": "QUICK_REPLY", "text": "Bloquear contato"},
]


with app.db() as connection:
    channel = connection.execute(
        "SELECT * FROM whatsapp_channels WHERE id=? AND mode='meta' AND active=1",
        (CHANNEL_ID,),
    ).fetchone()

if not channel:
    raise SystemExit("Canal oficial ativo não encontrado.")

token = CredentialVault(app.META_KEY_PATH).decrypt(channel["meta_access_token_enc"])
client = MetaClient(token, channel["meta_api_version"] or app.DEFAULT_GRAPH_API_VERSION)
payload = {
    "name": TEMPLATE_NAME,
    "language": LANGUAGE,
    "category": "MARKETING",
    "components": [
        {
            "type": "BODY",
            "text": BODY_TEXT,
            "example": {"body_text": [["Adrian"]]},
        },
        {"type": "BUTTONS", "buttons": BUTTONS},
    ],
}

response, http_status = client.request(
    "POST",
    f"{channel['meta_waba_id']}/message_templates",
    payload=payload,
)
template_status = str(response.get("status") or "PENDING").upper()
template_id = str(response.get("id") or "")
synced_at = app.now()

with app.db() as connection:
    connection.execute(
        """INSERT INTO meta_official_templates(
               channel_id,meta_template_id,name,language,category,status,
               header_text,body_text,footer_text,buttons_json,components_json,
               variables_json,synced_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(channel_id,name,language) DO UPDATE SET
               meta_template_id=excluded.meta_template_id,
               category=excluded.category,status=excluded.status,
               body_text=excluded.body_text,buttons_json=excluded.buttons_json,
               components_json=excluded.components_json,
               variables_json=excluded.variables_json,synced_at=excluded.synced_at""",
        (
            CHANNEL_ID,
            template_id,
            TEMPLATE_NAME,
            LANGUAGE,
            str(response.get("category") or "MARKETING"),
            template_status,
            "",
            BODY_TEXT,
            "",
            json.dumps(BUTTONS, ensure_ascii=False),
            json.dumps(payload["components"], ensure_ascii=False),
            json.dumps([{"key": "BODY:1", "component": "body", "position": 1}], ensure_ascii=False),
            synced_at,
        ),
    )
    app.audit_meta(
        connection,
        "template_create",
        "sucesso",
        f"Modelo {TEMPLATE_NAME} enviado à Meta.",
        10,
        CHANNEL_ID,
        technical={
            "http_status": http_status,
            "template_id": template_id,
            "status": template_status,
        },
    )
    connection.commit()

print(
    json.dumps(
        {
            "http_status": http_status,
            "template_id": template_id,
            "name": TEMPLATE_NAME,
            "language": LANGUAGE,
            "category": str(response.get("category") or "MARKETING"),
            "status": template_status,
        },
        ensure_ascii=False,
    )
)
