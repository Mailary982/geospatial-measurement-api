"""Job orchestration: ties together storage, the database and geospatial processing."""

import logging
import uuid
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from app.core.exceptions import AppError, NoValidGeometriesError
from app.db.models import GeospatialJob, JobStatus, MeasurementResult
from app.services.file_validation import ValidatedUpload
from app.services.geo_reader import (
    GeometrySplit,
    ensure_crs,
    read_geodataframe,
    split_valid_invalid,
)
from app.services.measurement import Measurements, measure
from app.services.storage import StorageBackend

logger = logging.getLogger(__name__)

UNEXPECTED_ERROR_MESSAGE = "Unexpected error while processing the file."


def create_job(db: Session, storage: StorageBackend, upload: ValidatedUpload) -> GeospatialJob:
    """Store the uploaded file and create a QUEUED job for it."""
    job_id = uuid.uuid4()
    storage_key = storage.save(job_id, upload.extension, upload.data)

    job = GeospatialJob(
        id=job_id,
        original_filename=upload.filename,
        file_format=upload.file_format,
        file_size_bytes=upload.size_bytes,
        storage_key=storage_key,
        status=JobStatus.QUEUED,
    )
    db.add(job)
    try:
        db.commit()
    except Exception:
        db.rollback()
        storage.delete(storage_key)  # don't leave an orphaned file behind
        raise
    return job


def analyze_file(path: Path, file_format: str) -> tuple[GeometrySplit, Measurements]:
    """Parse, validate and measure a stored file. Raises ProcessingError subclasses."""
    gdf = read_geodataframe(path, file_format)
    gdf = ensure_crs(gdf, file_format)

    split = split_valid_invalid(gdf)
    if split.valid.empty:
        first = split.invalid_features[0]
        raise NoValidGeometriesError(
            f"None of the {split.total_features} feature(s) has a valid, supported geometry "
            f"(first problem: feature {first.feature_index}: {first.reason})."
        )
    return split, measure(split.valid)


def run_job(job_id: uuid.UUID, session_factory: sessionmaker, storage: StorageBackend) -> None:
    """Background task: process one job and record COMPLETED or FAILED.

    Uses its own DB session because the request's session is closed by the time this runs.
    """
    with session_factory() as db:
        job = db.get(GeospatialJob, job_id)
        if job is None:
            logger.error("Job %s not found; nothing to process", job_id)
            return

        job.status = JobStatus.PROCESSING
        db.commit()

        try:
            split, measurements = analyze_file(storage.get_path(job.storage_key), job.file_format)
            job.result = build_result(split, measurements)
            job.status = JobStatus.COMPLETED
            db.commit()
            logger.info("Job %s completed (%d features)", job_id, split.total_features)
        except AppError as exc:
            # Expected problem with the user's data: the message is safe to show.
            db.rollback()
            mark_failed(db, job, exc.message)
            logger.info("Job %s failed: %s", job_id, exc.message)
        except Exception:
            # A bug or environment problem: log details, show a generic message.
            db.rollback()
            logger.exception("Job %s failed with an unexpected error", job_id)
            mark_failed(db, job, UNEXPECTED_ERROR_MESSAGE)


def mark_failed(db: Session, job: GeospatialJob, message: str) -> None:
    job.status = JobStatus.FAILED
    job.error_message = message
    db.commit()


def build_result(split: GeometrySplit, measurements: Measurements) -> MeasurementResult:
    box = measurements.bounding_box
    return MeasurementResult(
        total_features=split.total_features,
        valid_count=len(split.valid),
        invalid_count=split.invalid_count,
        geometry_types=measurements.geometry_types,
        invalid_features=[
            {"feature_index": feature.feature_index, "reason": feature.reason}
            for feature in split.invalid_features
        ],
        source_crs=measurements.source_crs,
        measurement_crs=measurements.measurement_crs,
        min_x=box.min_x,
        min_y=box.min_y,
        max_x=box.max_x,
        max_y=box.max_y,
        total_area=measurements.total_area,
        area_unit=measurements.area_unit,
        total_perimeter=measurements.total_perimeter,
        perimeter_unit=measurements.perimeter_unit,
        total_length=measurements.total_length,
        length_unit=measurements.length_unit,
    )
