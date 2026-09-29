
import os
from pathlib import Path

from dotenv import load_dotenv

REQUIRED_KEYS = ("FIRECRAWL_API_KEY", "GEMINI_API_KEY")


def main():

    # a packaged build unpacks itself into a temp folder, so the bare call would look for .env
    # there instead of where the user put it - the working directory is what they can see
    load_dotenv(Path.cwd() / ".env")
    load_dotenv()

    missing = [name for name in REQUIRED_KEYS if not os.getenv(name)]

    # the packaged build runs wherever the user drops it, so a missing key is the first thing they meet
    if missing:
        print("=== Product data downloader tool ===\n")
        print(f"Cannot start - no API key found for: {', '.join(missing)}\n")
        print("The program reads its keys from a file named .env in the folder you run it from.")
        print("An .env.example file ships next to the program: fill in your keys and rename it to .env.")
        print("It should end up looking like this:\n")

        for name in REQUIRED_KEYS:
            print(f"    {name}=your_key_here")

        print("\nFirecrawl keys: https://www.firecrawl.dev/")
        print("Gemini keys:    https://aistudio.google.com/apikey")
        print("\nIf the keys are meant to come from your team, ask whoever maintains this tool for them.")
        return

    from orchestrator import Orchestrator
    Orchestrator().run()


if __name__ == "__main__":
    main()
