from firecrawl_client import FirecrawlClient
from product_finder import ProductFinder
from ai_extractor import AIExtractor
import time
from excel_exporter import ExcelExporter
from pdf_downloader import PDFDownloader

def main():
    
    client = FirecrawlClient()
    
    #page_data = client.fetch_page("https://www.ti.com/product-category/sensors/products.html")
    product_number = "TMCS1170"

    pages = client.fetch_all_pages("https://www.ti.com/product-category/sensors/products.html")
    markdown_pages = [p["markdown"] for p in pages]
    merged = client.merge_pages(pages)

###################################################
    print("Hardcoded version status: START\n")
    start1 = time.time()

    finder = ProductFinder(merged["html"])
    row = finder.find_by_mpn(product_number)
    data = finder.extract_row_data(row)
    print(f"{data}\n")

    end1 = time.time()
    time1 = (end1 - start1)
    print("Hardcoded version status: END")
    print(f"Time: {time1}\n\n\n")
###################################################

    print("AI version status: START\n")
    start2 = time.time()
    
    extractor = AIExtractor()
    records = extractor.extract(
        markdown_pages,
        f"""
            Find every element on the page whose MPN (product number) matches "{product_number}".

            There may be zero, one, or multiple such elements on the page - for example,
            the same MPN could belong to different manufacturers, appear in different
            subcategories, or be rendered multiple times in the page's underlying structure
            (e.g. once as a summary/header entry and once as a detailed row).

            Do NOT assume there is only one correct match. Do NOT merge, filter, or discard
            any occurrence - return every single one you find as a separate object,
            even if some occurrences have mostly empty/null fields.
            Do NOT guess or fabricate values for missing fields.

            For each matching occurrence, extract the following fields:
            - MPN
            - Product page URL
            - Product image URL
            - Subcategory
            - Rating
            - Package type
            - Pin count
            - TI functional safety category
            - Operating temperature range (°C)
            - Package area (mm²)
            - Package size (L × W) (mm)
            - Price/Quantity (USD)
            - Product status
            - Full URL of the PDF datasheet
            - Full URL of the HTML datasheet

            Return a JSON array containing one object per occurrence found.
            If no matches are found at all, return an empty array [].
        """,
        search_term=product_number
    )
    print(records)
    print("\n")

    end2 = time.time()
    time2 = (end2 - start2)
    print("AI version status: END")
    print(f"Time: {time2}")
    print(f"\nPorównanie: hardcoded {time1:.2f}s vs AI {time2:.2f}s")

    excel_exporter = ExcelExporter()
    excel_exporter.save(records, "data/exel/products.xlsx")

    downloader = PDFDownloader()
    results = downloader.download_all(records, url_field="Full URL of the PDF datasheet", name_field="MPN")
    print(results)


if __name__ == "__main__":
    main()