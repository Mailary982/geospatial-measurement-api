"""Job service behaviour that is hard to observe through the HTTP API."""

import uuid

import pytest
from sqlalchemy.exc import OperationalError

from app.db.models import GeospatialJob, JobStatus
from app.services import job_service
from app.services.file_validation import ValidatedUpload
from app.services.storage import LocalStorage
from tests.helpers import geojson_bytes, point

UPLOAD = ValidatedUpload(
    filename="points.geojson", extension=".geojson", file_format="geojson",
    data=geojson_bytes(point(77.5, 12.9)),
)


@pytest.fixture
def storage(tmp_path) -> LocalStorage:
    return LocalStorage(tmp_path / "uploads")


def test_create_job_stores_file_and_queues_job(db_session, storage):
    job = job_service.create_job(db_session, storage, UPLOAD)

    assert job.status == JobStatus.QUEUED
    assert job.file_size_bytes == len(UPLOAD.data)
    assert storage.get_path(job.storage_key).read_bytes() == UPLOAD.data


def test_create_job_removes_file_if_database_commit_fails(db_session, storage, monkeypatch):
    def failing_commit():
        raise OperationalError("INSERT", {}, Exception("database is down"))

    monkeypatch.setattr(db_session, "commit", failing_commit)

    with pytest.raises(OperationalError):
        job_service.create_job(db_session, storage, UPLOAD)

    assert not any(storage.root.rglob("*.geojson"))


def test_run_job_marks_processing_before_analysis(session_factory, storage, monkeypatch):
    with session_factory() as db:
        job_id = job_service.create_job(db, storage, UPLOAD).id

    seen_statuses = []
    real_analyze = job_service.analyze_file

    def spy(path, file_format):
        with session_factory() as db:
            seen_statuses.append(db.get(GeospatialJob, job_id).status)
        return real_analyze(path, file_format)

    monkeypatch.setattr(job_service, "analyze_file", spy)

    job_service.run_job(job_id, session_factory, storage)

    with session_factory() as db:
        final_status = db.get(GeospatialJob, job_id).status
    assert seen_statuses == [JobStatus.PROCESSING]
    assert final_status == JobStatus.COMPLETED


def test_run_job_for_unknown_id_does_nothing(session_factory, storage):
    job_service.run_job(uuid.uuid4(), session_factory, storage)  # must not raise


def test_run_job_fails_cleanly_when_stored_file_is_missing(session_factory, storage):
    with session_factory() as db:
        job = job_service.create_job(db, storage, UPLOAD)
    storage.delete(job.storage_key)

    job_service.run_job(job.id, session_factory, storage)

    with session_factory() as db:
        stored = db.get(GeospatialJob, job.id)
        assert stored.status == JobStatus.FAILED
        assert stored.error_message
