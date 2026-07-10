from firecrawl_client import FirecrawlClient
from product_finder import ProductFinder
from ai_extractor import AIExtractor
import time

def main():

    client = FirecrawlClient()
    
    page_data = client.fetch_page("https://www.ti.com/product-category/sensors/products.html")
    product_number = "TMCS1170"

    print("Hardcodedd version status: START\n")
    start1 = time.time()

    finder = ProductFinder(page_data["html"])
    row = finder.find_by_mpn(product_number)
    data = finder.extract_row_data(row)
    print(f"{data}\n")

    end1 = time.time()
    time1 = (end1 - start1)
    print("Hardcodedd version status: END")
    print(f"Time: {time1}\n\n\n")

    #data = finder.find_pdf("TMCS1170")
    #pdf_path = finder.download_pdf("TMCS1170", output="./data/datasheets")
    #print(pdf_path)

    print("AI version status: START\n")
    start2 = time.time()

    extractor = AIExtractor()
    records = extractor.extract(
        page_data["html"], 
        f"""
            Extract the following fields for each matching product:

            - MPN (Product number: {product_number})
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

            Return one JSON object per product. If a field is not available, set its value to null. Always return absolute URLs.
        """
    )
    print(records)
    print("\n")

    end2 = time.time()
    time2 = (end2 - start2)
    print("AI version status: END")
    print(f"Time: {time2}")
    print(f"\nPorównanie: hardcoded {time1:.2f}s vs AI {time2:.2f}s")


if __name__ == "__main__":
    main()