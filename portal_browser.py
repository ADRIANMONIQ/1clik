import json
import subprocess
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

from whatsapp_bridge import _chrome_path


PORTALS = {
    "promosys": {
        "url": "https://www.sistemapromosys.com.br/",
        "port": 9361,
        "profile": "chrome-promosys-profile",
    },
    "promobank": {
        "url": "https://beta.promobank.online/",
        "port": 9362,
        "profile": "chrome-promobank-profile",
    },
}


def _config(base_dir, portal):
    name = str(portal or "").strip().lower()
    if name not in PORTALS:
        raise ValueError("Portal não permitido.")
    item = PORTALS[name]
    return name, item["url"], item["port"], Path(base_dir) / "data" / item["profile"]


def status(base_dir, portal, timeout=1):
    name, url, port, profile = _config(base_dir, portal)
    try:
        with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=timeout) as response:
            version = json.loads(response.read().decode("utf-8", errors="replace") or "{}")
        with urlopen(f"http://127.0.0.1:{port}/json/list", timeout=timeout) as response:
            tabs = json.loads(response.read().decode("utf-8", errors="replace") or "[]")
        return {
            "ok": True, "running": True, "portal": name, "url": url,
            "port": port, "profile": str(profile), "browser": version.get("Browser", ""),
            "tabs": [item.get("url", "") for item in tabs if item.get("type") == "page"],
            "targets": [
                {"id": item.get("id", ""), "url": item.get("url", "")}
                for item in tabs if item.get("type") == "page"
            ],
        }
    except Exception as exc:
        return {
            "ok": True, "running": False, "portal": name, "url": url,
            "port": port, "profile": str(profile), "error": str(exc)[:240],
        }


def _open_tab(port, url):
    request = Request(f"http://127.0.0.1:{port}/json/new?{quote(url, safe=':/?=&')}", method="PUT")
    with urlopen(request, timeout=3) as response:
        return json.loads(response.read().decode("utf-8", errors="replace") or "{}")


def _activate_tab(port, target_id):
    request = Request(f"http://127.0.0.1:{port}/json/activate/{target_id}", method="PUT")
    with urlopen(request, timeout=3) as response:
        return response.read()


def start(base_dir, portal):
    name, url, port, profile = _config(base_dir, portal)
    current = status(base_dir, name)
    if current.get("running"):
        domain = url.split("//", 1)[-1].split("/", 1)[0]
        target = next((item for item in current.get("targets", []) if name in item["url"].lower() or domain in item["url"]), None)
        if target:
            _activate_tab(port, target["id"])
        else:
            _open_tab(port, url)
        return {**status(base_dir, name), "started": False}

    executable = _chrome_path()
    if not executable:
        return {"ok": False, "running": False, "error": "Chrome ou Edge não encontrado."}
    profile.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        [
            executable,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--remote-allow-origins=*",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-popup-blocking",
            "--new-window",
            url,
        ],
        cwd=str(base_dir),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(40):
        time.sleep(0.25)
        current = status(base_dir, name)
        if current.get("running"):
            return {**current, "started": True}
    return {"ok": False, "running": False, "error": "O Chrome abriu, mas a porta de controle não respondeu."}


def verify_control(base_dir, portal):
    current = status(base_dir, portal, timeout=2)
    if not current.get("running"):
        return current
    from playwright.sync_api import sync_playwright

    runtime = sync_playwright().start()
    try:
        browser = runtime.chromium.connect_over_cdp(f'http://127.0.0.1:{current["port"]}', timeout=5000)
        pages = [page for context in browser.contexts for page in context.pages]
        titles = [page.title() for page in pages]
        return {**current, "controllable": bool(pages), "page_urls": [page.url for page in pages], "page_titles": titles}
    finally:
        runtime.stop()
