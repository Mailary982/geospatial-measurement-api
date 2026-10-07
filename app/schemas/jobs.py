import uuid

from pydantic import BaseModel

from app.db.models import JobStatus


class JobCreatedResponse(BaseModel):
    job_id: uuid.UUID
    status: JobStatus
    status_url: str
    result_url: str
