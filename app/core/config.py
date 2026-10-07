from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, read from environment variables (or a .env file).

    Variable names are case-insensitive, e.g. DATABASE_URL sets `database_url`.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Geospatial File Measurement API"
    database_url: str = "postgresql+psycopg://geo:geo@localhost:5432/geo"
    upload_dir: Path = Path("data/uploads")
    max_upload_size_mb: int = 20
    max_uncompressed_zip_mb: int = 100
    log_level: str = "INFO"

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024

    @property
    def max_uncompressed_zip_bytes(self) -> int:
        return self.max_uncompressed_zip_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
