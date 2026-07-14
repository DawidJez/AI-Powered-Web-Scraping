# test_pipeline.py
from excel_exporter import ExcelExporter
from pdf_downloader import PDFDownloader

records = [{'MPN': 'TMP275-Q1', 'Product page URL': 'https://www.ti.com/product/TMP275-Q1', 'Product image URL': None, 'Subcategory': 'Digital temperature sensors', 'Rating': 'Automotive', 'Package type': 'SOIC, VSSOP', 'Pin count': 8, 'TI functional safety category': None, 'Operating temperature range (°C)': '-40 to 125', 'Package area (mm²)': '14.7, 29.4', 'Package size (L × W) (mm)': '3 x 4.9, 4.9 x 6', 'Price/Quantity (USD)': '$1.012 | 1ku', 'Product status': 'ACTIVE', 'Full URL of the PDF datasheet': 'https://www.ti.com/lit/gpn/TMP275-Q1', 'Full URL of the HTML datasheet': 'https://www.ti.com/document-viewer/TMP275-Q1/datasheet'}, {'MPN': 'TMP275', 'Product page URL': 'https://www.ti.com/product/TMP275', 'Product image URL': None, 'Subcategory': 'Digital temperature sensors', 'Rating': 'Catalog', 'Package type': 'SOIC, VSSOP', 'Pin count': 8, 'TI functional safety category': None, 'Operating temperature range (°C)': '-40 to 125', 'Package area (mm²)': '14.7, 29.4', 'Package size (L × W) (mm)': '3 x 4.9, 4.9 x 6', 'Price/Quantity (USD)': '$0.634 | 1ku', 'Product status': 'ACTIVE', 'Full URL of the PDF datasheet': 'https://www.ti.com/lit/gpn/TMP275', 'Full URL of the HTML datasheet': 'https://www.ti.com/document-viewer/TMP275/datasheet'}]

print("=== Test ExcelExporter ===")
exporter = ExcelExporter()
path = exporter.save(records, "datasheets/exel/test_products.xlsx")
print(f"Zapisano: {path}")

print("\n=== Test PDFDownloader ===")
downloader = PDFDownloader()
results = downloader.download_all(records, url_field="Full URL of the PDF datasheet", name_field="MPN")
print(results)