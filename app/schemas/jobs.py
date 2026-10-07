import uuid
from datetime import UTC, datetime

from pydantic import BaseModel

from app.db.models import GeospatialJob, JobStatus

DECIMALS = 3


def as_utc(value: datetime) -> datetime:
    # Some databases (e.g. SQLite in tests) return naive datetimes; all our timestamps are UTC.
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def rounded(value: float | None) -> float | None:
    return None if value is None else round(value, DECIMALS)


class JobCreatedResponse(BaseModel):
    job_id: uuid.UUID
    status: JobStatus
    status_url: str
    result_url: str


class FileInfo(BaseModel):
    filename: str
    format: str
    size_bytes: int

    @classmethod
    def from_job(cls, job: GeospatialJob) -> "FileInfo":
        return cls(filename=job.original_filename, format=job.file_format, size_bytes=job.file_size_bytes)


class JobStatusResponse(BaseModel):
    job_id: uuid.UUID
    status: JobStatus
    file: FileInfo
    error_message: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_job(cls, job: GeospatialJob) -> "JobStatusResponse":
        return cls(
            job_id=job.id,
            status=job.status,
            file=FileInfo.from_job(job),
            error_message=job.error_message,
            created_at=as_utc(job.created_at),
            updated_at=as_utc(job.updated_at),
        )


class CRSInfo(BaseModel):
    source: str
    measurement: str


class InvalidFeatureInfo(BaseModel):
    feature_index: int
    reason: str


class GeometrySummary(BaseModel):
    total_features: int
    valid_count: int
    invalid_count: int
    types: list[str]
    invalid_features: list[InvalidFeatureInfo]


class BoundingBoxInfo(BaseModel):
    crs: str
    min_x: float
    min_y: float
    max_x: float
    max_y: float


class MeasurementValues(BaseModel):
    area: float | None
    area_unit: str | None
    perimeter: float | None
    perimeter_unit: str | None
    length: float | None
    length_unit: str | None


class JobResultResponse(BaseModel):
    job_id: uuid.UUID
    status: JobStatus
    file: FileInfo
    crs: CRSInfo
    geometry: GeometrySummary
    bounding_box: BoundingBoxInfo
    measurements: MeasurementValues

    @classmethod
    def from_job(cls, job: GeospatialJob) -> "JobResultResponse":
        result = job.result
        return cls(
            job_id=job.id,
            status=job.status,
            file=FileInfo.from_job(job),
            crs=CRSInfo(source=result.source_crs, measurement=result.measurement_crs),
            geometry=GeometrySummary(
                total_features=result.total_features,
                valid_count=result.valid_count,
                invalid_count=result.invalid_count,
                types=result.geometry_types,
                invalid_features=result.invalid_features,
            ),
            bounding_box=BoundingBoxInfo(
                crs=result.source_crs,
                min_x=result.min_x,
                min_y=result.min_y,
                max_x=result.max_x,
                max_y=result.max_y,
            ),
            measurements=MeasurementValues(
                area=rounded(result.total_area),
                area_unit=result.area_unit,
                perimeter=rounded(result.total_perimeter),
                perimeter_unit=result.perimeter_unit,
                length=rounded(result.total_length),
                length_unit=result.length_unit,
            ),
        )
