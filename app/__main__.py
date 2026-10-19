import logging
import os
import signal
import sys

from waitress import serve

from .config import Config
from .web import create_app


def main():
    logging.basicConfig(
        level=os.environ.get("ZEITBUCH_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = Config.from_env()
    app = create_app(cfg, startup_check=True)
    # Als PID 1 im Container gibt es keinen Default-Handler für SIGTERM.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    # Genau ein Prozess (waitress mit Threads).
    serve(app, host=cfg.bind, port=cfg.port, threads=8, ident="zeitbuch")


if __name__ == "__main__":
    main()
