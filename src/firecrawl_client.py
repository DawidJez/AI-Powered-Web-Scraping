from firecrawl import FirecrawlApp
from dotenv import load_dotenv
import os

load_dotenv()

app = FirecrawlApp(api_key=os.getenv("FIRECRAWL_API_KEY"))

result = app.scrape_url(
    url='https://docs.firecrawl.dev',
    formats=['markdown', 'html'],
)

print("Title:", result.metadata.title)
print("Description:", result.metadata.description)
print("Credits used:", result.metadata.credits_used)
print("First 500 characters of markdown", result.markdown[:1000])