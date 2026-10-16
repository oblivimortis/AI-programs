"""Entry point: ``python -m pet_hospital_mcp`` runs the stateless HTTP server."""

from __future__ import annotations

import logging

import uvicorn

from pet_hospital_mcp.config import Settings
from pet_hospital_mcp.logging_config import setup_logging
from pet_hospital_mcp.server import create_app


def main() -> None:
    settings = Settings.from_env()
    setup_logging()
    app = create_app(settings)
    uvicorn.run(app, host=settings.mcp_host, port=settings.mcp_port, log_level="info")


if __name__ == "__main__":
    main()