"""Read a stored geospatial file into a GeoDataFrame and validate its geometries.

No FastAPI or database code here, so it can be unit-tested on its own.
"""

import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
from shapely.geometry.base import BaseGeometry
from shapely.validation import explain_validity

from app.core.exceptions import EmptyDatasetError, FileParseError, MissingCRSError
from app.services.file_validation import find_shapefile_member

logger = logging.getLogger(__name__)

SUPPORTED_GEOMETRY_TYPES = {
    "Point", "MultiPoint",
    "LineString", "MultiLineString", "LinearRing",
    "Polygon", "MultiPolygon",
}

# Only the first N invalid features are reported individually (the count is always exact).
MAX_REPORTED_INVALID_FEATURES = 50

FORMAT_LABELS = {"geojson": "GeoJSON", "shapefile": "a Shapefile"}


@dataclass(frozen=True)
class InvalidFeature:
    feature_index: int  # 0-based position of the feature in the file
    reason: str


@dataclass
class GeometrySplit:
    valid: gpd.GeoDataFrame
    invalid_features: list[InvalidFeature]  # capped at MAX_REPORTED_INVALID_FEATURES
    invalid_count: int
    total_features: int


def read_geodataframe(path: Path, file_format: str) -> gpd.GeoDataFrame:
    source = shapefile_source(path) if file_format == "shapefile" else str(path)
    try:
        gdf = gpd.read_file(source)
    except Exception:
        # GDAL/pyogrio raise many different error types for malformed input, and
        # their messages contain server file paths. Log the details, return a clean message.
        logger.warning("Failed to parse %s file %s", file_format, path, exc_info=True)
        raise FileParseError(
            f"Could not read the file as {FORMAT_LABELS[file_format]}. It may be corrupted."
        ) from None

    if not isinstance(gdf, gpd.GeoDataFrame):
        raise FileParseError("The file does not contain a geometry column.")
    if gdf.empty:
        raise EmptyDatasetError("The file contains no features.")
    return gdf


def shapefile_source(zip_path: Path) -> str:
    """GDAL path to the .shp inside the ZIP, read in place via /vsizip/ (no extraction)."""
    with zipfile.ZipFile(zip_path) as archive:
        member = find_shapefile_member(archive.namelist())
    return f"/vsizip/{zip_path.resolve().as_posix()}/{member}"


def ensure_crs(gdf: gpd.GeoDataFrame, file_format: str) -> gpd.GeoDataFrame:
    if gdf.crs is not None:
        return gdf
    if file_format == "geojson":
        # RFC 7946: GeoJSON coordinates are always WGS84 longitude/latitude.
        return gdf.set_crs("EPSG:4326")
    raise MissingCRSError(
        "The shapefile's coordinate system is missing or could not be read from its .prj file."
    )


def invalid_reason(geometry: BaseGeometry | None) -> str | None:
    """Why a geometry cannot be measured, or None if it is fine."""
    if geometry is None or geometry.is_empty:
        return "empty geometry"
    if geometry.geom_type not in SUPPORTED_GEOMETRY_TYPES:
        return f"unsupported geometry type: {geometry.geom_type}"
    if not geometry.is_valid:
        return explain_validity(geometry)
    return None


def split_valid_invalid(gdf: gpd.GeoDataFrame) -> GeometrySplit:
    valid_mask = []
    invalid_features = []
    invalid_count = 0

    for position, geometry in enumerate(gdf.geometry):
        reason = invalid_reason(geometry)
        valid_mask.append(reason is None)
        if reason is not None:
            invalid_count += 1
            if len(invalid_features) < MAX_REPORTED_INVALID_FEATURES:
                invalid_features.append(InvalidFeature(feature_index=position, reason=reason))

    return GeometrySplit(
        valid=gdf[valid_mask],
        invalid_features=invalid_features,
        invalid_count=invalid_count,
        total_features=len(gdf),
    )
