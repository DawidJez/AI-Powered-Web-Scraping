from firecrawl_client import FirecrawlClient
from product_finder import ProductFinder

def main():

    client = FirecrawlClient()
    
    page_data = client.fetch_page("https://www.ti.com/product-category/sensors/products.html")
    #print(page_data["html"])

    finder = ProductFinder(page_data["html"])
    row = finder.find_by_mpn("TMCS1170")
    data = finder.extract_row_data(row)
    print(data)


if __name__ == "__main__":
    main()