from fastapi import APIRouter, BackgroundTasks, Depends, File, UploadFile
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_db, get_session_factory, get_storage
from app.core.config import Settings, get_settings
from app.schemas.errors import ErrorResponse
from app.schemas.jobs import JobCreatedResponse
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
