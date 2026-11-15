"""Start the packaged Mr. Financer workspace."""
import argparse
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser

import uvicorn

from mrfinancer import __version__


def open_when_ready(server: uvicorn.Server, url: str) -> None:
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    else:
        return

    try:
        if sys.platform.startswith("linux") and getattr(sys, "frozen", False):
            # Keep the desktop opener on the system libraries instead of the bundle's.
            opener = shutil.which("xdg-open")
            if opener:
                env = os.environ.copy()
                original = env.pop("LD_LIBRARY_PATH_ORIG", None)
                env.pop("LD_LIBRARY_PATH", None)
                if original is not None:
                    env["LD_LIBRARY_PATH"] = original
                subprocess.Popen([opener, url], env=env)
        else:
            webbrowser.open(url)
    except OSError:
        pass  # The address is also displayed in the terminal.


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the local Mr. Financer workspace.")
    parser.add_argument("--version", action="version", version=f"Mr. Financer {__version__}")
    parser.add_argument("--port", type=int, default=8765, help="Local port (default 8765)")
    parser.add_argument("--no-browser", action="store_true", help="Print the URL without opening it")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    if getattr(sys, "frozen", False):
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")

    from mrfinancer.api import app

    host = "127.0.0.1"
    url = f"http://{host}:{args.port}/"
    config = uvicorn.Config(app, host=host, port=args.port, loop="asyncio", http="h11", ws="none")
    server = uvicorn.Server(config)
    print(f"Mr. Financer {__version__} | {url}", flush=True)
    if not args.no_browser:
        threading.Thread(target=open_when_ready, args=(server, url), daemon=True).start()
    server.run()


if __name__ == "__main__":
    main()
