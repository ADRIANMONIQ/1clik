import re
import sys
from pathlib import Path


PROMOSYS_CAMPAIGNS_URL = "https://www.sistemapromosys.com.br/campanha/controleCampanha"


def locate_campaign(base_dir, campaign_name, cdp_port=9361):
    exact_name = str(campaign_name or "").strip()
    if not exact_name:
        raise ValueError("Informe o nome exato da campanha.")

    vendor = Path(base_dir) / "vendor"
    if str(vendor) not in sys.path:
        sys.path.insert(0, str(vendor))
    from playwright.sync_api import sync_playwright

    runtime = sync_playwright().start()
    try:
        browser = runtime.chromium.connect_over_cdp(f"http://127.0.0.1:{int(cdp_port)}", timeout=5000)
        pages = [page for context in browser.contexts for page in context.pages]
        page = next((item for item in pages if "sistemapromosys.com.br" in (item.url or "")), None)
        if page is None:
            raise ValueError("A janela dedicada do Promosys não está aberta.")
        if "/login" in page.url.lower() or "entrar" in page.title().lower():
            raise ValueError("O Promosys ainda não está autenticado. Faça o login na janela separada.")
        if "/campanha/controleCampanha" not in page.url:
            page.goto(PROMOSYS_CAMPAIGNS_URL, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_selector("table tbody tr", timeout=10000)
        matches = []
        for row in page.locator("table tbody tr").all():
            cells = [text.strip() for text in row.locator("td").all_inner_texts()]
            if any(text == exact_name for text in cells):
                matches.append((row, cells))
        if not matches:
            raise LookupError(f'Campanha “{exact_name}” não encontrada em Minhas Campanhas.')
        if len(matches) > 1:
            raise ValueError(f'Foram encontradas {len(matches)} campanhas com o nome exato “{exact_name}”.')
        row, cells = matches[0]
        name_index = cells.index(exact_name)
        total_text = cells[name_index + 3] if len(cells) > name_index + 3 else ""
        digits = re.sub(r"\D", "", total_text)
        leads_total = int(digits or 0)
        checkbox = row.locator('input[type="checkbox"]').first
        campaign_id = checkbox.get_attribute("id") if checkbox.count() else ""
        details_link = row.locator('a[href*="/campanha/visualizarBase/"]').first
        details_url = details_link.get_attribute("href") if details_link.count() else ""
        return {
            "found": True,
            "campaign_name": exact_name,
            "campaign_id": campaign_id or "",
            "leads_total": leads_total,
            "details_url": details_url or "",
            "page_url": page.url,
        }
    finally:
        runtime.stop()
