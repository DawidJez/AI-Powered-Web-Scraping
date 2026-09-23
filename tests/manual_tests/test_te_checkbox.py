"""
One-off probe, round 2: TE's Communication Protocol filter.

Round 1 showed the tick always survives, so the checkbox is not the problem. The Apply we
clicked was one of the nine wrapped in <span class="mobile"> inside the dropdowns; the real
one is page-level: .global-apply-btn-wrapper.desktop a.apply. The page also shows two counts
(a pending one next to Apply, and the grid total), so this round reads both instead of
grabbing the first "N Results" in the text. Firecrawl only - no Gemini, no pipeline code.
"""

import os

from dotenv import load_dotenv
from firecrawl import FirecrawlApp

load_dotenv()

URL = "https://www.te.com/en/plp/das-antennas/Y44rw.html"
VALUE = "897878"  # ISM

LABEL_SELECTOR = f'label:has(input[value="{VALUE}"])'
GLOBAL_APPLY = ".global-apply-btn-wrapper.desktop a.apply"

CLICK_LABEL_JS = (
    f"(function() {{ const input = document.querySelector('input[value=\"{VALUE}\"]'); "
    "const label = input && input.closest('label'); "
    "if (label) { label.click(); return 'clicked the label'; } return 'label not found'; })();"
)

GLOBAL_APPLY_JS = (
    f"(function() {{ const btn = document.querySelector('{GLOBAL_APPLY}'); "
    "if (!btn) { return 'no global apply button'; } "
    "btn.click(); return 'clicked the global apply, classes=' + btn.className; })();"
)

# pending = the count next to Apply, results = every "N Results" the page shows, in order
STATE_JS = (
    "(function() { const checked = [...document.querySelectorAll('input[type=checkbox]:checked')].map(i => i.name); "
    "const pending = document.querySelector('.parametric-counts-display .count'); "
    "const results = [...document.body.innerText.matchAll(/(\\d+)\\s+Results/g)].map(m => m[1]); "
    "return JSON.stringify({ checked: checked, pending: pending ? pending.textContent.trim() : null, results: results }); })();"
)

WARMUP = [
    {"type": "wait", "milliseconds": 5000},
    {"type": "scroll", "direction": "down", "amount": 1200},
    {"type": "wait", "milliseconds": 2000},
]

VARIANTS = {
    "1. label click only - does the page already know the answer?": WARMUP + [
        {"type": "executeJavascript", "script": CLICK_LABEL_JS},
        {"type": "wait", "milliseconds": 4000},
    ],
    "2. label click, then the GLOBAL desktop apply (both by script)": WARMUP + [
        {"type": "executeJavascript", "script": CLICK_LABEL_JS},
        {"type": "wait", "milliseconds": 2000},
        {"type": "executeJavascript", "script": GLOBAL_APPLY_JS},
        {"type": "wait", "milliseconds": 6000},
    ],
    "3. the same two clicks, but native browser clicks": WARMUP + [
        {"type": "click", "selector": LABEL_SELECTOR},
        {"type": "wait", "milliseconds": 2000},
        {"type": "click", "selector": GLOBAL_APPLY},
        {"type": "wait", "milliseconds": 6000},
    ],
}


def unwrap(js_return):
    value = js_return

    while isinstance(value, dict):
        value = value.get("value")

    return value


def run(name: str, actions: list) -> None:
    app = FirecrawlApp(api_key=os.getenv("FIRECRAWL_API_KEY"))
    actions = actions + [{"type": "executeJavascript", "script": STATE_JS}]

    try:
        result = app.scrape_url(url=URL, formats=["html"], wait_for=5000, actions=actions, proxy="auto")
    except Exception as error:
        print(f"{name}\n   FAILED: {error}\n")
        return

    print(name)
    for value in [unwrap(r) for r in (result.actions.get("javascriptReturns") or [])]:
        if value:
            print(f"   {value}")
    print()


if __name__ == "__main__":
    print("TE Communication Protocol probe round 2, value=ISM")
    print('results still ["54"] = the grid never filtered; a small number = it did\n')

    for variant_name, variant_actions in VARIANTS.items():
        run(variant_name, variant_actions)
