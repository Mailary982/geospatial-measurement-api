import uuid

import geopandas as gpd
import pytest
from shapely.geometry import box

from app.db.models import GeospatialJob, JobStatus
from app.services import job_service
from tests.helpers import geojson_bytes, line, point, polygon, shapefile_zip_bytes

UPLOAD_URL = "/api/v1/geospatial/jobs"

BLR_SQUARE = polygon((77.50, 12.90), (77.51, 12.90), (77.51, 12.91), (77.50, 12.91), (77.50, 12.90))
BOWTIE = polygon((77.5, 12.9), (77.6, 13.0), (77.6, 12.9), (77.5, 13.0), (77.5, 12.9))


def upload(client, filename: str, data: bytes):
    return client.post(UPLOAD_URL, files={"file": (filename, data, "application/octet-stream")})


def load_job(session_factory, job_id: str) -> GeospatialJob:
    with session_factory() as db:
        job = db.get(GeospatialJob, uuid.UUID(job_id))
        job.result  # load relationship before the session closes
        return job


def job_count(session_factory) -> int:
    with session_factory() as db:
        return db.query(GeospatialJob).count()


# --- successful uploads ---

def test_valid_geojson_upload_creates_job(client, session_factory, settings):
    response = upload(client, "parcels.geojson", geojson_bytes(BLR_SQUARE))

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "QUEUED"
    assert body["status_url"] == f"{UPLOAD_URL}/{body['job_id']}"
    assert body["result_url"] == f"{UPLOAD_URL}/{body['job_id']}/result"

    job = load_job(session_factory, body["job_id"])
    assert job.original_filename == "parcels.geojson"
    assert job.file_format == "geojson"
    assert (settings.upload_dir / job.storage_key).exists()


def test_geojson_job_is_processed_in_background(client, session_factory):
    body = upload(client, "parcels.geojson", geojson_bytes(BLR_SQUARE, line((77.5, 12.95), (77.51, 12.95)))).json()

    job = load_job(session_factory, body["job_id"])
    assert job.status == JobStatus.COMPLETED
    assert job.error_message is None
    assert job.result.total_features == 2
    assert job.result.source_crs == "EPSG:4326"
    assert job.result.measurement_crs == "EPSG:32643"
    assert job.result.total_area == pytest.approx(1_200_618, rel=0.005)
    assert job.result.total_length > 0


def test_shapefile_zip_upload_is_processed(client, session_factory, tmp_path):
    gdf = gpd.GeoDataFrame(geometry=[box(500000, 1400000, 500100, 1400100)], crs="EPSG:32643")

    body = upload(client, "parcels.zip", shapefile_zip_bytes(gdf, tmp_path)).json()

    job = load_job(session_factory, body["job_id"])
    assert job.status == JobStatus.COMPLETED
    assert job.file_format == "shapefile"
    assert job.result.total_area == pytest.approx(10_000)


def test_invalid_geometries_are_skipped_and_counted(client, session_factory):
    body = upload(client, "mixed.geojson", geojson_bytes(BLR_SQUARE, BOWTIE, None)).json()

    job = load_job(session_factory, body["job_id"])
    assert job.status == JobStatus.COMPLETED
    assert job.result.valid_count == 1
    assert job.result.invalid_count == 2
    assert job.result.invalid_features[0]["feature_index"] == 1


# --- rejected uploads (no job, no stored file) ---

@pytest.mark.parametrize(
    "filename, data, status, code",
    [
        ("notes.txt", b"hello", 415, "UNSUPPORTED_FILE_TYPE"),
        ("parcels.shp", b"data", 415, "UNSUPPORTED_FILE_TYPE"),
        ("empty.geojson", b"", 400, "EMPTY_FILE"),
        ("broken.geojson", b"{not json", 400, "INVALID_FILE"),
        ("parcels.zip", b"not a zip", 400, "INVALID_FILE"),
    ],
)
def test_bad_uploads_are_rejected(client, session_factory, settings, filename, data, status, code):
    response = upload(client, filename, data)

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert job_count(session_factory) == 0
    assert not settings.upload_dir.exists() or not any(settings.upload_dir.iterdir())


def test_oversized_upload_returns_413(client, settings):
    data = b" " * (settings.max_upload_size_bytes + 1)

    response = upload(client, "big.geojson", data)

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"


def test_missing_file_field_returns_422(client):
    response = client.post(UPLOAD_URL)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# --- background failures ---

def test_file_with_no_valid_geometry_fails_job(client, session_factory):
    body = upload(client, "bad.geojson", geojson_bytes(BOWTIE)).json()

    job = load_job(session_factory, body["job_id"])
    assert job.status == JobStatus.FAILED
    assert "Self-intersection" in job.error_message
    assert job.result is None


def test_unexpected_processing_error_fails_job_with_generic_message(client, session_factory, monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("internal detail that must not leak")

    monkeypatch.setattr(job_service, "analyze_file", explode)

    body = upload(client, "parcels.geojson", geojson_bytes(point(77.5, 12.9))).json()

    job = load_job(session_factory, body["job_id"])
    assert job.status == JobStatus.FAILED
    assert job.error_message == job_service.UNEXPECTED_ERROR_MESSAGE
