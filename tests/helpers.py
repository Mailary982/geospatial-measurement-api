import io
import json
import zipfile


def make_zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def feature_collection(*geometries: dict, crs: str | None = None) -> dict:
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


def geojson_bytes(*geometries: dict, crs: str | None = None) -> bytes:
    return json.dumps(feature_collection(*geometries, crs=crs)).encode()
