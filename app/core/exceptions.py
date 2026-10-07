"""Application errors.

Every error the API reports on purpose is an AppError subclass with a stable
`code`, an HTTP status and a message that is safe to show to API users.
"""


class AppError(Exception):
    code: str = "APP_ERROR"
    status_code: int = 400

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details


# --- Upload validation (returned directly from POST /jobs) ---

class InvalidFilenameError(AppError):
    code = "INVALID_FILENAME"
    status_code = 400


class EmptyFileError(AppError):
    code = "EMPTY_FILE"
    status_code = 400


class InvalidFileError(AppError):
    code = "INVALID_FILE"
    status_code = 400


class FileTooLargeError(AppError):
    code = "FILE_TOO_LARGE"
    status_code = 413


class UnsupportedFileTypeError(AppError):
    code = "UNSUPPORTED_FILE_TYPE"
    status_code = 415


# --- Job lookup ---

class JobNotFoundError(AppError):
    code = "JOB_NOT_FOUND"
    status_code = 404


class JobNotCompletedError(AppError):
    code = "JOB_NOT_COMPLETED"
    status_code = 409


class JobFailedError(AppError):
    code = "JOB_FAILED"
    status_code = 409


# --- Processing (raised inside the background job; stored as the job's error_message) ---

class ProcessingError(AppError):
    code = "PROCESSING_ERROR"
    status_code = 422


class FileParseError(ProcessingError):
    code = "FILE_PARSE_ERROR"


class EmptyDatasetError(ProcessingError):
    code = "EMPTY_DATASET"


class MissingCRSError(ProcessingError):
    code = "MISSING_CRS"


class UnsupportedCRSError(ProcessingError):
    code = "UNSUPPORTED_CRS"


class NoValidGeometriesError(ProcessingError):
    code = "NO_VALID_GEOMETRIES"


# --- Storage ---

class StorageError(AppError):
    code = "STORAGE_ERROR"
    status_code = 500
