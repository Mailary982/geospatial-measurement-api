import io
import json
import zipfile
from pathlib import Path

import geopandas as gpd


def make_zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def feature_collection(*geometries: dict | None, crs: str | None = None) -> dict:
    collection = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"id": i}, "geometry": geometry}
            for i, geometry in enumerate(geometries)
        ],
    }
    if crs:
        # Legacy (2008) GeoJSON "crs" member; GDAL still honours it.
        collection["crs"] = {"type": "name", "properties": {"name": crs}}
    return collection


def geojson_bytes(*geometries: dict | None, crs: str | None = None) -> bytes:
    return json.dumps(feature_collection(*geometries, crs=crs)).encode()


def shapefile_zip_bytes(
    gdf: gpd.GeoDataFrame, work_dir: Path, folder: str = "", drop: tuple[str, ...] = ()
) -> bytes:
    """Write `gdf` as a shapefile and return it zipped (optionally inside `folder`, minus `drop` extensions)."""
    shp_dir = work_dir / "shapefile"
    shp_dir.mkdir(parents=True, exist_ok=True)
    gdf.to_file(shp_dir / "data.shp")
    members = {
        f"{folder}{path.name}": path.read_bytes()
        for path in sorted(shp_dir.iterdir())
        if path.suffix not in drop
    }
    return make_zip(members)


def polygon(*ring: tuple[float, float]) -> dict:
    return {"type": "Polygon", "coordinates": [[list(point) for point in ring]]}


def line(*points: tuple[float, float]) -> dict:
    return {"type": "LineString", "coordinates": [list(point) for point in points]}


def point(x: float, y: float) -> dict:
    return {"type": "Point", "coordinates": [x, y]}
