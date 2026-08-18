# test_prefilter.py
from ai_extractor import AIExtractor
from firecrawl_client import FirecrawlClient

client = FirecrawlClient()
page_data = client.fetch_page("https://www.ti.com/product-category/sensors/products.html")

extractor = AIExtractor()
cleaned = extractor._clean_html(page_data["html"])
chunks = extractor._chunk_segmentation(cleaned, chunk_size=150_000)

print(f"Total chunks: {len(chunks)}")

search_term = "TMCS1170"
matching = [c for c in chunks if search_term in c]
print(f"Chunks containing '{search_term}': {len(matching)}")

for i, c in enumerate(chunks):
    contains = search_term in c
    print(f"Chunk {i+1}: {'CONTAINS' if contains else 'none'} ({len(c)} characters)")