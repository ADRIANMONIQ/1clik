import json
import shutil
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

from whatsapp_bridge import check_number, check_numbers_via_group, status


_WORKER_LOCK = threading.Lock()
PHONE_ALIASES = {"telefone", "contato", "contacto", "phone", "celular", "whatsapp", "numero", "número"}


def now():
    return datetime.now().isoformat(timespec="seconds")


def digits(value):
    value = str(value or "").split(".")[0]
    result = "".join(character for character in value if character.isdigit())
    if result.startswith("55") and len(result) in (12, 13):
        result = result[2:]
    return result


def initialize_schema(connection):
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS whatsapp_test_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT NOT NULL,
            source_path TEXT NOT NULL,
            output_path TEXT,
            sheet_name TEXT NOT NULL,
            phone_column INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'aguardando',
            total INTEGER NOT NULL DEFAULT 0,
            processed INTEGER NOT NULL DEFAULT 0,
            confirmed INTEGER NOT NULL DEFAULT 0,
            rejected INTEGER NOT NULL DEFAULT 0,
            inconclusive INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS whatsapp_test_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            row_number INTEGER NOT NULL,
            phone TEXT NOT NULL,
            normalized_phone TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pendente',
            reason TEXT,
            checked_at TEXT,
            UNIQUE(job_id,row_number)
        );
        CREATE INDEX IF NOT EXISTS idx_whatsapp_test_items_job_status
            ON whatsapp_test_items(job_id,status,id);
        CREATE TABLE IF NOT EXISTS whatsapp_test_contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            row_number INTEGER NOT NULL,
            contact_column INTEGER NOT NULL,
            client_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            normalized_phone TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pendente',
            reason TEXT,
            checked_at TEXT,
            UNIQUE(job_id,row_number,contact_column,normalized_phone)
        );
        CREATE INDEX IF NOT EXISTS idx_whatsapp_test_contacts_job_status
            ON whatsapp_test_contacts(job_id,status,id);
        """
    )


def find_name_column(headers):
    for index, header in enumerate(headers, start=1):
        normalized = str(header or "").strip().casefold()
        if normalized in ("nome", "name", "cliente", "nome do cliente", "nome_cliente"):
            return index
    return None


def find_phone_columns(headers):
    columns = []
    for index, header in enumerate(headers, start=1):
        normalized = str(header or "").strip().casefold()
        compact = "".join(character for character in normalized if character.isalnum())
        if (
            normalized in PHONE_ALIASES
            or any(alias in normalized for alias in ("telefone", "contato", "whatsapp", "celular"))
            or any(compact.startswith(alias) for alias in ("telefone", "contato", "whatsapp", "celular"))
        ):
            columns.append(index)
    return columns


def create_job(connection, source_name, source_path, channel_id):
    workbook = load_workbook(source_path, read_only=True, data_only=True)
    sheet = workbook.active
    sheet_name = sheet.title
    headers = [cell.value for cell in next(sheet.iter_rows(min_row=1, max_row=1))]
    name_column = find_name_column(headers)
    phone_columns = find_phone_columns(headers)
    if name_column is None:
        raise ValueError("A planilha precisa ter uma coluna NOME ou CLIENTE.")
    if not phone_columns:
        raise ValueError("A planilha precisa ter uma coluna TELEFONE, CONTATO, CELULAR ou WHATSAPP.")
    entries = []
    for row_number, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
        if not any(str(value or "").strip() for value in row):
            continue
        client_name = str(row[name_column - 1] or "").strip() if name_column <= len(row) else ""
        if not client_name:
            continue
        seen_in_row = set()
        for phone_column in phone_columns:
            phone = digits(row[phone_column - 1] if phone_column <= len(row) else "")
            normalized = "55" + phone if len(phone) in (10, 11) else phone
            if phone and normalized not in seen_in_row:
                entries.append((row_number, phone_column, client_name, phone, normalized))
                seen_in_row.add(normalized)
    workbook.close()
    if not entries:
        raise ValueError("Nenhum telefone válido foi encontrado na planilha.")
    timestamp = now()
    cursor = connection.execute(
        """INSERT INTO whatsapp_test_jobs
           (source_name,source_path,sheet_name,phone_column,channel_id,status,total,created_at,updated_at)
           VALUES(?,?,?,?,?,'aguardando',?,?,?)""",
        (source_name, str(source_path), sheet_name, phone_columns[0], channel_id, len(entries), timestamp, timestamp),
    )
    job_id = cursor.lastrowid
    connection.executemany(
        """INSERT INTO whatsapp_test_contacts
           (job_id,row_number,contact_column,client_name,phone,normalized_phone,status)
           VALUES(?,?,?,?,?,?,'pendente')""",
        [(job_id, row_number, phone_column, client_name, phone, normalized)
         for row_number, phone_column, client_name, phone, normalized in entries],
    )
    return job_id


def _export_confirmed(connection, job):
    source = Path(job["source_path"])
    output_dir = source.parent.parent / "whatsapp_test_outputs" if source.parent.name == "uploads" else source.parent / "whatsapp_test_outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{source.stem}_somente_com_whatsapp_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Contatos para 1Clik"
    sheet.append(["NOME", "CONTATO"])
    for contact in connection.execute(
        """SELECT client_name,phone FROM whatsapp_test_contacts
            WHERE job_id=? AND status='tem_whatsapp' ORDER BY row_number,contact_column,id""",
        (job["id"],),
    ):
        sheet.append([contact["client_name"], contact["phone"]])
    header_fill = PatternFill("solid", fgColor="F97316")
    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = Font(color="FFFFFF", bold=True)
    sheet.column_dimensions["A"].width = 38
    sheet.column_dimensions["B"].width = 20
    for cell in sheet["B"][1:]:
        cell.number_format = "@"
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    workbook.save(output)
    workbook.close()
    return output


def _process_job(db_path, job_id):
    connection = sqlite3.connect(db_path, timeout=10)
    connection.row_factory = sqlite3.Row
    initialize_schema(connection)
    job = connection.execute("SELECT * FROM whatsapp_test_jobs WHERE id=?", (job_id,)).fetchone()
    if not job:
        connection.close()
        return
    channel = connection.execute(
        "SELECT * FROM whatsapp_channels WHERE id=? AND mode='web' AND active=1", (job["channel_id"],)
    ).fetchone()
    if not channel:
        connection.execute(
            "UPDATE whatsapp_test_jobs SET status='aguardando_conexao',last_error=?,updated_at=? WHERE id=?",
            ("Conexão QR Code não encontrada.", now(), job_id),
        )
        connection.commit(); connection.close(); return
    active_campaign = connection.execute(
        "SELECT id FROM whatsapp_campaigns WHERE send_mode='web' AND status='em processamento' LIMIT 1"
    ).fetchone()
    if active_campaign:
        connection.execute(
            "UPDATE whatsapp_test_jobs SET status='aguardando',last_error=?,updated_at=? WHERE id=?",
            ("Aguardando a campanha por QR Code terminar.", now(), job_id),
        )
        connection.commit(); connection.close(); return
    state = status(timeout=2, cdp_port=channel["web_cdp_port"], profile_dir=channel["web_profile_dir"])
    if not state.get("connected"):
        connection.execute(
            "UPDATE whatsapp_test_jobs SET status='aguardando_conexao',last_error=?,updated_at=? WHERE id=?",
            ("Abra a conexão QR Code e aguarde o indicador ficar verde.", now(), job_id),
        )
        connection.commit(); connection.close(); return
    connection.execute(
        "UPDATE whatsapp_test_jobs SET status='processando',started_at=COALESCE(started_at,?),last_error=NULL,updated_at=? WHERE id=?",
        (now(), now(), job_id),
    )
    connection.commit()
    items = connection.execute(
        "SELECT * FROM whatsapp_test_contacts WHERE job_id=? AND status='pendente' ORDER BY id", (job_id,)
    ).fetchall()
    for item in items:
        result = check_number(
            item["normalized_phone"], cdp_port=channel["web_cdp_port"], profile_dir=channel["web_profile_dir"]
        )
        if result.get("exists") is True:
            item_status, reason = "tem_whatsapp", "Número localizado no WhatsApp Web."
        elif result.get("exists") is False:
            item_status, reason = "nao_tem_whatsapp", "Número não localizado no WhatsApp Web."
        else:
            item_status, reason = "inconclusivo", result.get("error") or "Não foi possível confirmar."
        checked_at = now()
        connection.execute(
            "UPDATE whatsapp_test_contacts SET status=?,reason=?,checked_at=? WHERE id=?",
            (item_status, reason, checked_at, item["id"]),
        )
        counts = connection.execute(
            """SELECT COUNT(*) AS processed,
                      SUM(status='tem_whatsapp') AS confirmed,
                      SUM(status='nao_tem_whatsapp') AS rejected,
                      SUM(status='inconclusivo') AS inconclusive
                 FROM whatsapp_test_contacts WHERE job_id=? AND status<>'pendente'""",
            (job_id,),
        ).fetchone()
        connection.execute(
            """UPDATE whatsapp_test_jobs SET processed=?,confirmed=?,rejected=?,inconclusive=?,updated_at=?
                 WHERE id=?""",
            (counts["processed"], counts["confirmed"] or 0, counts["rejected"] or 0,
             counts["inconclusive"] or 0, checked_at, job_id),
        )
        connection.commit()
        time.sleep(0.2)
    job = connection.execute("SELECT * FROM whatsapp_test_jobs WHERE id=?", (job_id,)).fetchone()
    output = _export_confirmed(connection, job)
    final_status = "concluido" if not job["inconclusive"] else "concluido_com_pendencias"
    connection.execute(
        "UPDATE whatsapp_test_jobs SET status=?,output_path=?,completed_at=?,updated_at=? WHERE id=?",
        (final_status, str(output), now(), now(), job_id),
    )
    connection.commit(); connection.close()


def _process_job_group(db_path, job_id):
    """Process Test WhatsApp in batches through the private TESTE W group."""
    connection = sqlite3.connect(db_path, timeout=10)
    connection.row_factory = sqlite3.Row
    initialize_schema(connection)
    job = connection.execute("SELECT * FROM whatsapp_test_jobs WHERE id=?", (job_id,)).fetchone()
    if not job:
        connection.close()
        return
    channel = connection.execute(
        "SELECT * FROM whatsapp_channels WHERE id=? AND mode='web' AND active=1", (job["channel_id"],)
    ).fetchone()
    if not channel:
        connection.execute(
            "UPDATE whatsapp_test_jobs SET status='aguardando_conexao',last_error=?,updated_at=? WHERE id=?",
            ("Conexão QR Code não encontrada.", now(), job_id),
        )
        connection.commit(); connection.close(); return
    active_campaign = connection.execute(
        "SELECT id FROM whatsapp_campaigns WHERE send_mode='web' AND status='em processamento' LIMIT 1"
    ).fetchone()
    if active_campaign:
        connection.execute(
            "UPDATE whatsapp_test_jobs SET status='aguardando',last_error=?,updated_at=? WHERE id=?",
            ("Aguardando a campanha por QR Code terminar.", now(), job_id),
        )
        connection.commit(); connection.close(); return
    state = status(timeout=2, cdp_port=channel["web_cdp_port"], profile_dir=channel["web_profile_dir"])
    if not state.get("connected"):
        connection.execute(
            "UPDATE whatsapp_test_jobs SET status='aguardando_conexao',last_error=?,updated_at=? WHERE id=?",
            ("Abra a conexão QR Code e aguarde o indicador ficar verde.", now(), job_id),
        )
        connection.commit(); connection.close(); return
    connection.execute(
        "UPDATE whatsapp_test_jobs SET status='processando',started_at=COALESCE(started_at,?),last_error=NULL,updated_at=? WHERE id=?",
        (now(), now(), job_id),
    )
    connection.commit()
    items = connection.execute(
        "SELECT * FROM whatsapp_test_contacts WHERE job_id=? AND status='pendente' ORDER BY id", (job_id,)
    ).fetchall()
    for offset in range(0, len(items), 12):
        batch = items[offset:offset + 12]
        result = check_numbers_via_group(
            [item["normalized_phone"] for item in batch],
            group_name="TESTE W",
            cdp_port=channel["web_cdp_port"],
            profile_dir=channel["web_profile_dir"],
        )
        checked_at = now()
        results = result.get("results") or {}
        for item in batch:
            verdict = results.get(item["normalized_phone"])
            if result.get("ok") and verdict is True:
                item_status = "tem_whatsapp"
                reason = "WhatsApp exibiu Iniciar conversa no grupo TESTE W."
            elif result.get("ok") and verdict is False:
                item_status = "nao_tem_whatsapp"
                reason = "Número permaneceu como texto comum no grupo TESTE W."
            else:
                item_status = "inconclusivo"
                reason = result.get("error") or "Não foi possível confirmar no grupo TESTE W."
            connection.execute(
                "UPDATE whatsapp_test_contacts SET status=?,reason=?,checked_at=? WHERE id=?",
                (item_status, reason, checked_at, item["id"]),
            )
        counts = connection.execute(
            """SELECT COUNT(*) AS processed,
                      SUM(status='tem_whatsapp') AS confirmed,
                      SUM(status='nao_tem_whatsapp') AS rejected,
                      SUM(status='inconclusivo') AS inconclusive
                 FROM whatsapp_test_contacts WHERE job_id=? AND status<>'pendente'""",
            (job_id,),
        ).fetchone()
        connection.execute(
            """UPDATE whatsapp_test_jobs SET processed=?,confirmed=?,rejected=?,inconclusive=?,updated_at=?
                 WHERE id=?""",
            (counts["processed"], counts["confirmed"] or 0, counts["rejected"] or 0,
             counts["inconclusive"] or 0, checked_at, job_id),
        )
        connection.commit()
        time.sleep(0.5)
    job = connection.execute("SELECT * FROM whatsapp_test_jobs WHERE id=?", (job_id,)).fetchone()
    output = _export_confirmed(connection, job)
    final_status = "concluido" if not job["inconclusive"] else "concluido_com_pendencias"
    connection.execute(
        "UPDATE whatsapp_test_jobs SET status=?,output_path=?,completed_at=?,updated_at=? WHERE id=?",
        (final_status, str(output), now(), now(), job_id),
    )
    connection.commit(); connection.close()


def worker_loop(db_path, stop_event=None):
    stop_event = stop_event or threading.Event()
    while not stop_event.is_set():
        if _WORKER_LOCK.acquire(blocking=False):
            try:
                connection = sqlite3.connect(db_path, timeout=10)
                connection.row_factory = sqlite3.Row
                initialize_schema(connection)
                job = connection.execute(
                    """SELECT id FROM whatsapp_test_jobs
                        WHERE status IN ('aguardando','aguardando_conexao','processando')
                        ORDER BY id LIMIT 1"""
                ).fetchone()
                connection.close()
                if job:
                    _process_job_group(db_path, job["id"])
            except Exception:
                pass
            finally:
                _WORKER_LOCK.release()
        stop_event.wait(5)
