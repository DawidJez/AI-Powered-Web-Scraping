# test_pipeline.py
from excel_exporter import ExcelExporter
from pdf_downloader import PDFDownloader

records = [
    {
        'MPN': 'TMCS1170',
        'Product page URL': 'https://www.ti.com/product/TMCS1170',
        'Product image URL': None,
        'Subcategory': None,
        'Rating': None,
        'Package type': None,
        'Pin count': None,
        'TI functional safety category': None,
        'Operating temperature range (°C)': None,
        'Package area (mm²)': None,
        'Package size (L × W) (mm)': None,
        'Price/Quantity (USD)': None,
        'Product status': None,
        'Full URL of the PDF datasheet': 'https://www.ti.com/lit/gpn/TMCS1170',
        'Full URL of the HTML datasheet': 'https://www.ti.com/document-viewer/TMCS1170/datasheet',
    },
    {
        'MPN': 'TMCS1170',
        'Product page URL': None,
        'Product image URL': None,
        'Subcategory': 'Hall-effect current sensors',
        'Rating': 'Catalog',
        'Package type': 'VQFN-HR',
        'Pin count': '12',
        'TI functional safety category': None,
        'Operating temperature range (°C)': '-40 to 125',
        'Package area (mm²)': '9',
        'Package size (L × W) (mm)': '3 x 3',
        'Price/Quantity (USD)': '$0.594 | 1ku',
        'Product status': 'ACTIVE',
        'Full URL of the PDF datasheet': None,
        'Full URL of the HTML datasheet': None,
    },
]

print("=== Test ExcelExporter ===")
exporter = ExcelExporter()
path = exporter.save(records, "output/test_products.xlsx")
print(f"Zapisano: {path}")

print("\n=== Test PDFDownloader ===")
downloader = PDFDownloader()
results = downloader.download_all(records, url_field="Full URL of the PDF datasheet", name_field="MPN")
print(results)