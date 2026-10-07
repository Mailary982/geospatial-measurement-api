import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, BigInteger, DateTime, Enum, Float, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class JobStatus(enum.StrEnum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class GeospatialJob(Base):
    """One uploaded file and its processing lifecycle."""

    __tablename__ = "geospatial_jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    original_filename: Mapped[str] = mapped_column(String(255))
    file_format: Mapped[str] = mapped_column(String(20))
    file_size_bytes: Mapped[int] = mapped_column(BigInteger)
    storage_key: Mapped[str] = mapped_column(String(255))
    # Stored as a plain VARCHAR (native_enum=False) so adding a status never needs an ALTER TYPE.
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False, length=20), default=JobStatus.QUEUED, index=True
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    result: Mapped["MeasurementResult | None"] = relationship(
        back_populates="job", cascade="all, delete-orphan", uselist=False
    )


class MeasurementResult(Base):
    """Measurements for a completed job (1:1 with GeospatialJob)."""

    __tablename__ = "measurement_results"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("geospatial_jobs.id", ondelete="CASCADE"), unique=True
    )

    total_features: Mapped[int] = mapped_column(Integer)
    valid_count: Mapped[int] = mapped_column(Integer)
    invalid_count: Mapped[int] = mapped_column(Integer)
    geometry_types: Mapped[list[str]] = mapped_column(JSON)
    # List of {"feature_index": int, "reason": str}, capped to keep the row small.
    invalid_features: Mapped[list[dict]] = mapped_column(JSON)

    source_crs: Mapped[str] = mapped_column(String(100))
    measurement_crs: Mapped[str] = mapped_column(String(100))

    # Bounding box in the source CRS.
    min_x: Mapped[float] = mapped_column(Float)
    min_y: Mapped[float] = mapped_column(Float)
    max_x: Mapped[float] = mapped_column(Float)
    max_y: Mapped[float] = mapped_column(Float)

    # NULL means "not applicable" (e.g. no polygons -> no area), never "zero".
    total_area: Mapped[float | None] = mapped_column(Float)
    area_unit: Mapped[str | None] = mapped_column(String(20))
    total_perimeter: Mapped[float | None] = mapped_column(Float)
    perimeter_unit: Mapped[str | None] = mapped_column(String(20))
    total_length: Mapped[float | None] = mapped_column(Float)
    length_unit: Mapped[str | None] = mapped_column(String(20))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped[GeospatialJob] = relationship(back_populates="result")
