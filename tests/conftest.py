import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_session_factory
from app.core.config import Settings, get_settings
from app.db.base import Base
from app.main import create_app


@pytest.fixture
def settings(tmp_path) -> Settings:
    # _env_file=None: tests never read a developer's local .env.
    return Settings(
        _env_file=None,
        database_url="sqlite://",
        upload_dir=tmp_path / "uploads",
        max_upload_size_mb=1,
        max_uncompressed_zip_mb=5,
    )


@pytest.fixture
def session_factory() -> sessionmaker:
    """A fresh in-memory SQLite database per test.

    StaticPool shares one connection, so the request thread and the background
    task thread see the same in-memory database.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def db_session(session_factory):
    with session_factory() as session:
        yield session


@pytest.fixture
def app(session_factory, settings):
    application = create_app()
    application.dependency_overrides[get_session_factory] = lambda: session_factory
    application.dependency_overrides[get_settings] = lambda: settings
    return application


@pytest.fixture
def client(app) -> TestClient:
    # TestClient runs BackgroundTasks before returning, so jobs finish synchronously in tests.
    with TestClient(app) as test_client:
        yield test_client
