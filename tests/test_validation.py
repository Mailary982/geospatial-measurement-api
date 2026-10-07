import io
import json

import pytest

from app.core.config import Settings
from app.core.exceptions import (
    EmptyFileError,
    FileTooLargeError,
    InvalidFileError,
    InvalidFilenameError,
    UnsupportedFileTypeError,
)
from app.services.file_validation import clean_filename, validate_upload
from tests.helpers import geojson_bytes, make_zip

POINT = {"type": "Point", "coordinates": [77.59, 12.97]}
SHAPEFILE_MEMBERS = {
    "parcels.shp": b"shp", "parcels.shx": b"shx", "parcels.dbf": b"dbf", "parcels.prj": b"prj",
}


@pytest.fixture
def settings() -> Settings:
    return Settings(max_upload_size_mb=1, max_uncompressed_zip_mb=1)


def validate(filename, data: bytes, settings):
    return validate_upload(filename, io.BytesIO(data), settings)


# --- filename ---

@pytest.mark.parametrize("filename", [None, "", "   ", "..", "bad\x00name.geojson", "a" * 300 + ".json"])
def test_invalid_filenames_are_rejected(filename, settings):
    with pytest.raises(InvalidFilenameError):
        validate(filename, geojson_bytes(POINT), settings)


@pytest.mark.parametrize(
    "raw, expected",
    [("../../etc/roads.geojson", "roads.geojson"), ("C:\\Users\\me\\roads.geojson", "roads.geojson")],
)
def test_client_directory_parts_are_stripped(raw, expected):
    assert clean_filename(raw) == expected


# --- extension ---

@pytest.mark.parametrize("filename", ["notes.txt", "data.csv", "noextension", "roads.kml"])
def test_unsupported_extensions_are_rejected(filename, settings):
    with pytest.raises(UnsupportedFileTypeError):
        validate(filename, b"content", settings)


def test_bare_shp_is_rejected_with_zip_hint(settings):
    with pytest.raises(UnsupportedFileTypeError, match=r"\.zip"):
        validate("parcels.shp", b"content", settings)


def test_extension_is_case_insensitive(settings):
    upload = validate("ROADS.GeoJSON", geojson_bytes(POINT), settings)
    assert upload.file_format == "geojson"
    assert upload.extension == ".geojson"


# --- size / emptiness ---

def test_empty_file_is_rejected(settings):
    with pytest.raises(EmptyFileError):
        validate("roads.geojson", b"", settings)


def test_whitespace_only_file_is_rejected(settings):
    with pytest.raises(EmptyFileError):
        validate("roads.geojson", b"  \n\t ", settings)


def test_oversized_file_is_rejected(settings):
    too_big = b" " * (settings.max_upload_size_bytes + 1)
    with pytest.raises(FileTooLargeError):
        validate("roads.geojson", too_big, settings)


# --- GeoJSON content ---

def test_valid_geojson_is_accepted(settings):
    data = geojson_bytes(POINT)
    upload = validate("points.geojson", data, settings)
    assert upload.filename == "points.geojson"
    assert upload.size_bytes == len(data)


def test_geojson_with_utf8_bom_is_accepted(settings):
    upload = validate("points.json", b"\xef\xbb\xbf" + geojson_bytes(POINT), settings)
    assert upload.file_format == "geojson"


def test_bare_geometry_geojson_is_accepted(settings):
    validate("point.geojson", json.dumps(POINT).encode(), settings)


@pytest.mark.parametrize(
    "content",
    [b"this is not json", b"\xff\xfe\x00binary", b'{"type": "Unknown"}', b"[1, 2, 3]", b'{"no": "type"}'],
)
def test_non_geojson_content_is_rejected(content, settings):
    with pytest.raises(InvalidFileError):
        validate("roads.geojson", content, settings)


def test_extension_is_not_trusted(settings):
    # A ZIP renamed to .geojson must fail the content check.
    with pytest.raises(InvalidFileError):
        validate("roads.geojson", make_zip(SHAPEFILE_MEMBERS), settings)


def test_feature_collection_without_features_is_rejected(settings):
    with pytest.raises(EmptyFileError):
        validate("empty.geojson", b'{"type": "FeatureCollection", "features": []}', settings)


# --- Shapefile ZIP content ---

def test_complete_shapefile_zip_is_accepted(settings):
    upload = validate("parcels.zip", make_zip(SHAPEFILE_MEMBERS), settings)
    assert upload.file_format == "shapefile"


def test_shapefile_in_subfolder_and_macos_junk_is_accepted(settings):
    members = {f"export/{name}": content for name, content in SHAPEFILE_MEMBERS.items()}
    members["__MACOSX/export/._parcels.shp"] = b"junk"
    validate("parcels.zip", make_zip(members), settings)


def test_non_zip_content_is_rejected(settings):
    with pytest.raises(InvalidFileError, match="not a valid ZIP"):
        validate("parcels.zip", b"definitely not a zip", settings)


def test_truncated_zip_is_rejected(settings):
    data = make_zip(SHAPEFILE_MEMBERS)
    with pytest.raises(InvalidFileError):
        validate("parcels.zip", data[: len(data) // 2], settings)


def test_zip_without_shp_is_rejected(settings):
    with pytest.raises(InvalidFileError, match=r"\.shp"):
        validate("parcels.zip", make_zip({"readme.txt": b"hello"}), settings)


def test_zip_with_two_shapefiles_is_rejected(settings):
    members = dict(SHAPEFILE_MEMBERS)
    members.update({"roads.shp": b"", "roads.shx": b"", "roads.dbf": b"", "roads.prj": b""})
    with pytest.raises(InvalidFileError, match="exactly one"):
        validate("parcels.zip", make_zip(members), settings)


@pytest.mark.parametrize("missing", [".shx", ".dbf", ".prj"])
def test_zip_missing_companion_file_is_rejected(missing, settings):
    members = {name: content for name, content in SHAPEFILE_MEMBERS.items() if not name.endswith(missing)}
    with pytest.raises(InvalidFileError) as error:
        validate("parcels.zip", make_zip(members), settings)
    assert error.value.details == {"missing": [missing]}


def test_zip_bomb_is_rejected_before_decompressing(settings):
    # 2 MB of zeros compresses to a few KB but exceeds the 1 MB uncompressed limit.
    members = dict(SHAPEFILE_MEMBERS)
    members["parcels.dbf"] = b"\0" * (2 * 1024 * 1024)
    with pytest.raises(InvalidFileError, match="uncompressed limit"):
        validate("parcels.zip", make_zip(members), settings)
