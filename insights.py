import json
import re
import sqlite3
from pathlib import Path


OMEGA_DB_PATH = Path(__file__).resolve().parent.parent / "omega_clientes.db"

DEFAULT_OPPORTUNITIES = (
    "INSS (Promobank)",
    "SIAPE",
    "Prefeitura de Marabá",
    "Prefeitura de Belém",
    "Governo do Pará",
    "CLT / FGTS",
    "Cartão Benefício",
    "Cartão Consignado",
    "Outros convênios cadastrados",
)


def _digits(value):
    return "".join(character for character in str(value or "") if character.isdigit())


def _money(value):
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = re.sub(r"[^\d,.-]", "", str(value)).strip()
    if not text:
        return 0.0
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return 0.0


def _category(convenio, gestora="", product=""):
    source = " ".join((str(convenio or ""), str(gestora or ""), str(product or ""))).upper()
    if "INSS" in source or "PROMOBANK" in source:
        return "INSS (Promobank)"
    if "SIAPE" in source:
        return "SIAPE"
    if "MARAB" in source:
        return "Prefeitura de Marabá"
    if "BEL" in source and ("PREF" in source or "BELÉM" in source or "BELEM" in source):
        return "Prefeitura de Belém"
    if "GOVERNO" in source and ("PARÁ" in source or "PARA" in source):
        return "Governo do Pará"
    if "CLT" in source or "FGTS" in source:
        return "CLT / FGTS"
    if "CART" in source and "BENEF" in source:
        return "Cartão Benefício"
    if "CART" in source and "CONSIGN" in source:
        return "Cartão Consignado"
    return str(convenio or gestora or "Outros convênios cadastrados").strip()


def campaign_summaries(connection):
    campaigns = connection.execute(
        "SELECT id,nome,origem,status,total,created_at,updated_at,started_at,completed_at,created_by_user_id FROM whatsapp_campaigns ORDER BY id DESC"
    ).fetchall()
    result = []
    for campaign in campaigns:
        items = connection.execute(
            "SELECT id,nome,telefone,status,message_id,last_error,created_at,updated_at FROM whatsapp_campaign_items WHERE campaign_id=?",
            (campaign["id"],),
        ).fetchall()
        phones = {_digits(item["telefone"]) for item in items if _digits(item["telefone"])}
        messages = connection.execute(
            "SELECT id,telefone,direction,status,mensagem,created_at FROM whatsapp_messages WHERE created_at>=? ORDER BY id DESC",
            (campaign["created_at"],),
        ).fetchall()
        related = [message for message in messages if _digits(message["telefone"]) in phones]
        inbound_by_phone = {}
        for message in related:
            if str(message["direction"] or "").lower() in ("in", "inbound"):
                inbound_by_phone.setdefault(_digits(message["telefone"]), []).append(message)
        responses = set(inbound_by_phone)
        statuses = [str(message["status"] or "").lower() for message in related]
        sent = sum(1 for item in items if str(item["status"] or "").lower() in ("enviado", "enviado_web"))
        errors = sum(1 for item in items if any(word in str(item["status"] or "").lower() for word in ("falh", "erro")))
        blocked = sum(1 for status in statuses if "bloque" in status)
        not_delivered = sum(1 for item in items if str(item["status"] or "").lower() == "suprimido")
        not_delivered += sum(1 for status in statuses if status == "failed" or any(word in status for word in ("nao_entreg", "não_entreg", "numero_sem_whatsapp", "undeliver")))
        read = sum(1 for status in statuses if status in ("lido", "lida", "read"))
        responded = len(responses)
        awaiting = max(0, sent - responded - blocked - not_delivered)
        total = int(campaign["total"] or len(items))
        handled_rows = connection.execute(
            "SELECT campaign_item_id,handled,handled_at FROM conversation_handled WHERE campaign_id=?",
            (campaign["id"],),
        ).fetchall()
        handled_by_item = {row["campaign_item_id"]: row for row in handled_rows}
        all_responses_handled = bool(responses)
        for item in items:
            phone = _digits(item["telefone"])
            if phone not in inbound_by_phone:
                continue
            latest_inbound_at = inbound_by_phone[phone][0]["created_at"] or ""
            handled_row = handled_by_item.get(item["id"])
            if not handled_row or not handled_row["handled"] or (handled_row["handled_at"] or "") < latest_inbound_at:
                all_responses_handled = False
                break
        # Conversation colors: orange=no reply, green=reply requiring attention,
        # purple=every received reply was answered by the operator.
        color = "handled" if all_responses_handled else "replied" if responses else "awaiting-reply"
        metrics = {
            "sent": sent,
            "responded": responded,
            "awaiting": awaiting,
            "error": errors,
            "blocked": blocked,
            "not_delivered": not_delivered,
            "read": read,
        }
        result.append({
            **dict(campaign),
            "total": total,
            "color": color,
            "metrics": {key: {"count": value, "percent": round(value * 100 / total, 1) if total else 0} for key, value in metrics.items()},
        })
    return result


