import logging

from fastapi import FastAPI

from app.api.routes import health
from app.core.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Upload a GeoJSON or zipped Shapefile and get area, length and bounding-box measurements.",
    )
    app.include_router(health.router)
    return app


app = create_app()
