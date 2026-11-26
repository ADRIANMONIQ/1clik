"""Read-only WhatsApp DOM probe used to keep selectors compatible."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from whatsapp_bridge import _page


_, _, page = _page(9341, None)
result = page.evaluate(
    r"""() => ({
      url: location.href,
      containers: [...document.querySelectorAll('[data-testid="msg-container"]')].slice(-8).map((node) => ({
        className: node.className,
        parentClass: node.parentElement?.className || '',
        closestDataId: node.closest('[data-id]')?.getAttribute('data-id') || '',
        ownDataId: node.getAttribute('data-id') || '',
        incomingAncestor: Boolean(node.closest('.message-in')),
        outgoingAncestor: Boolean(node.closest('.message-out')),
        text: (node.innerText || '').slice(0, 120),
      })),
      dataIds: [...document.querySelectorAll('[data-id]')].slice(-12).map((node) => ({
        id: node.getAttribute('data-id') || '',
        className: node.className,
        text: (node.innerText || '').slice(0, 80),
      })),
      unread: [...document.querySelectorAll('#pane-side [aria-label],#pane-side [data-testid]')]
        .filter((node) => /lida|unread/i.test(`${node.getAttribute('aria-label') || ''} ${node.getAttribute('data-testid') || ''}`))
        .slice(0, 20).map((node) => ({
          tag: node.tagName,
          aria: node.getAttribute('aria-label') || '',
          testid: node.getAttribute('data-testid') || '',
          text: (node.innerText || '').slice(0, 80),
        })),
    })"""
)
print(json.dumps(result, ensure_ascii=False, indent=2))
