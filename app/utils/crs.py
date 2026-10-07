"""Choosing a coordinate reference system (CRS) suitable for measuring.

Why: in a geographic CRS such as EPSG:4326 coordinates are degrees, so
`geometry.area` would be in "square degrees", which is not a real unit, and the
ground size of one degree changes with latitude. To get metres we transform the
data into a projected CRS with metre units and low distortion where the data is.

Rules:
- Geographic CRS (lon/lat)  -> the UTM zone covering the data's centre.
- Web Mercator (EPSG:3857)  -> also UTM: it is projected but stretches distances
                               badly away from the equator (1.4x at 45 degrees).
- Any other projected CRS   -> used as-is; its unit (metre, foot, ...) is
                               converted to metres.

UTM is only accurate near its zone. Data wider than MAX_LONGITUDE_SPAN_DEGREES
is rejected rather than measured with large, silent errors.
"""

import geopandas as gpd
from pyproj import CRS

from app.core.exceptions import ExtentTooLargeError, InvalidCoordinatesError, UnsupportedCRSError

WEB_MERCATOR_EPSG = 3857

# At 6 degrees from a UTM zone's central meridian the scale error is ~0.5% for
# length (~1% for area). A 12 degree wide dataset centred in its zone stays within that.
MAX_LONGITUDE_SPAN_DEGREES = 12.0


def crs_label(crs: CRS) -> str:
    """Short human-readable name, e.g. 'EPSG:4326'."""
    authority = crs.to_authority()
    if authority:
        return f"{authority[0]}:{authority[1]}"
    return crs.name


def choose_measurement_crs(gdf: gpd.GeoDataFrame) -> CRS:
    source = gdf.crs
    if source is None:
        raise UnsupportedCRSError("Data has no coordinate reference system.")

    if source.is_geographic:
        check_lon_lat_range(gdf)
        return utm_crs_for(gdf, lon_lat_bounds=gdf.total_bounds)

    if source.to_epsg() == WEB_MERCATOR_EPSG:
        return utm_crs_for(gdf, lon_lat_bounds=gdf.to_crs("EPSG:4326").total_bounds)

    if source.is_projected:
        return source

    raise UnsupportedCRSError(
        f"Coordinate system {crs_label(source)} is neither geographic nor projected, "
        "so it cannot be used for measurement."
    )


def check_lon_lat_range(gdf: gpd.GeoDataFrame) -> None:
    """Catch the common mistake of projected (metre) coordinates labelled as lon/lat."""
    min_x, min_y, max_x, max_y = gdf.total_bounds
    if min_x < -180 or max_x > 180 or min_y < -90 or max_y > 90:
        raise InvalidCoordinatesError(
            f"Coordinates are outside the valid longitude/latitude range for {crs_label(gdf.crs)} "
            "(longitude -180..180, latitude -90..90). The file may contain projected coordinates "
            "without declaring their coordinate system."
        )


def utm_crs_for(gdf: gpd.GeoDataFrame, lon_lat_bounds) -> CRS:
    min_lon, _, max_lon, _ = lon_lat_bounds
    span = max_lon - min_lon
    if span > MAX_LONGITUDE_SPAN_DEGREES:
        raise ExtentTooLargeError(
            f"Data spans {span:.1f} degrees of longitude. Measurement uses a single UTM zone, "
            f"which is only accurate for data up to {MAX_LONGITUDE_SPAN_DEGREES:g} degrees wide."
        )
    try:
        return gdf.estimate_utm_crs()
    except RuntimeError:
        # e.g. data near the poles, where UTM is not defined.
        raise UnsupportedCRSError(
            "Could not find a UTM zone for this data (UTM covers 80°S to 84°N)."
        ) from None


def metres_per_unit(crs: CRS) -> float:
    """Conversion factor from the CRS's linear unit to metres (1.0 for metre, 0.3048 for foot)."""
    return crs.axis_info[0].unit_conversion_factor
