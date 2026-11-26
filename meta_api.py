import json
import mimetypes
import os
import re
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from cryptography.fernet import Fernet, InvalidToken


DEFAULT_GRAPH_API_VERSION = os.environ.get("ONECLIK_META_API_VERSION", "v20.0").strip() or "v20.0"
GRAPH_BASE_URL = "https://graph.facebook.com"


class MetaApiError(RuntimeError):
    def __init__(self, message, http_status=None, error_code=None, temporary=False):
        super().__init__(message)
        self.http_status = http_status
        self.error_code = error_code
        self.temporary = temporary


class CredentialVault:
    def __init__(self, key_path):
        self.key_path = Path(key_path)
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.key_path.exists():
            self.key_path.write_bytes(Fernet.generate_key())
            try:
                os.chmod(self.key_path, 0o600)
            except OSError:
                pass
        self.fernet = Fernet(self.key_path.read_bytes().strip())

    def encrypt(self, value):
        value = str(value or "")
        return self.fernet.encrypt(value.encode("utf-8")).decode("ascii") if value else ""

    def decrypt(self, value):
        if not value:
            return ""
        try:
            return self.fernet.decrypt(str(value).encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError) as exc:
            raise RuntimeError("Não foi possível descriptografar as credenciais deste canal.") from exc


def mask_secret(value):
    value = str(value or "")
    if not value:
        return "Não informado"
    if len(value) <= 8:
        return value[:2] + "*" * max(2, len(value) - 4) + value[-2:]
    return value[:4] + "*" * 12 + value[-3:]


def template_variables(components):
    positions = set()
    for component in components or []:
        for match in re.findall(r"\{\{(\d+)\}\}", str(component.get("text") or "")):
            positions.add(int(match))
    return sorted(positions)


