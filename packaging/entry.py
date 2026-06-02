"""PyInstaller entry point. Uses an absolute import so it works as a top-level
script (a relative `from .cli` fails outside package context)."""
from tuneassist.cli import main

if __name__ == "__main__":
    main()
