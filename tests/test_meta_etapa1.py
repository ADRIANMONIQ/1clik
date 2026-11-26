import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

import app
import campaign_worker
from meta_api import CredentialVault, MetaClient, mask_secret, template_variable_specs


class FakeResponse:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class MetaStageOneTests(unittest.TestCase):
    def test_vault_encrypts_and_masks(self):
        with tempfile.TemporaryDirectory() as folder:
            vault = CredentialVault(Path(folder) / "credential.key")
            encrypted = vault.encrypt("EAAG_TOKEN_SIMULADO_9XZ")
            self.assertNotIn("TOKEN_SIMULADO", encrypted)
            self.assertEqual(vault.decrypt(encrypted), "EAAG_TOKEN_SIMULADO_9XZ")
            self.assertEqual(mask_secret("EAAG_TOKEN_SIMULADO_9XZ"), "EAAG************9XZ")

    def test_connection_validation_is_simulated(self):
        def opener(request, timeout=0):
            url = request.full_url
            if "/me?" in url:
                return FakeResponse({"id": "user-1", "name": "Sistema"})
            if "/phone-1?" in url:
                return FakeResponse({"id": "phone-1", "display_phone_number": "+55 91 99999-9999", "verified_name": "Omega", "quality_rating": "GREEN", "name_status": "APPROVED"})
            if "/waba-1/phone_numbers?" in url:
                return FakeResponse({"data": [{"id": "phone-1"}]})
            if "/waba-1?" in url:
                return FakeResponse({"id": "waba-1", "name": "Omega WABA"})
            raise AssertionError(url)

        result = MetaClient("TOKEN_FALSO", "v20.0", opener=opener).validate_connection("phone-1", "waba-1")
        self.assertEqual(result["token_status"], "válido")
        self.assertEqual(result["quality_rating"], "GREEN")

    def test_worker_sends_one_simulated_template_without_network(self):
        app.initialize()
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "test.db"
            key_path = Path(folder) / "credential.key"
            shutil.copy2(app.DB_PATH, database)
            vault = CredentialVault(key_path)
            stamp = "2099-01-01T10:00:00"
            with closing(sqlite3.connect(database)) as connection, connection:
                channel_id = connection.execute("""INSERT INTO whatsapp_channels(nome,telefone,mode,meta_phone_number_id,meta_access_token,meta_access_token_enc,meta_api_version,official_number,active,created_at,updated_at,rotation_enabled,meta_waba_id)
                    VALUES('Canal teste','91 999999999','meta','phone-test','',?,'v20.0',1,1,?,?,0,'waba-test')""", (vault.encrypt("TOKEN_FALSO"), stamp, stamp)).lastrowid
                template_id = connection.execute("""INSERT INTO meta_official_templates(channel_id,name,language,status,components_json,variables_json,synced_at)
                    VALUES(?,'boas_vindas','pt_BR','APPROVED','[]','[1]',?)""", (channel_id, stamp)).lastrowid
                campaign_id = connection.execute("""INSERT INTO whatsapp_campaigns(nome,status,channel_id,total,enviados,falhas,created_at,updated_at,official_template_id,template_language,interval_seconds)
                    VALUES('SIMULACAO','em processamento',?,1,0,0,?,?,?,'pt_BR',1)""", (channel_id, stamp, stamp, template_id)).lastrowid
                item_id = connection.execute("""INSERT INTO whatsapp_campaign_items(campaign_id,nome,telefone,status,created_at,updated_at,original_phone,normalized_phone,variables_json)
                    VALUES(?,'Cliente Teste','91 999999999','pendente',?,?, '91 999999999','5591999999999','[\"Cliente Teste\"]')""", (campaign_id, stamp, stamp)).lastrowid
                connection.execute("""INSERT INTO meta_campaign_send_attempts(campaign_id,item_id,channel_id,template_id,original_phone,normalized_phone,variables_json,status,created_at,updated_at)
                    VALUES(?,?,?,?, '91 999999999','5591999999999','[\"Cliente Teste\"]','pendente',?,?)""", (campaign_id, item_id, channel_id, template_id, stamp, stamp))

            class FakeClient:
                def __init__(self, *_args, **_kwargs):
                    pass

                def send_template(self, *_args, **_kwargs):
                    return {"http_status": 200, "wamid": "wamid.SIMULADO", "raw": {}}

            with patch.object(campaign_worker, "DB_PATH", database), patch.object(campaign_worker, "KEY_PATH", key_path), patch.object(campaign_worker, "MetaClient", FakeClient):
                processed, _ = campaign_worker.run_once()
                processed_again, _ = campaign_worker.run_once()
            self.assertTrue(processed)
            self.assertFalse(processed_again)
            with closing(sqlite3.connect(database)) as connection:
                status_value, wamid = connection.execute("SELECT status,wamid FROM meta_campaign_send_attempts WHERE campaign_id=?", (campaign_id,)).fetchone()
                campaign_status = connection.execute("SELECT status FROM whatsapp_campaigns WHERE id=?", (campaign_id,)).fetchone()[0]
            self.assertEqual((status_value, wamid), ("enviado", "wamid.SIMULADO"))
            self.assertEqual(campaign_status, "concluída")

    def test_invalid_brazilian_phone_is_rejected_without_changing_original(self):
        original = "telefone ausente"
        self.assertEqual(app.normalize_brazil_phone(original), "")
        self.assertEqual(original, "telefone ausente")

    def test_template_variable_positions_cover_header_body_and_button(self):
        components = [
            {"type": "HEADER", "text": "Olá {{1}}"},
            {"type": "BODY", "text": "Nome {{1}}, valor {{2}}"},
            {"type": "BUTTONS", "buttons": [{"type": "URL", "url": "https://exemplo/{{1}}"}]},
        ]
        keys = [item["key"] for item in template_variable_specs(components)]
        self.assertEqual(keys, ["HEADER:1", "BODY:1", "BODY:2", "BUTTON:0:1"])

    def test_official_template_payload_preserves_accents_and_quick_replies(self):
        payload = app.build_official_template_payload(
            "omega_maraba_v2",
            "pt_BR",
            "MARKETING",
            "Olá, {{1}}! Crédito disponível.",
            ["Adrian"],
            ["Sim, tenho interesse", "Não, obrigada", "Bloquear contato"],
        )
        self.assertEqual(payload["components"][0]["example"]["body_text"], [["Adrian"]])
        self.assertEqual(payload["components"][1]["buttons"][1]["text"], "Não, obrigada")

    def test_official_template_rejects_named_or_skipped_variables(self):
        with self.assertRaises(ValueError):
            app.build_official_template_payload("modelo", "pt_BR", "MARKETING", "Olá, {{nome}}!", [], [])
        with self.assertRaises(ValueError):
            app.build_official_template_payload("modelo", "pt_BR", "MARKETING", "Olá, {{2}}!", ["Adrian", "500"], [])


if __name__ == "__main__":
    unittest.main()
