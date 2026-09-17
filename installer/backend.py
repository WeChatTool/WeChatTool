"""Entry point for the bundled CLI; the GUI uses the same backend as Terminal."""

from wechattool.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