def campaign_contacts(connection, campaign_id, metric, search="", limit=100, offset=0):
    campaign = connection.execute("SELECT * FROM whatsapp_campaigns WHERE id=?", (campaign_id,)).fetchone()
    if not campaign:
        return [], 0
    items = connection.execute(
        """SELECT i.id,i.nome,i.telefone,i.status,i.last_error,i.variables_json,i.normalized_phone,
                  i.created_at,i.updated_at,
                  COALESCE(h.handled,0) AS handled,h.handled_at,
                  s.error_code AS suppression_code,s.reason AS suppression_reason,
                  s.suppression_type,s.blocked_until,s.active AS suppression_active
             FROM whatsapp_campaign_items i
             LEFT JOIN conversation_handled h ON h.campaign_item_id=i.id
             LEFT JOIN meta_phone_suppressions s ON s.normalized_phone=i.normalized_phone
            WHERE i.campaign_id=? ORDER BY i.id""",
        (campaign_id,),
    ).fetchall()
    messages = connection.execute(
        "SELECT telefone,direction,status,mensagem,created_at FROM whatsapp_messages WHERE created_at>=? ORDER BY id DESC",
        (campaign["created_at"],),
    ).fetchall()
    by_phone = {}
    for message in messages:
        by_phone.setdefault(_digits(message["telefone"]), []).append(message)
    selected = []
    query = search.casefold().strip()
    for item in items:
        linked = by_phone.get(_digits(item["telefone"]), [])
        msg_statuses = [str(message["status"] or "").lower() for message in linked]
        responded = any(str(message["direction"] or "").lower() in ("in", "inbound") for message in linked)
        item_status = str(item["status"] or "").lower()
        matches = {
            "sent": item_status in ("enviado", "enviado_web"),
            "responded": responded,
            "awaiting": item_status in ("enviado", "enviado_web") and not responded and not any("bloque" in s or "entreg" in s and ("nao" in s or "não" in s) for s in msg_statuses),
            "error": "falh" in item_status or "erro" in item_status,
            "blocked": any("bloque" in status for status in msg_statuses),
            "not_delivered": item_status == "suprimido" or bool(item["suppression_active"]) or any(status == "failed" or any(word in status for word in ("nao_entreg", "não_entreg", "numero_sem_whatsapp", "undeliver")) for status in msg_statuses),
            "read": any(status in ("lido", "lida", "read") for status in msg_statuses),
        }
        if not matches.get(metric, False):
            continue
        latest = linked[0] if linked else None
        latest_inbound = next(
            (message for message in linked if str(message["direction"] or "").lower() in ("in", "inbound")),
            None,
        )
        effectively_handled = bool(
            item["handled"]
            and latest_inbound
            and (item["handled_at"] or "") >= (latest_inbound["created_at"] or "")
        )
        row = {
            **dict(item),
            "handled": effectively_handled,
            "message": latest["mensagem"] if latest else "",
            "message_status": latest["status"] if latest else "",
            "message_at": latest["created_at"] if latest else "",
        }
        if row.get("suppression_reason"):
            row["last_error"] = row["suppression_reason"]
        try:
            variables = json.loads(item["variables_json"] or "{}")
            body_values = variables.get("body", []) if isinstance(variables, dict) else []
            row["name_used"] = body_values[0] if body_values else ""
        except (ValueError, TypeError):
            row["name_used"] = ""
        if query and query not in f"{row['nome']} {row['telefone']} {row['message']} {row['status']}".casefold():
            continue
        selected.append(row)
    return selected[offset:offset + min(limit, 200)], len(selected)


def opportunity_data(category=None, search="", limit=100, offset=0):
    if not OMEGA_DB_PATH.exists():
        return {"cards": [], "clients": [], "total": 0, "source_available": False}
    connection = sqlite3.connect(f"file:{OMEGA_DB_PATH.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """SELECT r.*,c.nome AS client_name,c.cpf AS client_cpf,c.contato,
                  COALESCE(g.nome_convenio,g.convenio_vinculado,g.nome,'') AS gestora_nome
             FROM consulta_margem_results r
             LEFT JOIN clients c ON c.id=r.client_id
             LEFT JOIN gestoras g ON g.id=r.gestora_id
            ORDER BY r.consulted_at DESC,r.id DESC"""
    ).fetchall()
    configured = [row["label"] for row in connection.execute("SELECT label FROM convenios WHERE active=1 ORDER BY label")]
    connection.close()
    latest = {}
    for row in rows:
        unique = (row["client_id"] or row["cpf_masked"] or row["id"], row["gestora_id"] or row["convenio"] or "")
        latest.setdefault(unique, row)
    grouped = {}
    normalized = []
    for row in latest.values():
        item = dict(row)
        item["category"] = _category(row["convenio"], row["gestora_nome"], row["status_margem"])
        values = [_money(row["margem_disponivel"]), _money(row["margem_cartao"]), _money(row["margem_beneficio"]), _money(row["valor_disponivel"])]
        item["margin_value"] = max(values)
        item["positive"] = item["margin_value"] > 0
        normalized.append(item)
        grouped.setdefault(item["category"], []).append(item)
    names = list(DEFAULT_OPPORTUNITIES)
    for name in configured + sorted(grouped):
        mapped = _category(name)
        if mapped not in names:
            names.append(mapped)
    cards = []
    for name in names:
        entries = grouped.get(name, [])
        values = [item["margin_value"] for item in entries if item["positive"]]
        cards.append({
            "name": name,
            "consultations": len(entries),
            "positive": len(values),
            "total_margin": round(sum(values), 2),
            "average_margin": round(sum(values) / len(values), 2) if values else 0,
            "updated_at": max((item["consulted_at"] or "" for item in entries), default=""),
        })
    if category:
        normalized = [item for item in normalized if item["category"] == category]
    query = search.casefold().strip()
    if query:
        normalized = [item for item in normalized if query in f"{item.get('client_name') or item.get('nome_cliente') or ''} {item.get('client_cpf') or item.get('cpf_masked') or ''} {item.get('matricula') or ''}".casefold()]
    return {"cards": cards, "clients": normalized[offset:offset + min(limit, 200)], "total": len(normalized), "source_available": True}
