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
            Find the product whose MPN (product number) is exactly "{product_number}".
            Ignore all other products on the page.

            This product may appear more than once in the page's underlying structure
            (e.g. once as a collapsed/summary/group entry that lacks real data,
            and once as the actual detailed row with the real field values).
            These are NOT separate products - they are duplicate renderings of the same one.

            In that case, identify which occurrence is the real, data-bearing entry
            (the one where most of the requested fields below have actual non-null values)
            and return ONLY that one. Discard occurrences that are mostly empty/null -
            those are structural placeholders, not real product data.

            Do NOT merge fields from multiple occurrences into one object.
            Do NOT guess or fabricate values for missing fields.

            For this one product, extract the following fields:
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

            Return a JSON array with exactly ONE object for this product,
            or an empty array [] if the MPN is not found on the page at all.
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