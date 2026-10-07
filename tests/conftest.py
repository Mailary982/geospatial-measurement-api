import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_session_factory
from app.db.base import Base
from app.main import create_app


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
def app(session_factory):
    application = create_app()
    application.dependency_overrides[get_session_factory] = lambda: session_factory
    return application


@pytest.fixture
def client(app) -> TestClient:
    with TestClient(app) as test_client:
        yield test_client
