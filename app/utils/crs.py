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
"""

import geopandas as gpd
from pyproj import CRS

from app.core.exceptions import UnsupportedCRSError

WEB_MERCATOR_EPSG = 3857


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

    if source.is_geographic or source.to_epsg() == WEB_MERCATOR_EPSG:
        try:
            return gdf.estimate_utm_crs()
        except RuntimeError:
            # e.g. data near the poles, where UTM is not defined.
            raise UnsupportedCRSError(
                "Could not find a UTM zone for this data (UTM covers 80°S to 84°N)."
            ) from None

    if source.is_projected:
        return source

    raise UnsupportedCRSError(
        f"Coordinate system {crs_label(source)} is neither geographic nor projected, "
        "so it cannot be used for measurement."
    )


def metres_per_unit(crs: CRS) -> float:
    """Conversion factor from the CRS's linear unit to metres (1.0 for metre, 0.3048 for foot)."""
    return crs.axis_info[0].unit_conversion_factor
