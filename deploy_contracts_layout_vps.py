import json
import subprocess
import textwrap


SSH = [
    r"C:\Windows\System32\OpenSSH\ssh.exe",
    "-i",
    r"C:\Users\Vaio\.ssh\omega_vps_ed25519",
    "-o",
    "IdentitiesOnly=yes",
    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=20",
    "root@187.127.250.163",
    "python3 -",
]

REMOTE = r'''
from datetime import datetime
from pathlib import Path
import hashlib
import json
import py_compile
import shutil
import subprocess
import time
import urllib.request

app = Path("/opt/omega-crm/app.py")
source = app.read_text(encoding="utf-8")
old_section = '              <section class="pc-card"><h2>▦ Contratos Averbados <span class="pc-sub">{len(promobank_contracts)} encontrados na última consulta</span></h2>{contracts_html}</section>\n'
wide_section = '          <section class="pc-card pc-contracts-card"><h2>▦ Contratos Averbados <span class="pc-sub">{len(promobank_contracts)} encontrados na última consulta</span></h2>{contracts_html}</section>\n'
marker = '          </section>\n          <section class="pc-grid">\n'

if source.count(old_section) != 1:
    raise SystemExit("OLD_SECTION_COUNT=" + str(source.count(old_section)))
if source.count(marker) != 1:
    raise SystemExit("MARKER_COUNT=" + str(source.count(marker)))
if "pc-contracts-card" in source:
    raise SystemExit("PATCH_ALREADY_PRESENT")

stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
backup = app.with_name(f"app.py.before_contracts_full_width_{stamp}.bak")
shutil.copy2(app, backup)
before_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()

patched = source.replace(old_section, "", 1)
patched = patched.replace(marker, '          </section>\n' + wide_section + '          <section class="pc-grid">\n', 1)
app.write_text(patched, encoding="utf-8", newline="\n")

try:
    py_compile.compile(str(app), doraise=True)
    subprocess.run(["systemctl", "restart", "omega-crm.service"], check=True)
    deadline = time.time() + 30
    status = ""
    http_status = None
    title_present = False
    while time.time() < deadline:
        status = subprocess.run(
            ["systemctl", "is-active", "omega-crm.service"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        try:
            with urllib.request.urlopen("http://127.0.0.1:8088/login", timeout=5) as response:
                body = response.read().decode("utf-8", "replace")
                http_status = response.status
                title_present = "ÔMEGA CRÉDITOS" in body or "OMEGA CREDITOS" in body
            if status == "active" and http_status == 200:
                break
        except Exception:
            pass
        time.sleep(1)
    if status != "active" or http_status != 200:
        raise RuntimeError(f"POST_DEPLOY_HEALTH service={status!r} http={http_status!r}")
except Exception:
    shutil.copy2(backup, app)
    subprocess.run(["systemctl", "restart", "omega-crm.service"], check=False)
    raise

after = app.read_text(encoding="utf-8")
result = {
    "backup": str(backup),
    "before_sha256": before_hash,
    "after_sha256": hashlib.sha256(after.encode("utf-8")).hexdigest(),
    "wide_section_count": after.count("pc-contracts-card"),
    "old_section_count": after.count(old_section),
    "service": status,
    "http": http_status,
    "login_title": title_present,
}
print(json.dumps(result, ensure_ascii=False))
'''


completed = subprocess.run(
    SSH,
    input=textwrap.dedent(REMOTE),
    text=True,
    encoding="utf-8",
    errors="replace",
    capture_output=True,
    timeout=120,
)
print(completed.stdout, end="")
if completed.stderr:
    print(completed.stderr, end="")
raise SystemExit(completed.returncode)
