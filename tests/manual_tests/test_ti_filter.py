"""
One-off probe: how to drive TI's real column filter.

The floating filter input in the header row is a permanently disabled twin. The real control
appears only after the column menu is opened, as <ti-search-field>, whose input lives in an
open shadow root. Each variant opens the menu first, then tries one way of typing into it.
Firecrawl only - no Gemini, no pipeline code.
"""

import os

from dotenv import load_dotenv
from firecrawl import FirecrawlApp

load_dotenv()

URL = "https://www.ti.com/product-category/sensors/products.html#"
VALUE = "TMP75"

HOST = "ti-search-field"
SHADOW_INPUT = ".ti-search-field-input"
GATE_BUTTON = '.ag-header-row-column-filter [aria-colindex="1"] button[ref="eButtonShowMainFilter"]'

GATE_JS = (
    "(function() { const header = [...document.querySelectorAll('.ag-header-cell')]"
    ".find(th => th.textContent.includes('Product number')); "
    "if (header) { const button = header.querySelector('[ref=\"eButtonShowMainFilter\"]'); "
    "if (button) button.click(); } })();"
)

# is the real control there at all, and can we reach into it
PROBE_JS = (
    "(function() { const host = document.querySelector('ti-search-field'); "
    "return JSON.stringify({ host: !!host, "
    "shadow: !!(host && host.shadowRoot), "
    "input: !!(host && host.shadowRoot && host.shadowRoot.querySelector('input')), "
    "placeholder: host ? host.getAttribute('placeholder') : null }); })();"
)

# the pattern from the project history: native setter + composed events, inside the shadow root
SHADOW_TYPE_JS = (
    "(function() { const host = document.querySelector('ti-search-field'); "
    "if (!host) { return 'no ti-search-field'; } "
    "const input = host.shadowRoot && host.shadowRoot.querySelector('input'); "
    "if (!input) { return 'no input in shadowRoot'; } "
    "const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set; "
    "input.focus(); "
    f"setter.call(input, '{VALUE}'); "
    "input.dispatchEvent(new Event('input', { bubbles: true, composed: true })); "
    "input.dispatchEvent(new Event('change', { bubbles: true, composed: true })); "
    "input.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, composed: true, key: 'Enter' })); "
    "input.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, composed: true, key: 'Enter' })); "
    "return 'typed into the shadow input'; })();"
)

# 396 here means nothing was filtered
ROWS_JS = (
    "(function() { const rows = document.querySelectorAll('.ag-center-cols-container .ag-row').length; "
    "const panel = document.querySelector('.ag-paging-row-summary-panel, .ag-status-name-value'); "
    "return JSON.stringify({ rows: rows, summary: panel ? panel.textContent.replace(/\\s+/g, ' ').trim() : null }); })();"
)

WARMUP = [
    {"type": "wait", "milliseconds": 5000},
    {"type": "scroll", "direction": "down", "amount": 1500},
    {"type": "wait", "milliseconds": 2000},
]

# the JS click never opened the menu, so the gate itself gets a real browser click here
OPEN_GATE = WARMUP + [
    {"type": "click", "selector": GATE_BUTTON},
    {"type": "wait", "milliseconds": 2500},
    {"type": "executeJavascript", "script": PROBE_JS},
]

OPEN_GATE_BY_JS = WARMUP + [
    {"type": "executeJavascript", "script": GATE_JS},
    {"type": "wait", "milliseconds": 2500},
    {"type": "executeJavascript", "script": PROBE_JS},
]

VARIANTS = {
    "0. JS click on the gate (baseline - does the menu open at all?)": OPEN_GATE_BY_JS + [
        {"type": "wait", "milliseconds": 1000},
    ],
    "1. native gate click, then native click on the shadow input + write + Enter": OPEN_GATE + [
        {"type": "click", "selector": SHADOW_INPUT},
        {"type": "write", "text": VALUE},
        {"type": "wait", "milliseconds": 1000},
        {"type": "press", "key": "Enter"},
        {"type": "wait", "milliseconds": 4000},
    ],
    "2. native gate click, then native click on the ti-search-field host + write + Enter": OPEN_GATE + [
        {"type": "click", "selector": HOST},
        {"type": "write", "text": VALUE},
        {"type": "wait", "milliseconds": 1000},
        {"type": "press", "key": "Enter"},
        {"type": "wait", "milliseconds": 4000},
    ],
    "3. native gate click, then executeJavascript through shadowRoot": OPEN_GATE + [
        {"type": "executeJavascript", "script": SHADOW_TYPE_JS},
        {"type": "wait", "milliseconds": 4000},
    ],
}


def unwrap(js_return):
    value = js_return

    while isinstance(value, dict):
        value = value.get("value")

    return value


def run(name: str, actions: list) -> None:
    app = FirecrawlApp(api_key=os.getenv("FIRECRAWL_API_KEY"))
    actions = actions + [{"type": "executeJavascript", "script": ROWS_JS}]

    try:
        result = app.scrape_url(url=URL, formats=["html"], wait_for=5000, actions=actions, proxy="auto")
    except Exception as error:
        print(f"{name}\n   FAILED: {error}\n")
        return

    returns = [unwrap(r) for r in (result.actions.get("javascriptReturns") or [])]

    print(name)
    for value in returns:
        if isinstance(value, str) and '"host"' in value:
            print(f"   control after the gate: {value}")
        elif isinstance(value, str) and '"rows"' in value:
            print(f"   grid after the action:  {value}")
        elif value:
            print(f"   {value}")
    print()


if __name__ == "__main__":
    print(f"TI filter probe, value={VALUE}")
    print('summary "of 396" = the filter did not apply, a smaller total = it did\n')

    for variant_name, variant_actions in VARIANTS.items():
        run(variant_name, variant_actions)
