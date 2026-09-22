"""
One-off probe: can Vishay's Part Number filter be applied at all?

Runs the same filter interaction three ways and reports how many rows the results
table has afterwards. 234 rows means the filter did not apply. Firecrawl only -
no Gemini, no pipeline code.
"""

import os

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from firecrawl import FirecrawlApp

load_dotenv()

URL = "https://www.vishay.com/en/leds/smd/"
VALUE = "VLMK3100"
SECOND_VALUE = "VLRYG311N2Q1"

# Part Number is the first column - fine to hardcode in a throwaway probe
PART_NUMBER_TH = "th:nth-child(1)"
FILTER_ICON = f"{PART_NUMBER_TH} .Webtable_vshFilterIcon__35Cpg"
FILTER_SELECT = f"{PART_NUMBER_TH} select.Webtable_vshFilterOption__B5g0I"
FILTER_OPTION = f'{FILTER_SELECT} option[value="{VALUE}"]'
SECOND_FILTER_OPTION = f'{FILTER_SELECT} option[value="{SECOND_VALUE}"]'

GATE_JS = (
    "(function() { const th = [...document.querySelectorAll('th')]"
    ".find(th => th.textContent.includes('Part Number')); "
    "const icon = th && th.querySelector('.Webtable_vshFilterIcon__35Cpg'); "
    "if (icon) { icon.click(); } })();"
)

SELECT_JS = (
    "(function() { const th = [...document.querySelectorAll('th')]"
    ".find(th => th.textContent.includes('Part Number')); "
    "const select = th && th.querySelector('select.Webtable_vshFilterOption__B5g0I'); "
    "if (!select) { return; } "
    f"const option = [...select.options].find(o => o.value === '{VALUE}'); "
    "if (option) { option.selected = true; "
    "select.dispatchEvent(new Event('change', { bubbles: true })); } })();"
)

# read back what the control thinks is selected, to tell "no selection" from "no filtering"
SELECTED_JS = (
    "(function() { const th = [...document.querySelectorAll('th')]"
    ".find(th => th.textContent.includes('Part Number')); "
    "const select = th && th.querySelector('select.Webtable_vshFilterOption__B5g0I'); "
    "if (!select) { return 'no select in DOM'; } "
    "return [...select.selectedOptions].map(o => o.value).join(',') || 'nothing selected'; })();"
)

WARMUP = [
    {"type": "wait", "milliseconds": 4000},
    {"type": "scroll", "direction": "down", "amount": 2000},
    {"type": "wait", "milliseconds": 1500},
]

VARIANTS = {
    "1. executeJavascript (what the pipeline does today)": WARMUP + [
        {"type": "executeJavascript", "script": GATE_JS},
        {"type": "wait", "milliseconds": 1500},
        {"type": "executeJavascript", "script": SELECT_JS},
        {"type": "wait", "milliseconds": 3000},
    ],
    "2. native click on the option": WARMUP + [
        {"type": "click", "selector": FILTER_ICON},
        {"type": "wait", "milliseconds": 1500},
        {"type": "click", "selector": FILTER_OPTION},
        {"type": "wait", "milliseconds": 3000},
    ],
    "3. native click on the select + typed value": WARMUP + [
        {"type": "click", "selector": FILTER_ICON},
        {"type": "wait", "milliseconds": 1500},
        {"type": "click", "selector": FILTER_SELECT},
        {"type": "write", "text": VALUE},
        {"type": "wait", "milliseconds": 1000},
        {"type": "press", "key": "Enter"},
        {"type": "wait", "milliseconds": 3000},
    ],
    # 2 rows = the clicks add up, 1 row = the second one replaces the first
    f"4. native click on two options ({VALUE} + {SECOND_VALUE})": WARMUP + [
        {"type": "click", "selector": FILTER_ICON},
        {"type": "wait", "milliseconds": 1500},
        {"type": "click", "selector": FILTER_OPTION},
        {"type": "wait", "milliseconds": 3000},
        {"type": "click", "selector": SECOND_FILTER_OPTION},
        {"type": "wait", "milliseconds": 3000},
    ],
}


def table_summary(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    table = next(
        (t for t in soup.find_all("table") if any("Webtable_listTable" in c for c in (t.get("class") or []))),
        None,
    )

    if table is None:
        return "no results table in the scraped HTML"

    parts = [r.find("td").get_text(strip=True) for r in table.find_all("tr")[1:] if r.find("td")]

    if not parts:
        return "results table is present but empty"

    return f"{len(parts)} rows | first: {parts[0]} | last: {parts[-1]}"


def unwrap(js_return):
    value = js_return

    while isinstance(value, dict):
        value = value.get("value")

    return value


def run(name: str, actions: list) -> None:
    app = FirecrawlApp(api_key=os.getenv("FIRECRAWL_API_KEY"))
    actions = actions + [
        {"type": "executeJavascript", "script": SELECTED_JS},
        {"type": "scrape"},
    ]

    try:
        result = app.scrape_url(url=URL, formats=["html"], wait_for=5000, actions=actions, proxy="auto")
    except Exception as error:
        print(f"{name}\n   FAILED: {error}\n")
        return

    returns = result.actions.get("javascriptReturns") or []
    scrapes = result.actions.get("scrapes") or []
    html = scrapes[-1]["html"] if scrapes else (result.html or "")

    print(f"{name}")
    print(f"   control reports selected: {unwrap(returns[-1]) if returns else None}")
    print(f"   {table_summary(html)}\n")


if __name__ == "__main__":
    print(f"Vishay Part Number filter probe, value={VALUE}")
    print("234 rows = the filter did not apply, a short list = it did\n")

    for variant_name, variant_actions in VARIANTS.items():
        run(variant_name, variant_actions)
