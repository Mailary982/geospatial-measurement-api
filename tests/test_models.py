from app.db.models import GeospatialJob, JobStatus, MeasurementResult


def make_job(**overrides) -> GeospatialJob:
    values = dict(
        original_filename="roads.geojson",
        file_format="geojson",
        file_size_bytes=123,
        storage_key="abc/upload.geojson",
    )
    values.update(overrides)
    return GeospatialJob(**values)


def test_new_job_defaults_to_queued(db_session):
    job = make_job()
    db_session.add(job)
    db_session.commit()

    stored = db_session.get(GeospatialJob, job.id)
    assert stored.status == JobStatus.QUEUED
    assert stored.created_at is not None
    assert stored.result is None


def test_job_with_measurement_result_round_trip(db_session):
    job = make_job()
    job.result = MeasurementResult(
        total_features=2,
        valid_count=1,
        invalid_count=1,
        geometry_types=["Polygon"],
        invalid_features=[{"feature_index": 1, "reason": "empty geometry"}],
        source_crs="EPSG:4326",
        measurement_crs="EPSG:32643",
        min_x=77.5, min_y=12.9, max_x=77.6, max_y=13.0,
        total_area=1000.0, area_unit="square_meters",
        total_perimeter=130.0, perimeter_unit="meters",
    )
    db_session.add(job)
    db_session.commit()
    db_session.expire_all()

    stored = db_session.get(GeospatialJob, job.id)
    assert stored.result.geometry_types == ["Polygon"]
    assert stored.result.invalid_features[0]["reason"] == "empty geometry"
    assert stored.result.total_length is None
