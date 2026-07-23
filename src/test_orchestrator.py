from orchestrator import Orchestrator
from firecrawl_client import FirecrawlClient

def main():
    orchestrator = Orchestrator()
    firecrawl = FirecrawlClient()
    
    orchestrator.test_filter_finder()
    #firecrawl.test()

if __name__ == "__main__":
    main()