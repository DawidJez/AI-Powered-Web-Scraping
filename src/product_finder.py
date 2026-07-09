from bs4 import BeautifulSoup # needed for searching data hidden inside the html
import requests
import os

COLUMN_MAP = {
    "-3": "subcategory",
    "1498": "rating",
    "2954": "package_type",
    "2955": "pin_count",
    "3316": "ti_functional_safety_category",
    "1192": "operating_temperature_range",
    "261011": "package_area",
    "1811": "package_size",
    "1130": "price_quantity",
    "-4": "status",
}

class ProductFinder:

    def __init__(self, html: str):
        self.soup = BeautifulSoup(html, "html.parser")

    def find_by_mpn(self, mpn: str):
        # mpn name is to be found in attribute as gpn

        found_mpns = self.soup.find_all(attrs={"gpn": mpn})

        if found_mpns is None:
            return None
        
        # secured candidates because if paren None -> None.find_all -> AttributeError
        candidates = []
        for found_mpn in found_mpns:
            row = found_mpn.find_parent(attrs={"role": "row"})
            if row is not None:
                candidates.append(row)

        if not candidates:
            return None

        # takes the record we need, it has more gridcells
        return max(candidates, key=lambda r: len(r.find_all(attrs={"role": "gridcell"})))
        
    def extract_row_data(self, row) -> dict:
        
        if row is None:
            return {}
        
        data = {}
        cells = row.find_all("div", attrs={"role": "gridcell"})
        for cell in cells:
            col_id = cell.get("col-id")
            key = COLUMN_MAP.get(col_id, col_id)

            value_el = cell.select_one(".ag-cell-value")
            text = value_el.get_text(strip=True) if value_el else cell.get_text(strip=True)

            link = cell.find("a")
            if link and link.get("href"):
                data[key] = {"text": text, "href": link["href"]}
            else:
                data[key] = text

        return data
    
    def find_pdf(self, mpn: str) -> str | None:

        found_mpn = self.soup.find(attrs={"gpn": mpn})

        if found_mpn is None:
            return None

        row = found_mpn.find_parent(attrs={"role": "row"})

        if row is None:
            return None

        link = row.find("a", string=lambda t: t and t.strip() == "PDF")
        if link and link.get("href"):
            return link["href"]
        
        return None
    
    def download_pdf(self, mpn: str, output: str) -> str | None:

        pdf_url = self.find_pdf(mpn)
        if pdf_url is None:
            return None
        
        os.makedirs(output, exist_ok=True)

        # link is redirect so we follow it
        # User-Agent so site won't block out request
        response = requests.get(
            pdf_url,
            headers={"User-Agent": "Mozilla/5.0"},
            allow_redirects=True,
            timeout=30
        )
        response.raise_for_status()

        path = f"{output}/{mpn}.pdf"

        with open(path, "wb") as p:
            p.write(response.content)

        return path