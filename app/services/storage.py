"""File storage for uploaded files.

Uploaded files are kept on disk (never in PostgreSQL). Storage paths are built
only from the server-generated job ID and a whitelisted extension, so a
user-supplied filename can never influence where a file is written.
"""

import re
import uuid
from abc import ABC, abstractmethod
from pathlib import Path

from app.core.exceptions import StorageError

_EXTENSION_PATTERN = re.compile(r"^\.[a-z0-9]{1,10}$")


class StorageBackend(ABC):
    """Interface so a different backend (e.g. S3) can be added without touching callers."""

    @abstractmethod
    def save(self, job_id: uuid.UUID, extension: str, data: bytes) -> str:
        """Store the file and return its storage key."""

    @abstractmethod
    def get_path(self, storage_key: str) -> Path:
        """Return a local filesystem path for reading the stored file."""

    @abstractmethod
    def delete(self, storage_key: str) -> None:
        """Remove the stored file if it exists."""


class LocalStorage(StorageBackend):
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def save(self, job_id: uuid.UUID, extension: str, data: bytes) -> str:
        if not isinstance(job_id, uuid.UUID):
            raise StorageError("Job ID must be a UUID.")
        if not _EXTENSION_PATTERN.match(extension):
            raise StorageError(f"Invalid file extension for storage: {extension!r}")

        storage_key = f"{job_id}/upload{extension}"
        path = self.get_path(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return storage_key

    def get_path(self, storage_key: str) -> Path:
        path = (self.root / storage_key).resolve()
        # Defence in depth: never resolve to a location outside the storage root.
        if not path.is_relative_to(self.root):
            raise StorageError("Storage key resolves outside the storage directory.")
        return path

    def delete(self, storage_key: str) -> None:
        path = self.get_path(storage_key)
        path.unlink(missing_ok=True)
        if path.parent != self.root and path.parent.exists() and not any(path.parent.iterdir()):
            path.parent.rmdir()