def template_variable_specs(components):
    specs = []
    for component in components or []:
        component_type = str(component.get("type") or "").upper()
        if component_type in ("HEADER", "BODY"):
            text = str(component.get("text") or "")
            for match in sorted({int(value) for value in re.findall(r"\{\{(\d+)\}\}", text)}):
                specs.append({"key": f"{component_type}:{match}", "component": component_type.lower(), "position": match})
            for name in dict.fromkeys(re.findall(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}", text)):
                specs.append({"key": f"{component_type}:{name}", "component": component_type.lower(), "position": name, "parameter_name": name})
        elif component_type == "BUTTONS":
            for index, button in enumerate(component.get("buttons") or []):
                source = str(button.get("url") or button.get("text") or "")
                for match in sorted({int(value) for value in re.findall(r"\{\{(\d+)\}\}", source)}):
                    specs.append({"key": f"BUTTON:{index}:{match}", "component": "button", "button_index": index, "position": match, "sub_type": str(button.get("type") or "url").lower()})
    return specs


def component_text(components, component_type):
    for component in components or []:
        if str(component.get("type") or "").upper() == component_type.upper():
            return str(component.get("text") or "")
    return ""


class MetaClient:
    def __init__(self, access_token, version=DEFAULT_GRAPH_API_VERSION, opener=urlopen):
        self.access_token = str(access_token or "").strip()
        self.version = str(version or DEFAULT_GRAPH_API_VERSION).strip().lstrip("/")
        self.opener = opener

    def request(self, method, path, payload=None, params=None, timeout=25):
        url = f"{GRAPH_BASE_URL}/{self.version}/{str(path).lstrip('/')}"
        if params:
            url += "?" + urlencode(params)
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(url, data=body, method=method, headers={
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        })
        try:
            with self.opener(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
                return json.loads(raw or "{}"), int(getattr(response, "status", 200))
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                error = json.loads(raw).get("error", {})
            except Exception:
                error = {}
            message = str(error.get("message") or "A Meta recusou a solicitação.")
            code = error.get("code")
            temporary = exc.code == 429 or exc.code >= 500 or bool(error.get("is_transient"))
            raise MetaApiError(message, exc.code, code, temporary) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise MetaApiError("Não foi possível alcançar a Meta.", None, None, True) from exc

    def upload_media(self, phone_number_id, file_path):
        path = Path(file_path)
        if not path.is_file():
            raise MetaApiError("A imagem do cabeçalho não foi encontrada no 1Clik.", 400, None, False)
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        boundary = "----1Clik" + uuid.uuid4().hex
        parts = []
        for name, value in (("messaging_product", "whatsapp"), ("type", mime_type)):
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
            f"Content-Type: {mime_type}\r\n\r\n".encode()
        )
        parts.extend((path.read_bytes(), f"\r\n--{boundary}--\r\n".encode()))
        request = Request(
            f"{GRAPH_BASE_URL}/{self.version}/{phone_number_id}/media",
            data=b"".join(parts),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        try:
            with self.opener(request, timeout=60) as response:
                data = json.loads(response.read().decode("utf-8", errors="replace") or "{}")
                media_id = str(data.get("id") or "")
                if not media_id:
                    raise MetaApiError("A Meta não retornou o identificador da imagem.", 502, None, True)
                return media_id
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                error = json.loads(raw).get("error", {})
            except Exception:
                error = {}
            raise MetaApiError(
                str(error.get("message") or "A Meta recusou a imagem do cabeçalho."),
                exc.code,
                error.get("code"),
                exc.code == 429 or exc.code >= 500 or bool(error.get("is_transient")),
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise MetaApiError("Não foi possível carregar a imagem na Meta.", None, None, True) from exc

    def validate_connection(self, phone_number_id, waba_id):
        token_data, _ = self.request("GET", "me", params={"fields": "id,name"})
        phone_data, _ = self.request("GET", phone_number_id, params={"fields": "id,display_phone_number,verified_name,quality_rating,name_status"})
        waba_data, _ = self.request("GET", waba_id, params={"fields": "id,name"})
        numbers, _ = self.request("GET", f"{waba_id}/phone_numbers", params={"fields": "id,display_phone_number,verified_name,quality_rating,name_status", "limit": "100"})
        linked = any(str(item.get("id")) == str(phone_number_id) for item in numbers.get("data", []))
        if not linked:
            raise MetaApiError("O Phone Number ID não pertence ao WABA informado.", 400, None, False)
        return {
            "display_phone_number": phone_data.get("display_phone_number", ""),
            "verified_name": phone_data.get("verified_name", ""),
            "name_status": phone_data.get("name_status", ""),
            "quality_rating": phone_data.get("quality_rating", ""),
            "waba_id": waba_data.get("id", ""),
            "waba_name": waba_data.get("name", ""),
            "token_status": "válido" if token_data.get("id") else "não confirmado",
        }

    def list_templates(self, waba_id):
        templates = []
        path = f"{waba_id}/message_templates"
        params = {"fields": "id,name,language,status,category,components", "limit": "100"}
        while path:
            data, _ = self.request("GET", path, params=params)
            templates.extend(data.get("data", []))
            next_url = (data.get("paging") or {}).get("next")
            if not next_url:
                break
            parsed = urlparse(next_url)
            marker = f"/{self.version}/"
            path = parsed.path.split(marker, 1)[-1]
            params = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        return templates

    def send_template(self, phone_number_id, recipient, template_name, language, variables, header_media=None):
        components = []
        if isinstance(variables, list):
            variables = {"body": variables}
        def text_parameter(value):
            if isinstance(value, dict) and value.get("parameter_name"):
                return {"type": "text", "parameter_name": str(value["parameter_name"]), "text": str(value.get("text") or "")}
            return {"type": "text", "text": str(value)}
        if header_media:
            media_type = str(header_media.get("type") or "image").lower()
            components.append({
                "type": "header",
                "parameters": [{"type": media_type, media_type: {"id": str(header_media["id"])}}],
            })
        for component_type in ("header", "body"):
            values = (variables or {}).get(component_type) or []
            if values:
                components.append({"type": component_type, "parameters": [text_parameter(value) for value in values]})
        for index, values in ((variables or {}).get("buttons") or {}).items():
            if values:
                components.append({"type": "button", "sub_type": "url", "index": str(index), "parameters": [{"type": "text", "text": str(value)} for value in values]})
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "template",
            "template": {"name": template_name, "language": {"code": language}},
        }
        if components:
            payload["template"]["components"] = components
        data, http_status = self.request("POST", f"{phone_number_id}/messages", payload=payload)
        wamid = (data.get("messages") or [{}])[0].get("id", "")
        return {"http_status": http_status, "wamid": wamid, "raw": data}
