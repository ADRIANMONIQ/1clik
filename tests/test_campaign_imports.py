import sqlite3
import unittest

from openpyxl import load_workbook

from campaign_imports import contacts_excel_bytes, excel_bytes, initialize_schema, normalize_rows, save_batch


class CampaignImportTests(unittest.TestCase):
    def test_normalizes_splits_and_deduplicates_contacts(self):
        rows = normalize_rows("promobank", "CAMPANHA A", [
            {"NOME": "Maria Silva", "CPF": "123", "CONTATOS": "(91) 99999-0001; 91 98888-0002"},
            {"NOME": "Maria repetida", "CONTATO": "+55 91 99999-0001"},
            {"NOME": "Inválido", "CONTATO": "123"},
        ])
        self.assertEqual(2, len(rows))
        self.assertEqual("5591999990001", rows[0]["telefone"])
        self.assertEqual("promobank", rows[0]["origem"])
        self.assertEqual("CAMPANHA A", rows[0]["campanha_origem"])

    def test_persists_batch_and_generates_excel(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        initialize_schema(connection)
        batch_id, normalized = save_batch(
            connection, "promosys", "CAMPANHA 14", [{"NOME": "João", "TELEFONE": "91999990000"}], 10
        )
        batch = connection.execute("SELECT * FROM campaign_import_batches WHERE id=?", (batch_id,)).fetchone()
        self.assertEqual(10, batch["leads_total"])
        self.assertEqual(1, batch["contacts_total"])
        self.assertEqual(1, len(normalized))
        workbook = load_workbook(filename=__import__("io").BytesIO(excel_bytes(batch)), read_only=True)
        values = list(workbook.active.values)
        self.assertEqual("NOME", values[0][0])
        self.assertEqual("João", values[1][0])

    def test_promosys_csv_phone_columns_keep_the_correct_name(self):
        rows = normalize_rows("promosys", "ADRIAN_11112026", [{
            "Nome Campanha": "ADRIAN_11112026",
            "Nome": "CLIENTE TESTE",
            "Telefone 1": "(61) 98197-6950",
            "Telefone 2": "61 99999-0001",
            "Telefone 3": "",
        }])
        self.assertEqual(2, len(rows))
        self.assertEqual(["CLIENTE TESTE", "CLIENTE TESTE"], [row["nome"] for row in rows])
        self.assertEqual(["5561981976950", "5561999990001"], [row["telefone"] for row in rows])

    def test_clean_download_has_only_name_and_contact(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        initialize_schema(connection)
        batch_id, _ = save_batch(connection, "promosys", "TESTE", [{
            "Nome": "MARIA TESTE", "Telefone 1": "61999990001", "Telefone 2": "61988880002"
        }])
        batch = connection.execute("SELECT * FROM campaign_import_batches WHERE id=?", (batch_id,)).fetchone()
        workbook = load_workbook(filename=__import__("io").BytesIO(contacts_excel_bytes(batch)), read_only=True)
        values = list(workbook.active.values)
        self.assertEqual(("NOME", "CONTATO"), values[0])
        self.assertEqual(3, len(values))
        self.assertEqual("MARIA TESTE", values[1][0])


if __name__ == "__main__":
    unittest.main()
