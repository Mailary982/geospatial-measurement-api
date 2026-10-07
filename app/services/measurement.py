"""Measure valid geometries: area, perimeter, length and bounding box.

Pure function of a GeoDataFrame (no FastAPI / DB), so it is easy to unit-test.

Which geometries contribute to which measurement:
- Polygon, MultiPolygon        -> area and perimeter
- LineString, MultiLineString  -> length
- Point, MultiPoint            -> counted only
A measurement is None (not 0) when no geometry of the relevant kind exists.
"""

from dataclasses import dataclass

import geopandas as gpd

from app.utils.crs import choose_measurement_crs, crs_label, metres_per_unit

AREA_UNIT = "square_meters"
LENGTH_UNIT = "meters"

POLYGON_TYPES = ["Polygon", "MultiPolygon"]
LINE_TYPES = ["LineString", "MultiLineString", "LinearRing"]


@dataclass(frozen=True)
class BoundingBox:
    min_x: float
    min_y: float
    max_x: float
    max_y: float


@dataclass(frozen=True)
class Measurements:
    source_crs: str
    measurement_crs: str
    geometry_types: list[str]
    bounding_box: BoundingBox  # in the source CRS
    total_area: float | None
    total_perimeter: float | None
    total_length: float | None

    @property
    def area_unit(self) -> str | None:
        return AREA_UNIT if self.total_area is not None else None

    @property
    def perimeter_unit(self) -> str | None:
        return LENGTH_UNIT if self.total_perimeter is not None else None

    @property
    def length_unit(self) -> str | None:
        return LENGTH_UNIT if self.total_length is not None else None


def measure(gdf: gpd.GeoDataFrame) -> Measurements:
    """Measure a GeoDataFrame that contains only valid, supported geometries."""
    if gdf.empty:
        raise ValueError("Cannot measure an empty GeoDataFrame.")

    measurement_crs = choose_measurement_crs(gdf)
    projected = gdf.to_crs(measurement_crs)
    to_metres = metres_per_unit(measurement_crs)

    polygons = projected[projected.geom_type.isin(POLYGON_TYPES)]
    lines = projected[projected.geom_type.isin(LINE_TYPES)]

    total_area = None
    total_perimeter = None
    if not polygons.empty:
        total_area = float(polygons.area.sum()) * to_metres**2
        total_perimeter = float(polygons.length.sum()) * to_metres

    total_length = None
    if not lines.empty:
        total_length = float(lines.length.sum()) * to_metres

    min_x, min_y, max_x, max_y = (float(value) for value in gdf.total_bounds)

    return Measurements(
        source_crs=crs_label(gdf.crs),
        measurement_crs=crs_label(measurement_crs),
        geometry_types=sorted(set(gdf.geom_type)),
        bounding_box=BoundingBox(min_x=min_x, min_y=min_y, max_x=max_x, max_y=max_y),
        total_area=total_area,
        total_perimeter=total_perimeter,
        total_length=total_length,
    )
