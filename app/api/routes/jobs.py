import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, UploadFile
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_db, get_session_factory, get_storage
from app.core.config import Settings, get_settings
from app.schemas.errors import ErrorResponse
from app.schemas.jobs import JobCreatedResponse, JobResultResponse, JobStatusResponse
from app.services import job_service
from app.services.file_validation import validate_upload
from app.services.storage import StorageBackend

API_PREFIX = "/api/v1/geospatial"

router = APIRouter(prefix=API_PREFIX, tags=["geospatial jobs"])


@router.post(
    "/jobs",
    status_code=201,
    response_model=JobCreatedResponse,
    summary="Upload a GeoJSON or zipped Shapefile for measurement",
    responses={
        400: {"model": ErrorResponse, "description": "Empty, malformed or incomplete file"},
        413: {"model": ErrorResponse, "description": "File too large"},
        415: {"model": ErrorResponse, "description": "Unsupported file type"},
        422: {"model": ErrorResponse, "description": "Missing 'file' form field"},
    },
)
def create_job(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(description="A .geojson/.json file, or a .zip with .shp, .shx, .dbf and .prj"),
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
    settings: Settings = Depends(get_settings),
    session_factory: sessionmaker = Depends(get_session_factory),
) -> JobCreatedResponse:
    upload = validate_upload(file.filename, file.file, settings)
    job = job_service.create_job(db, storage, upload)

    # Runs after the response is sent; the client polls the status URL.
    background_tasks.add_task(job_service.run_job, job.id, session_factory, storage)

    return JobCreatedResponse(
        job_id=job.id,
        status=job.status,
        status_url=f"{API_PREFIX}/jobs/{job.id}",
        result_url=f"{API_PREFIX}/jobs/{job.id}/result",
    )


@router.get(
    "/jobs/{job_id}",
    response_model=JobStatusResponse,
    summary="Get a job's processing status",
    responses={404: {"model": ErrorResponse, "description": "Unknown job"}},
)
def get_job_status(job_id: uuid.UUID, db: Session = Depends(get_db)) -> JobStatusResponse:
    return JobStatusResponse.from_job(job_service.get_job(db, job_id))


@router.get(
    "/jobs/{job_id}/result",
    response_model=JobResultResponse,
    summary="Get the measurements of a completed job",
    responses={
        404: {"model": ErrorResponse, "description": "Unknown job"},
        409: {"model": ErrorResponse, "description": "Job not completed yet, or failed"},
    },
)
def get_job_result(job_id: uuid.UUID, db: Session = Depends(get_db)) -> JobResultResponse:
    return JobResultResponse.from_job(job_service.get_completed_job(db, job_id))
