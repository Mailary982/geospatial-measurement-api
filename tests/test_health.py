from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_session_factory


def test_health_returns_ok(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_reports_unavailable_database(app, client, tmp_path):
    # A SQLite file inside a directory that does not exist cannot be opened.
    broken_engine = create_engine(f"sqlite:///{tmp_path / 'missing-dir' / 'db.sqlite'}")
    app.dependency_overrides[get_session_factory] = lambda: sessionmaker(bind=broken_engine)

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["database"] == "unavailable"
