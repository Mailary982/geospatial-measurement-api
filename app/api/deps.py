from collections.abc import Iterator

from fastapi import Depends
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.db.session import SessionLocal
from app.services.storage import LocalStorage, StorageBackend


def get_session_factory() -> sessionmaker:
    """Session factory used by requests and background jobs.

    Tests override this single dependency to point everything at a test database.
    """
    return SessionLocal


def get_db(session_factory: sessionmaker = Depends(get_session_factory)) -> Iterator[Session]:
    db = session_factory()
    try:
        yield db
    finally:
        db.close()


def get_storage(settings: Settings = Depends(get_settings)) -> StorageBackend:
    return LocalStorage(settings.upload_dir)
