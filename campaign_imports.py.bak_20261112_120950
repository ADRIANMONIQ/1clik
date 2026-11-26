import io
import json
import re
import unicodedata
from datetime import datetime


VALID_SOURCES = {"promobank", "promosys"}


def timestamp():
    return datetime.now().isoformat(timespec="seconds")


def initialize_schema(connection):
    connection.executescript("""
    CREATE TABLE IF NOT EXISTS campaign_import_batches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_user_id INTEGER,
        source TEXT NOT NULL,
        campaign_name TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'aguardando',
        leads_total INTEGER NOT NULL DEFAULT 0,
        contacts_total INTEGER NOT NULL DEFAULT 0,
        rows_json TEXT NOT NULL DEFAULT '[]',
        error_message TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        completed_at TEXT,
        copied_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_campaign_import_source
        ON campaign_import_batches(source, id DESC);
    CREATE TABLE IF NOT EXISTS campaign_import_selections (
        user_id INTEGER PRIMARY KEY,
        batch_id INTEGER NOT NULL,
        updated_at TEXT NOT NULL
    );
    """)


def _key(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _value(row, *names):
    normalized = {_key(key): value for key, value in row.items()}
    for name in names:
        value = normalized.get(_key(name))
        if value not in (None, ""):
            return str(value).strip()
    return ""


def normalize_phone(value):
    digits = re.sub(r"\D", "", str(value or ""))
    if digits.startswith("55") and len(digits) in (12, 13):
        digits = digits[2:]
    if len(digits) not in (10, 11):
        return ""
    return "55" + digits


def normalize_rows(source, campaign_name, rows):
    source = str(source or "").strip().lower()
    if source not in VALID_SOURCES:
        raise ValueError("Origem deve ser promobank ou promosys.")
    output = []
    seen = set()
    for original in rows or []:
        if not isinstance(original, dict):
            continue
        normalized_original = {_key(key): value for key, value in original.items()}
        contact_values = [
            str(value).strip() for key, value in normalized_original.items()
            if value not in (None, "") and (
                key in {"contatos", "contato", "telefone", "telefones", "celular", "whatsapp", "phone"}
                or key.startswith(("contato_", "telefone_", "celular_", "whatsapp_", "phone_"))
            )
        ]
        contacts = ";".join(contact_values)
        candidates = re.split(r"[;,|\n\r/]+", contacts) if contacts else []
        for candidate in candidates:
            phone = normalize_phone(candidate)
            if not phone or phone in seen:
                continue
            seen.add(phone)
            row = {str(key): "" if value is None else value for key, value in original.items()}
            row.update({
                "nome": _value(original, "nome", "name", "cliente"),
                "cpf": _value(original, "cpf"),
                "telefone": phone,
                "matricula": _value(original, "matricula", "beneficio", "nb"),
                "convenio": _value(original, "convenio", "especie"),
                "margem": _value(original, "margem", "margem_emprestimo"),
                "produto_interesse": _value(
                    original, "produto_interesse", "possibilidade_de_portabilidade",
                    "possibilidade_de_refinanciamento",
                ),
                "observacoes": _value(original, "observacao", "observacoes"),
                "origem": source,
                "campanha_origem": str(campaign_name or "").strip(),
            })
            output.append(row)
    return output


def save_batch(connection, source, campaign_name, rows, leads_total=None, status="concluida", error_message="", owner_user_id=None):
    normalized = normalize_rows(source, campaign_name, rows)
    created = timestamp()
    leads = int(leads_total if leads_total is not None else len(rows or []))
    cursor = connection.execute(
        """INSERT INTO campaign_import_batches
           (owner_user_id,source,campaign_name,status,leads_total,contacts_total,rows_json,error_message,created_at,updated_at,completed_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (owner_user_id, source.lower(), str(campaign_name).strip(), status, leads, len(normalized),
         json.dumps(normalized, ensure_ascii=False), str(error_message or "") or None,
         created, created, created if status == "concluida" else None),
    )
    return cursor.lastrowid, normalized


def batch_rows(batch):
    try:
        rows = json.loads(batch["rows_json"] or "[]")
    except (TypeError, json.JSONDecodeError):
        rows = []
    return rows if isinstance(rows, list) else []


def excel_bytes(batch):
    from openpyxl import Workbook

    rows = batch_rows(batch)
    headers = ["NOME", "CPF", "CONTATO", "MATRICULA", "CONVENIO", "MARGEM", "ORIGEM", "CAMPANHA_ORIGEM"]
    keys = ["nome", "cpf", "telefone", "matricula", "convenio", "margem", "origem", "campanha_origem"]
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Contatos"
    sheet.append(headers)
    for row in rows:
        sheet.append([row.get(key, "") for key in keys])
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for column in sheet.columns:
        letter = column[0].column_letter
        sheet.column_dimensions[letter].width = min(45, max(12, max(len(str(cell.value or "")) for cell in column) + 2))
    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def contacts_excel_bytes(batch):
    """Generate the minimal dispatch workbook: exactly one phone per row."""
    from openpyxl import Workbook

    rows = batch_rows(batch)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Contatos"
    sheet.append(["NOME", "CONTATO"])
    for row in rows:
        sheet.append([row.get("nome", ""), row.get("telefone", "")])
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    sheet.column_dimensions["A"].width = 38
    sheet.column_dimensions["B"].width = 20
    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()
