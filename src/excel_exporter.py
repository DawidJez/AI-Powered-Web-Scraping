import openpyxl
import os
from openpyxl.styles import Font


class ExcelExporter:
    def save(self, records: list[dict], output_path: str) -> str:
        if not records:
            raise ValueError("Brak rekordów do zapisania")

        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        flat_records = [self._flatten_record(r) for r in records]

        columns = []
        for r in flat_records:
            for key in r.keys():
                if key not in columns:
                    columns.append(key)

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Data"

        header_font = Font(name="Arial", bold=True)
        for col_idx, col_name in enumerate(columns, start=1):
            cell = ws.cell(row=1, column=col_idx, value=col_name)
            cell.font = header_font

        default_font = Font(name="Arial")
        for row_idx, record in enumerate(flat_records, start=2):
            for col_idx, col_name in enumerate(columns, start=1):
                value = record.get(col_name)
                cell = ws.cell(row=row_idx, column=col_idx, value=value)
                cell.font = default_font

        output_path = self._get_unique_path(output_path)

        wb.save(output_path)
        return output_path

    def _flatten_record(self, record: dict) -> dict:
        # Converts nested dictionaries (e.g., {'text':..., 'href':...}) into a readable string.
        flat = {}
        for key, value in record.items():
            if isinstance(value, dict):
                text = value.get("text", "")
                href = value.get("href", "")
                flat[key] = f"{text} ({href})" if href else text
            else:
                flat[key] = value
        return flat

    def _get_unique_path(self, output_path: str) -> str:
        if not os.path.exists(output_path):
            return output_path

        base, ext = os.path.splitext(output_path)
        counter = 1
        new_path = f"{base} ({counter}){ext}"

        while os.path.exists(new_path):
            counter += 1
            new_path = f"{base} ({counter}){ext}"
        return new_path