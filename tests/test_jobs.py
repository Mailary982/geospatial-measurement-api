import uuid

import pytest

from app.db.models import GeospatialJob
from tests.helpers import geojson_bytes, line, point, polygon

JOBS_URL = "/api/v1/geospatial/jobs"

BLR_SQUARE = polygon((77.50, 12.90), (77.51, 12.90), (77.51, 12.91), (77.50, 12.91), (77.50, 12.90))
BOWTIE = polygon((77.5, 12.9), (77.6, 13.0), (77.6, 12.9), (77.5, 13.0), (77.5, 12.9))


def submit(client, *geometries, filename="data.geojson") -> str:
    response = client.post(JOBS_URL, files={"file": (filename, geojson_bytes(*geometries))})
    assert response.status_code == 201
    return response.json()["job_id"]


def insert_queued_job(session_factory) -> str:
    with session_factory() as db:
        job = GeospatialJob(
            original_filename="waiting.geojson", file_format="geojson",
            file_size_bytes=10, storage_key="x/upload.geojson",
        )
        db.add(job)
        db.commit()
        return str(job.id)


# --- status endpoint ---

def test_status_of_completed_job(client):
    job_id = submit(client, BLR_SQUARE, filename="parcels.geojson")

    response = client.get(f"{JOBS_URL}/{job_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job_id
    assert body["status"] == "COMPLETED"
    assert body["file"]["filename"] == "parcels.geojson"
    assert body["file"]["format"] == "geojson"
    assert body["file"]["size_bytes"] > 0
    assert body["error_message"] is None
    assert body["created_at"].endswith(("Z", "+00:00"))
    assert body["updated_at"] >= body["created_at"]


def test_status_of_failed_job_includes_error(client):
    job_id = submit(client, BOWTIE)

    body = client.get(f"{JOBS_URL}/{job_id}").json()

    assert body["status"] == "FAILED"
    assert "Self-intersection" in body["error_message"]


def test_status_of_queued_job(client, session_factory):
    job_id = insert_queued_job(session_factory)

    body = client.get(f"{JOBS_URL}/{job_id}").json()

    assert body["status"] == "QUEUED"


def test_status_of_unknown_job_returns_404(client):
    response = client.get(f"{JOBS_URL}/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "JOB_NOT_FOUND"


def test_non_uuid_job_id_returns_422(client):
    response = client.get(f"{JOBS_URL}/not-a-uuid")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# --- result endpoint ---

def test_result_of_polygon_job(client):
    job_id = submit(client, BLR_SQUARE, filename="parcels.geojson")

    response = client.get(f"{JOBS_URL}/{job_id}/result")

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job_id
    assert body["status"] == "COMPLETED"
    assert body["file"]["filename"] == "parcels.geojson"
    assert body["crs"] == {"source": "EPSG:4326", "measurement": "EPSG:32643"}
    assert body["geometry"] == {
        "total_features": 1, "valid_count": 1, "invalid_count": 0,
        "types": ["Polygon"], "invalid_features": [],
    }
    assert body["bounding_box"] == {
        "crs": "EPSG:4326", "min_x": 77.5, "min_y": 12.9, "max_x": 77.51, "max_y": 12.91,
    }
    measurements = body["measurements"]
    assert measurements["area"] == pytest.approx(1_200_618, rel=0.005)
    assert measurements["area_unit"] == "square_meters"
    assert measurements["perimeter_unit"] == "meters"
    assert measurements["length"] is None
    assert measurements["length_unit"] is None


def test_result_values_are_rounded_to_three_decimals(client):
    job_id = submit(client, BLR_SQUARE)

    area = client.get(f"{JOBS_URL}/{job_id}/result").json()["measurements"]["area"]

    assert area == round(area, 3)


def test_result_of_line_job_has_length_but_no_area(client):
    job_id = submit(client, line((77.50, 12.95), (77.51, 12.95)))

    measurements = client.get(f"{JOBS_URL}/{job_id}/result").json()["measurements"]

    assert measurements["length"] == pytest.approx(1085, rel=0.01)
    assert measurements["length_unit"] == "meters"
    assert measurements["area"] is None
    assert measurements["perimeter"] is None


def test_result_of_point_job_has_no_measurements(client):
    job_id = submit(client, point(77.5, 12.9), point(77.6, 13.0))

    body = client.get(f"{JOBS_URL}/{job_id}/result").json()

    assert body["geometry"]["types"] == ["Point"]
    assert body["bounding_box"]["max_x"] == 77.6
    assert all(value is None for value in body["measurements"].values())


def test_result_reports_skipped_invalid_features(client):
    job_id = submit(client, BLR_SQUARE, BOWTIE, None)

    geometry = client.get(f"{JOBS_URL}/{job_id}/result").json()["geometry"]

    assert geometry["total_features"] == 3
    assert geometry["valid_count"] == 1
    assert geometry["invalid_count"] == 2
    assert [f["feature_index"] for f in geometry["invalid_features"]] == [1, 2]
    assert geometry["invalid_features"][1]["reason"] == "empty geometry"


def test_result_of_queued_job_returns_409(client, session_factory):
    job_id = insert_queued_job(session_factory)

    response = client.get(f"{JOBS_URL}/{job_id}/result")

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "JOB_NOT_COMPLETED"
    assert error["details"] == {"status": "QUEUED"}


def test_result_of_failed_job_returns_409_with_reason(client):
    job_id = submit(client, BOWTIE)

    response = client.get(f"{JOBS_URL}/{job_id}/result")

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "JOB_FAILED"
    assert "Self-intersection" in error["details"]["error_message"]


def test_result_of_unknown_job_returns_404(client):
    response = client.get(f"{JOBS_URL}/{uuid.uuid4()}/result")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "JOB_NOT_FOUND"
