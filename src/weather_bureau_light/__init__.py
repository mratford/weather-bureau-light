"""A local rebuild of the old Met Office forecast page."""

from __future__ import annotations

import logging
import os


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("WBL_LOG_LEVEL", "INFO"),
        format="%(levelname)s %(name)s: %(message)s",
    )

    from .app import create_app
    from .config import ConfigError

    try:
        app = create_app()
    except ConfigError as exc:
        raise SystemExit(f"ERROR: {exc}")

    host = os.environ.get("WBL_HOST", "127.0.0.1")
    port = int(os.environ.get("WBL_PORT", "5000"))
    print(f"Weather Bureau Light: http://{host}:{port}/")
    app.run(host=host, port=port, debug=os.environ.get("WBL_DEBUG") == "1")
