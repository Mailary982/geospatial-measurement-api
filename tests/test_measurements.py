"""Measurement accuracy tests.

Ground truth for lon/lat data comes from pyproj.Geod, which computes exact
distances/areas on the WGS84 ellipsoid. UTM results must agree within 0.5%.
"""

import geopandas as gpd
import pytest
from pyproj import Geod
from shapely.geometry import LineString, MultiPoint, MultiPolygon, Point, Polygon, box

from app.services.measurement import AREA_UNIT, LENGTH_UNIT, measure

GEOD = Geod(ellps="WGS84")
TOLERANCE = 0.005  # 0.5 %

# ~1.1 km x 1.1 km square in Bengaluru (lon/lat).
BLR_SQUARE = box(77.50, 12.90, 77.51, 12.91)
# 1 degree of longitude along the equator (~111 km).
EQUATOR_LINE = LineString([(0.0, 0.0), (1.0, 0.0)])


def gdf_of(*geometries, crs="EPSG:4326") -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(geometry=list(geometries), crs=crs)


def geodesic_area(polygon: Polygon) -> float:
    area, _ = GEOD.geometry_area_perimeter(polygon)
    return abs(area)


def test_polygon_area_and_perimeter_in_lon_lat_match_geodesic():
    result = measure(gdf_of(BLR_SQUARE))

    expected_area, expected_perimeter = GEOD.geometry_area_perimeter(BLR_SQUARE)
    assert result.total_area == pytest.approx(abs(expected_area), rel=TOLERANCE)
    assert result.total_perimeter == pytest.approx(expected_perimeter, rel=TOLERANCE)
    assert result.area_unit == AREA_UNIT
    assert result.perimeter_unit == LENGTH_UNIT
    assert result.total_length is None
    assert result.length_unit is None
    assert result.source_crs == "EPSG:4326"
    assert result.measurement_crs == "EPSG:32643"


def test_area_is_not_computed_in_square_degrees():
    # The naive answer would be 0.0001 "square degrees".
    assert measure(gdf_of(BLR_SQUARE)).total_area > 1_000_000


def test_line_length_in_lon_lat_matches_geodesic():
    result = measure(gdf_of(EQUATOR_LINE))

    assert result.total_length == pytest.approx(GEOD.geometry_length(EQUATOR_LINE), rel=TOLERANCE)
    assert result.length_unit == LENGTH_UNIT
    assert result.total_area is None
    assert result.area_unit is None
    assert result.total_perimeter is None


def test_points_have_no_area_or_length():
    result = measure(gdf_of(Point(77.5, 12.9), MultiPoint([(77.6, 13.0), (77.7, 13.1)])))

    assert result.total_area is None
    assert result.total_perimeter is None
    assert result.total_length is None
    assert result.geometry_types == ["MultiPoint", "Point"]


def test_projected_metre_crs_measures_exactly():
    # 100 m x 100 m square, 250 m line, in UTM 43N.
    square = box(500000, 1400000, 500100, 1400100)
    road = LineString([(500000, 1400000), (500150, 1400200)])

    result = measure(gdf_of(square, road, crs="EPSG:32643"))

    assert result.total_area == pytest.approx(10_000)
    assert result.total_perimeter == pytest.approx(400)
    assert result.total_length == pytest.approx(250)
    assert result.measurement_crs == "EPSG:32643"


def test_projected_foot_crs_is_converted_to_metres():
    # 100 ft x 100 ft square in New York State Plane (US survey feet).
    square = box(1_000_000, 200_000, 1_000_100, 200_100)

    result = measure(gdf_of(square, crs="EPSG:2263"))

    assert result.total_area == pytest.approx(929.03, rel=1e-4)
    assert result.total_perimeter == pytest.approx(121.92, rel=1e-4)


def test_web_mercator_input_is_measured_accurately():
    # In raw EPSG:3857 units this square would be ~5% too big at 13°N; via UTM it is correct.
    result = measure(gdf_of(BLR_SQUARE).to_crs("EPSG:3857"))

    assert result.measurement_crs == "EPSG:32643"
    assert result.total_area == pytest.approx(geodesic_area(BLR_SQUARE), rel=TOLERANCE)


def test_multipolygon_area_is_sum_of_parts():
    other = box(77.52, 12.90, 77.53, 12.91)

    result = measure(gdf_of(MultiPolygon([BLR_SQUARE, other])))

    expected = geodesic_area(BLR_SQUARE) + geodesic_area(other)
    assert result.total_area == pytest.approx(expected, rel=TOLERANCE)


def test_mixed_geometries_aggregate_by_kind():
    line = LineString([(77.50, 12.95), (77.51, 12.95)])

    result = measure(gdf_of(BLR_SQUARE, line, Point(77.55, 12.95)))

    assert result.geometry_types == ["LineString", "Point", "Polygon"]
    assert result.total_area == pytest.approx(geodesic_area(BLR_SQUARE), rel=TOLERANCE)
    # Line length only, polygon perimeter is NOT added to it.
    assert result.total_length == pytest.approx(GEOD.geometry_length(line), rel=TOLERANCE)


def test_bounding_box_is_in_source_crs():
    result = measure(gdf_of(BLR_SQUARE, Point(77.60, 13.00)))

    box_ = result.bounding_box
    assert (box_.min_x, box_.min_y, box_.max_x, box_.max_y) == pytest.approx((77.50, 12.90, 77.60, 13.00))


def test_empty_input_is_rejected():
    with pytest.raises(ValueError):
        measure(gdf_of())
