"""Allow ``python -m polybot`` to launch the application."""
from polybot.main import main

if __name__ == "__main__":
    raise SystemExit(main())
