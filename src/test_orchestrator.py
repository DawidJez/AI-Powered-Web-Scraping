from orchestrator import Orchestrator


def main():
    orchestrator = Orchestrator()

    print("DEBUG: Starting test() in orchestrator.py")
    orchestrator.test()

if __name__ == "__main__":
    main()