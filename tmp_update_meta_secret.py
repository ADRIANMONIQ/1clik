import html
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from meta_api import CredentialVault


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "1clik.db"
KEY_PATH = BASE_DIR / "secrets" / "meta_credentials.key"
HOST = "127.0.0.1"
PORT = 8765


class Handler(BaseHTTPRequestHandler):
    def log_message(self, _format, *_args):
        return

    def do_GET(self):
        body = b"""<!doctype html><meta charset="utf-8">
<title>Atualizar App Secret</title>
<form method="post">
<label>App Secret <input name="secret" type="password" autocomplete="off" required></label>
<button type="submit">Salvar com seguranca</button>
</form>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        fields = parse_qs(self.rfile.read(length).decode("utf-8", errors="strict"))
        secret = fields.get("secret", [""])[0].strip()
        ok = False
        message = "Chave invalida."
        if len(secret) >= 16:
            vault = CredentialVault(KEY_PATH)
            encrypted = vault.encrypt(secret)
            with sqlite3.connect(DB_PATH) as connection:
                cursor = connection.execute(
                    """UPDATE whatsapp_channels
                       SET meta_app_secret='', meta_app_secret_enc=?,
                           updated_at=datetime('now')
                       WHERE id=12 AND mode='meta'""",
                    (encrypted,),
                )
                connection.commit()
                row = connection.execute(
                    "SELECT meta_app_secret_enc FROM whatsapp_channels WHERE id=12"
                ).fetchone()
            ok = cursor.rowcount == 1 and row and vault.decrypt(row[0]) == secret
            message = "App Secret salvo e verificado." if ok else "Nao foi possivel confirmar a gravacao."
        body = (
            "<!doctype html><meta charset='utf-8'><h1>"
            + html.escape(message)
            + "</h1>"
        ).encode("utf-8")
        self.send_response(200 if ok else 400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        threading.Thread(target=self.server.shutdown, daemon=True).start()


if __name__ == "__main__":
    HTTPServer((HOST, PORT), Handler).serve_forever()
