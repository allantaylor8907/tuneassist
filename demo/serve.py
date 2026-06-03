"""Host the Tune Assist demo in a browser.

    pip install ".[serve]"
    python demo/serve.py            # -> http://localhost:8000

Each browser session gets its own isolated, locked-down app process (file access
confined to demo/samples). Equivalent to: textual serve "python -m tuneassist.demo"
"""
import os

from textual_serve.server import Server


def main():
    host = os.environ.get("HOST", "localhost")
    port = int(os.environ.get("PORT", "8000"))
    Server("python -m tuneassist.demo", host=host, port=port,
           title="Tune Assist - demo").serve()


if __name__ == "__main__":
    main()
