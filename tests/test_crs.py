import geopandas as gpd
import pytest
from pyproj import CRS
from shapely.geometry import Point

from app.core.exceptions import ExtentTooLargeError, InvalidCoordinatesError, UnsupportedCRSError
from app.utils.crs import (
    MAX_LONGITUDE_SPAN_DEGREES,
    choose_measurement_crs,
    crs_label,
    metres_per_unit,
)


def points_gdf(x: float, y: float, crs: str) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(geometry=[Point(x, y)], crs=crs)


@pytest.mark.parametrize(
    "lon, lat, expected_epsg",
    [
        (77.59, 12.97, 32643),   # Bengaluru -> UTM 43N
        (-0.12, 51.50, 32630),   # London    -> UTM 30N
        (151.21, -33.87, 32756), # Sydney    -> UTM 56S
    ],
)
def test_geographic_data_uses_local_utm_zone(lon, lat, expected_epsg):
    crs = choose_measurement_crs(points_gdf(lon, lat, "EPSG:4326"))
    assert crs.to_epsg() == expected_epsg


def test_projected_metre_crs_is_used_as_is():
    crs = choose_measurement_crs(points_gdf(500000, 1400000, "EPSG:32643"))
    assert crs.to_epsg() == 32643


def test_web_mercator_is_reprojected_to_utm():
    gdf = points_gdf(77.59, 12.97, "EPSG:4326").to_crs("EPSG:3857")
    assert choose_measurement_crs(gdf).to_epsg() == 32643


def test_polar_data_raises_unsupported_crs():
    with pytest.raises(UnsupportedCRSError, match="UTM"):
        choose_measurement_crs(points_gdf(10.0, 88.0, "EPSG:4326"))


def test_geocentric_crs_is_unsupported():
    gdf = gpd.GeoDataFrame(geometry=[Point(1, 2)], crs="EPSG:4978")
    with pytest.raises(UnsupportedCRSError):
        choose_measurement_crs(gdf)


@pytest.mark.parametrize(
    "epsg, factor",
    [(32643, 1.0), (2263, 0.3048006096)],  # metre; US survey foot
)
def test_metres_per_unit(epsg, factor):
    assert metres_per_unit(CRS.from_epsg(epsg)) == pytest.approx(factor)


def test_crs_label():
    assert crs_label(CRS.from_epsg(4326)) == "EPSG:4326"


# --- guards against silently wrong measurements ---

def test_projected_coordinates_labelled_as_lon_lat_are_rejected():
    # UTM metre values in a file that declares (or defaults to) EPSG:4326.
    with pytest.raises(InvalidCoordinatesError, match="projected coordinates"):
        choose_measurement_crs(points_gdf(500000, 1400000, "EPSG:4326"))


def test_latitude_beyond_90_is_rejected():
    with pytest.raises(InvalidCoordinatesError):
        choose_measurement_crs(points_gdf(10, 95, "EPSG:4326"))


def wide_gdf(min_lon: float, max_lon: float, crs="EPSG:4326") -> gpd.GeoDataFrame:
    gdf = gpd.GeoDataFrame(geometry=[Point(min_lon, 10), Point(max_lon, 10)], crs="EPSG:4326")
    return gdf.to_crs(crs)


def test_data_wider_than_utm_limit_is_rejected():
    with pytest.raises(ExtentTooLargeError, match="degrees of longitude"):
        choose_measurement_crs(wide_gdf(70, 70 + MAX_LONGITUDE_SPAN_DEGREES + 1))


def test_data_crossing_antimeridian_is_rejected():
    # As planar GeoJSON, 170 -> -170 spans 340 degrees the "long way round".
    with pytest.raises(ExtentTooLargeError):
        choose_measurement_crs(wide_gdf(-170, 170))


def test_web_mercator_wide_data_is_rejected():
    with pytest.raises(ExtentTooLargeError):
        choose_measurement_crs(wide_gdf(0, 40, crs="EPSG:3857"))


def test_data_within_utm_limit_is_accepted():
    crs = choose_measurement_crs(wide_gdf(72, 72 + MAX_LONGITUDE_SPAN_DEGREES))
    assert crs.to_epsg() == 32643
