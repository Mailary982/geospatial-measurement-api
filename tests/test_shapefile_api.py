"""Shapefile ZIP uploads through the API, focusing on CRS handling."""

import geopandas as gpd
import pytest
from shapely.geometry import LineString, box

from tests.helpers import make_zip, shapefile_zip_bytes

JOBS_URL = "/api/v1/geospatial/jobs"


def upload_zip(client, data: bytes) -> dict:
    return client.post(JOBS_URL, files={"file": ("data.zip", data, "application/zip")})


def result_for(client, data: bytes) -> dict:
    response = upload_zip(client, data)
    assert response.status_code == 201
    return client.get(response.json()["result_url"]).json()


def test_shapefile_in_lon_lat_is_projected_to_utm(client, tmp_path):
    gdf = gpd.GeoDataFrame(geometry=[box(77.50, 12.90, 77.51, 12.91)], crs="EPSG:4326")

    body = result_for(client, shapefile_zip_bytes(gdf, tmp_path))

    assert body["crs"] == {"source": "EPSG:4326", "measurement": "EPSG:32643"}
    assert body["measurements"]["area"] == pytest.approx(1_200_618, rel=0.005)


def test_shapefile_in_feet_is_converted_to_metres(client, tmp_path):
    # 100 ft x 100 ft square and a 100 ft line in NY State Plane (US survey feet).
    gdf = gpd.GeoDataFrame(geometry=[box(1_000_000, 200_000, 1_000_100, 200_100)], crs="EPSG:2263")

    body = result_for(client, shapefile_zip_bytes(gdf, tmp_path))

    assert body["crs"] == {"source": "EPSG:2263", "measurement": "EPSG:2263"}
    assert body["measurements"]["area"] == pytest.approx(929.03, rel=1e-4)
    assert body["measurements"]["area_unit"] == "square_meters"


def test_shapefile_with_lines(client, tmp_path):
    gdf = gpd.GeoDataFrame(geometry=[LineString([(500000, 1400000), (500300, 1400400)])], crs="EPSG:32643")

    body = result_for(client, shapefile_zip_bytes(gdf, tmp_path))

    assert body["measurements"]["length"] == pytest.approx(500)
    assert body["measurements"]["area"] is None


def test_shapefile_in_zip_subfolder(client, tmp_path):
    gdf = gpd.GeoDataFrame(geometry=[box(0, 0, 10, 10)], crs="EPSG:32643")

    body = result_for(client, shapefile_zip_bytes(gdf, tmp_path, folder="exports/2024/"))

    assert body["measurements"]["area"] == pytest.approx(100)


def test_shapefile_without_prj_is_rejected_at_upload(client, tmp_path):
    gdf = gpd.GeoDataFrame(geometry=[box(0, 0, 10, 10)], crs="EPSG:32643")

    response = upload_zip(client, shapefile_zip_bytes(gdf, tmp_path, drop=(".prj",)))

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "INVALID_FILE"
    assert error["details"] == {"missing": [".prj"]}


def test_shapefile_with_unreadable_prj_fails_job(client, tmp_path):
    gdf = gpd.GeoDataFrame(geometry=[box(0, 0, 10, 10)], crs="EPSG:32643")
    shp_dir = tmp_path / "shp"
    shp_dir.mkdir()
    gdf.to_file(shp_dir / "data.shp")
    (shp_dir / "data.prj").write_text("this is not a coordinate system")
    data = make_zip({path.name: path.read_bytes() for path in shp_dir.iterdir()})

    response = upload_zip(client, data)
    status = client.get(response.json()["status_url"]).json()

    assert status["status"] == "FAILED"
    assert "coordinate system" in status["error_message"]


def test_corrupt_shapefile_fails_job_without_leaking_paths(client, settings):
    members = {f"data{ext}": b"garbage" for ext in (".shp", ".shx", ".dbf", ".prj")}

    response = upload_zip(client, make_zip(members))
    status = client.get(response.json()["status_url"]).json()

    assert status["status"] == "FAILED"
    assert status["error_message"] == "Could not read the file as a Shapefile. It may be corrupted."
    assert str(settings.upload_dir) not in status["error_message"]
