from bs4 import BeautifulSoup # needed for searching data hidden inside the html

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

        return max(candidates, key=lambda r: len(r.find_all(attrs={"role": "gridcell"}))) # 
        
    def extract_row_data(self, row) -> dict:
        
        if row is None:
            return {}
        
        data = {}
        cells = row.find_all("div", attrs={"role": "gridcell"})
        for cell in cells:
            col_id = cell.get("col-id")
            value_el = cell.select_one(".ag-cell-value")
            text = value_el.get_text(strip=True) if value_el else cell.get_text(strip=True)

            link = cell.find("a")
            if link and link.get("href"):
                data[col_id] = {"text": text, "href": link["href"]}
            else:
                data[col_id] = text

        return data
    