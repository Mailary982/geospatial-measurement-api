"""Fast, synchronous checks on an uploaded file.

These run inside the upload request so obviously bad files get an immediate
4xx response. They check structure only (is it JSON? is it a complete
shapefile ZIP?). Full geospatial parsing happens later in the background job.
"""

import io
import json
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import BinaryIO

from app.core.config import Settings
from app.core.exceptions import (
    EmptyFileError,
    FileTooLargeError,
    InvalidFileError,
    InvalidFilenameError,
    UnsupportedFileTypeError,
)

# extension -> file format
SUPPORTED_EXTENSIONS = {".geojson": "geojson", ".json": "geojson", ".zip": "shapefile"}

SHAPEFILE_PART_EXTENSIONS = {".shp", ".shx", ".dbf", ".prj", ".cpg"}
SHAPEFILE_REQUIRED_COMPANIONS = (".shx", ".dbf", ".prj")

GEOJSON_TYPES = {
    "FeatureCollection", "Feature", "Point", "MultiPoint", "LineString",
    "MultiLineString", "Polygon", "MultiPolygon", "GeometryCollection",
}

MAX_FILENAME_LENGTH = 255
READ_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class ValidatedUpload:
    filename: str
    extension: str
    file_format: str
    data: bytes

    @property
    def size_bytes(self) -> int:
        return len(self.data)


def validate_upload(filename: str | None, stream: BinaryIO, settings: Settings) -> ValidatedUpload:
    name = clean_filename(filename)
    extension, file_format = detect_format(name)
    data = read_with_limit(stream, settings.max_upload_size_bytes, settings.max_upload_size_mb)

    if not data.strip():
        raise EmptyFileError("The uploaded file is empty.")

    if file_format == "geojson":
        check_geojson(data)
    else:
        check_shapefile_zip(data, settings.max_uncompressed_zip_bytes, settings.max_uncompressed_zip_mb)

    return ValidatedUpload(filename=name, extension=extension, file_format=file_format, data=data)


def clean_filename(filename: str | None) -> str:
    """Return the base filename, without any client-side directory part."""
    if not filename or not filename.strip():
        raise InvalidFilenameError("A filename is required.")

    name = PurePosixPath(filename.replace("\\", "/")).name.strip()
    if not name or name in {".", ".."}:
        raise InvalidFilenameError("The filename is not valid.")
    if len(name) > MAX_FILENAME_LENGTH:
        raise InvalidFilenameError(f"The filename must be at most {MAX_FILENAME_LENGTH} characters.")
    if any(ord(char) < 32 for char in name):
        raise InvalidFilenameError("The filename contains control characters.")
    return name


def detect_format(filename: str) -> tuple[str, str]:
    extension = PurePosixPath(filename).suffix.lower()

    if extension in SHAPEFILE_PART_EXTENSIONS:
        raise UnsupportedFileTypeError(
            "Shapefiles must be uploaded as a single .zip containing the .shp, .shx, .dbf and .prj files."
        )
    if extension not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise UnsupportedFileTypeError(
            f"Unsupported file type '{extension or '(none)'}'. Supported types: {supported}."
        )
    return extension, SUPPORTED_EXTENSIONS[extension]


def read_with_limit(stream: BinaryIO, max_bytes: int, max_mb: int) -> bytes:
    """Read the stream in chunks and stop as soon as it exceeds the size limit."""
    buffer = bytearray()
    while chunk := stream.read(READ_CHUNK_SIZE):
        buffer.extend(chunk)
        if len(buffer) > max_bytes:
            raise FileTooLargeError(f"File exceeds the {max_mb} MB upload limit.")
    return bytes(buffer)


def check_geojson(data: bytes) -> None:
    try:
        text = data.decode("utf-8-sig")  # tolerate a UTF-8 byte-order mark
    except UnicodeDecodeError:
        raise InvalidFileError("GeoJSON file must be UTF-8 encoded text.") from None

    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidFileError(
            f"File is not valid JSON (line {exc.lineno}, column {exc.colno})."
        ) from None

    if not isinstance(document, dict) or document.get("type") not in GEOJSON_TYPES:
        raise InvalidFileError(
            "JSON is not GeoJSON: expected a top-level \"type\" of FeatureCollection, "
            "Feature or a geometry type."
        )
    if document["type"] == "FeatureCollection" and not document.get("features"):
        raise EmptyFileError("GeoJSON FeatureCollection contains no features.")


def check_shapefile_zip(data: bytes, max_uncompressed_bytes: int, max_uncompressed_mb: int) -> None:
    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise InvalidFileError("File is not a valid ZIP archive.")

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = [
                info for info in archive.infolist()
                if not info.is_dir() and not _is_os_metadata(info.filename)
            ]
            # Zip-bomb guard: check the declared uncompressed size before decompressing anything.
            if sum(info.file_size for info in members) > max_uncompressed_bytes:
                raise InvalidFileError(
                    f"ZIP contents exceed the {max_uncompressed_mb} MB uncompressed limit."
                )
            corrupted_member = archive.testzip()
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, EOFError, ValueError):
        raise InvalidFileError(
            "ZIP archive could not be read (corrupted, encrypted or unsupported compression)."
        ) from None

    if corrupted_member is not None:
        raise InvalidFileError(f"ZIP archive is corrupted (bad entry: {corrupted_member}).")

    _check_shapefile_members([info.filename for info in members])


def _check_shapefile_members(names: list[str]) -> None:
    shp_files = [name for name in names if name.lower().endswith(".shp")]
    if not shp_files:
        raise InvalidFileError("ZIP does not contain a .shp file.")
    if len(shp_files) > 1:
        raise InvalidFileError(f"ZIP must contain exactly one shapefile; found {len(shp_files)}.")

    base = shp_files[0][: -len(".shp")].lower()
    present = {name.lower() for name in names}
    missing = [ext for ext in SHAPEFILE_REQUIRED_COMPANIONS if base + ext not in present]
    if missing:
        raise InvalidFileError(
            f"Shapefile is missing required file(s): {', '.join(missing)}. "
            "A shapefile needs .shp, .shx, .dbf and .prj (the .prj defines the coordinate system).",
            details={"missing": missing},
        )


def _is_os_metadata(name: str) -> bool:
    """macOS adds __MACOSX/ folders and ._ files when zipping; ignore them."""
    return name.startswith("__MACOSX/") or PurePosixPath(name).name.startswith("._")
