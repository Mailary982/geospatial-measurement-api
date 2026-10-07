import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point, Polygon

from app.core.exceptions import EmptyDatasetError, FileParseError, MissingCRSError
from app.services.geo_reader import (
    MAX_REPORTED_INVALID_FEATURES,
    ensure_crs,
    invalid_reason,
    read_geodataframe,
    split_valid_invalid,
)
from tests.helpers import geojson_bytes, line, make_zip, point, polygon, shapefile_zip_bytes

SQUARE = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
BOWTIE = polygon((0, 0), (1, 1), (1, 0), (0, 1), (0, 0))


def write(tmp_path, name: str, data: bytes):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def sample_gdf(crs="EPSG:4326") -> gpd.GeoDataFrame:
    # A shapefile holds a single geometry type, so both features are polygons.
    return gpd.GeoDataFrame(
        {"name": ["a", "b"]}, geometry=[SQUARE, SQUARE.buffer(-0.25)], crs=crs
    )


# --- reading GeoJSON ---

def test_reads_geojson_features(tmp_path):
    path = write(tmp_path, "a.geojson", geojson_bytes(point(77.5, 12.9), line((0, 0), (1, 1))))

    gdf = read_geodataframe(path, "geojson")

    assert len(gdf) == 2
    assert list(gdf.geom_type) == ["Point", "LineString"]


def test_geojson_without_crs_member_is_wgs84(tmp_path):
    path = write(tmp_path, "a.geojson", geojson_bytes(point(77.5, 12.9)))

    gdf = ensure_crs(read_geodataframe(path, "geojson"), "geojson")

    assert gdf.crs.to_epsg() == 4326


def test_geojson_legacy_crs_member_is_honoured(tmp_path):
    path = write(tmp_path, "a.geojson", geojson_bytes(point(500000, 1400000), crs="EPSG:32643"))

    gdf = read_geodataframe(path, "geojson")

    assert gdf.crs.to_epsg() == 32643


def test_corrupt_geojson_raises_clean_parse_error(tmp_path):
    path = write(tmp_path, "a.geojson", b'{"type": "FeatureCollection", "features": [{"broken": ')

    with pytest.raises(FileParseError) as error:
        read_geodataframe(path, "geojson")

    assert str(tmp_path) not in error.value.message


def test_geojson_with_no_features_raises_empty_dataset(tmp_path):
    path = write(tmp_path, "a.geojson", b'{"type": "FeatureCollection", "features": []}')

    with pytest.raises(EmptyDatasetError):
        read_geodataframe(path, "geojson")


# --- reading Shapefile ZIPs ---

def test_reads_zipped_shapefile(tmp_path):
    path = write(tmp_path, "a.zip", shapefile_zip_bytes(sample_gdf(), tmp_path))

    gdf = read_geodataframe(path, "shapefile")

    assert len(gdf) == 2
    assert gdf.crs.to_epsg() == 4326


def test_reads_shapefile_inside_zip_subfolder(tmp_path):
    data = shapefile_zip_bytes(sample_gdf("EPSG:32643"), tmp_path, folder="export/")
    path = write(tmp_path, "a.zip", data)

    gdf = read_geodataframe(path, "shapefile")

    assert gdf.crs.to_epsg() == 32643


def test_shapefile_without_readable_prj_raises_missing_crs(tmp_path):
    data = shapefile_zip_bytes(sample_gdf(), tmp_path, drop=(".prj",))
    path = write(tmp_path, "a.zip", data)

    gdf = read_geodataframe(path, "shapefile")

    with pytest.raises(MissingCRSError):
        ensure_crs(gdf, "shapefile")


def test_corrupt_shapefile_raises_clean_parse_error(tmp_path):
    members = {f"data{ext}": b"garbage" for ext in (".shp", ".shx", ".dbf", ".prj")}
    path = write(tmp_path, "a.zip", make_zip(members))

    with pytest.raises(FileParseError) as error:
        read_geodataframe(path, "shapefile")

    assert "Shapefile" in error.value.message
    assert str(tmp_path) not in error.value.message


# --- geometry validation ---

@pytest.mark.parametrize(
    "geometry, expected",
    [
        (None, "empty geometry"),
        (Polygon(), "empty geometry"),
        (SQUARE, None),
        (Point(1, 2), None),
        (LineString([(0, 0), (1, 1)]), None),
    ],
)
def test_invalid_reason(geometry, expected):
    assert invalid_reason(geometry) == expected


def test_self_intersecting_polygon_is_reported_with_reason(tmp_path):
    path = write(tmp_path, "a.geojson", geojson_bytes(BOWTIE))

    split = split_valid_invalid(read_geodataframe(path, "geojson"))

    assert split.invalid_count == 1
    assert split.invalid_features[0].reason.startswith("Self-intersection")


def test_split_keeps_valid_and_reports_invalid_by_position(tmp_path):
    geometries = [
        polygon((0, 0), (1, 0), (1, 1), (0, 1), (0, 0)),          # 0 valid
        BOWTIE,                                                    # 1 invalid
        None,                                                      # 2 empty
        {"type": "GeometryCollection", "geometries": [point(1, 2)]},  # 3 unsupported
        point(5, 5),                                               # 4 valid
    ]
    path = write(tmp_path, "a.geojson", geojson_bytes(*geometries))

    split = split_valid_invalid(read_geodataframe(path, "geojson"))

    assert split.total_features == 5
    assert len(split.valid) == 2
    assert split.invalid_count == 3
    assert [f.feature_index for f in split.invalid_features] == [1, 2, 3]
    assert split.invalid_features[1].reason == "empty geometry"
    assert split.invalid_features[2].reason == "unsupported geometry type: GeometryCollection"


def test_invalid_feature_list_is_capped_but_count_is_exact():
    count = MAX_REPORTED_INVALID_FEATURES + 10
    gdf = gpd.GeoDataFrame(geometry=[None] * count, crs="EPSG:4326")

    split = split_valid_invalid(gdf)

    assert split.invalid_count == count
    assert len(split.invalid_features) == MAX_REPORTED_INVALID_FEATURES
    assert split.valid.empty
