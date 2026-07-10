import os
import requests


class PDFDownloader:
    def download(self, pdf_url: str, filename: str, output_dir: str = "./data/datasheets") -> str | None:
        if not pdf_url:
            return None

        os.makedirs(output_dir, exist_ok=True)

        # link is redirect so we follow it
        # User-Agent so site won't block out request
        response = requests.get(
            pdf_url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=30,
        )
        response.raise_for_status()

        if not response.content.startswith(b"%PDF-"):
            return None

        safe_filename = filename if filename.endswith(".pdf") else f"{filename}.pdf"
        path = os.path.join(output_dir, safe_filename)

        with open(path, "wb") as f:
            f.write(response.content)

        return path

    def download_all(self, records: list[dict], url_field: str, name_field: str, output_dir: str = "./data/datasheets") -> list[dict]:
   
        results = []
        for r in records:
            pdf_url = r.get(url_field)
            name = r.get(name_field, "unknown")
            path = self.download(pdf_url, name, output_dir)
            results.append({"name": name, "path": path})
        return results