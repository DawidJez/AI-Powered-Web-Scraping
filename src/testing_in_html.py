from bs4 import BeautifulSoup

with open("data/html_to_analize.txt", "r", encoding="utf-8") as f:
    html = f.read()

soup = BeautifulSoup(html, "html.parser")
buttons = soup.select('button[aria-label="Open Filter Menu"]')
print(f"znaleziono: {len(buttons)}")

for i, btn in enumerate(buttons):
    th = btn.find_parent("th") or btn.find_parent("div", attrs={"role": "columnheader"})
    col_id = th.get("col-id") if th else None
    print(i, "col-id:", col_id, "| ref:", btn.get("ref"))