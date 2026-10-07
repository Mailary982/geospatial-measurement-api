"""Full upload -> status -> result flow using the small files in tests/fixtures/.

Expected values come from pyproj.Geod (exact ellipsoidal maths), so the test
checks the API's UTM-based numbers against an independent ground truth.
"""

from pathlib import Path

import geopandas as gpd
import pytest
from pyproj import Geod

JOBS_URL = "/api/v1/geospatial/jobs"
FIXTURES = Path(__file__).parent / "fixtures"
GEOD = Geod(ellps="WGS84")
TOLERANCE = 0.005


def run_flow(client, fixture_name: str) -> dict:
    data = (FIXTURES / fixture_name).read_bytes()
    created = client.post(JOBS_URL, files={"file": (fixture_name, data, "application/geo+json")})
    assert created.status_code == 201

    status = client.get(created.json()["status_url"])
    assert status.status_code == 200
    assert status.json()["status"] == "COMPLETED"

    result = client.get(created.json()["result_url"])
    assert result.status_code == 200
    return result.json()


def geodesic_totals(fixture_name: str, geometry_types: list[str]) -> tuple[float, float]:
    """(area, length-or-perimeter) of matching geometries, computed on the ellipsoid."""
    gdf = gpd.read_file(FIXTURES / fixture_name)
    selected = gdf[gdf.geom_type.isin(geometry_types) & gdf.is_valid]
    area = sum(abs(GEOD.geometry_area_perimeter(g)[0]) for g in selected.geometry)
    length = sum(GEOD.geometry_length(g) for g in selected.geometry)
    return area, length


def test_parcels_fixture(client):
    body = run_flow(client, "parcels.geojson")

    area, perimeter = geodesic_totals("parcels.geojson", ["Polygon"])
    assert body["geometry"]["total_features"] == 2
    assert body["geometry"]["types"] == ["Polygon"]
    assert body["measurements"]["area"] == pytest.approx(area, rel=TOLERANCE)
    assert body["measurements"]["perimeter"] == pytest.approx(perimeter, rel=TOLERANCE)
    assert body["measurements"]["length"] is None


def test_roads_fixture(client):
    body = run_flow(client, "roads.geojson")

    _, length = geodesic_totals("roads.geojson", ["LineString", "MultiLineString"])
    assert body["geometry"]["types"] == ["LineString", "MultiLineString"]
    assert body["measurements"]["length"] == pytest.approx(length, rel=TOLERANCE)
    assert body["measurements"]["area"] is None


def test_landmarks_fixture(client):
    body = run_flow(client, "landmarks.geojson")

    assert body["geometry"]["types"] == ["Point"]
    assert body["bounding_box"] == {
        "crs": "EPSG:4326", "min_x": 77.5946, "min_y": 12.9592, "max_x": 77.6101, "max_y": 12.9716,
    }
    assert all(value is None for value in body["measurements"].values())


def test_mixed_fixture_with_invalid_features(client):
    body = run_flow(client, "mixed_with_invalid.geojson")

    geometry = body["geometry"]
    assert geometry["total_features"] == 5
    assert geometry["valid_count"] == 3
    assert geometry["invalid_count"] == 2
    assert geometry["types"] == ["LineString", "Point", "Polygon"]
    assert geometry["invalid_features"][0]["feature_index"] == 3
    assert geometry["invalid_features"][0]["reason"].startswith("Self-intersection")
    assert geometry["invalid_features"][1] == {"feature_index": 4, "reason": "empty geometry"}

    area, _ = geodesic_totals("mixed_with_invalid.geojson", ["Polygon"])
    _, length = geodesic_totals("mixed_with_invalid.geojson", ["LineString"])
    assert body["measurements"]["area"] == pytest.approx(area, rel=TOLERANCE)
    assert body["measurements"]["length"] == pytest.approx(length, rel=TOLERANCE)
