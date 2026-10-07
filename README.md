# Geospatial File Measurement API

A FastAPI service that accepts a **GeoJSON** file or a **zipped Shapefile** and returns:
- geometry count and types
- coordinate reference system (CRS)
- bounding box
- total **area**, **perimeter** and **length**, in metres

Measurements use a correct metric CRS, not raw latitude/longitude. Jobs and results are stored in PostgreSQL.

```
POST /api/v1/geospatial/jobs              upload a file      -> 201 {job_id, status: QUEUED}
GET  /api/v1/geospatial/jobs/{id}         processing status  -> QUEUED | PROCESSING | COMPLETED | FAILED
GET  /api/v1/geospatial/jobs/{id}/result  measurements       -> 200 (or 409 if not completed)
GET  /health                              service + DB health
```

Interactive docs (Swagger UI) are at **http://localhost:8000/docs** once the API is running.

---

## Architecture

```
Client ──> FastAPI routes (thin)
              │
              ├─ file_validation  quick checks during the request (type, size, content, ZIP contents)
              ├─ storage          StorageBackend interface; LocalStorage saves to disk
              ├─ job_service      creates jobs; run_job() runs as a background task
              │     ├─ geo_reader   file -> GeoDataFrame, CRS check, valid/invalid split
              │     ├─ measurement  area / perimeter / length / bbox (pure functions)
              │     └─ utils/crs    picks the measurement CRS
              │
              ├─ PostgreSQL       geospatial_jobs, measurement_results
              └─ Local disk       data/uploads/<job_id>/upload.<ext>
```

**Request flow**
1. `POST /jobs` validates the upload synchronously, so bad files get an immediate 4xx.
2. The file is saved to disk and a `QUEUED` job row is created.
3. The API returns `201`.
4. A FastAPI `BackgroundTask` then sets the job to `PROCESSING`, parses and measures the file, and stores the result. The job ends as `COMPLETED` or `FAILED`.
5. The client polls the status URL, then fetches the result.

The geospatial modules (`geo_reader`, `measurement`, `utils/crs`) import neither FastAPI nor SQLAlchemy, so they are unit-tested directly.

The full design is in [docs/HLD.md](docs/HLD.md).

### Technology choices
| Choice | Why |
|---|---|
| FastAPI + Pydantic v2 | Typed request/response models and automatic OpenAPI docs. |
| SQLAlchemy 2 (sync) + Alembic | Simple, explicit ORM with versioned migrations. Async adds nothing here because the heavy work is CPU-bound GDAL. |
| GeoPandas / Shapely / pyproj / pyogrio | The standard Python GIS stack. The wheels bundle GDAL, GEOS and PROJ, so no system install is needed. |
| `BackgroundTasks` instead of Celery/Redis | Uploads are capped at 20 MB and process in seconds. A queue would add two services the assignment doesn't need (see *Known limitations*). |
| Plain PostgreSQL (no PostGIS) | We store numbers and metadata only. No spatial SQL queries are needed. |

---

## Why CRS transformation is needed

GeoJSON coordinates are longitude/latitude in **degrees** (EPSG:4326). Calling `polygon.area` on them returns *square degrees*, which is not a real unit. The ground size of a degree also changes with latitude: one degree of longitude is ~111 km at the equator and 0 km at the poles. A 1 km² parcel in Bengaluru has a "raw" area of about `0.0001`.

So before measuring, the service transforms the data to a **projected CRS in metres**:

| Source CRS | Measurement CRS |
|---|---|
| Geographic (lon/lat, e.g. EPSG:4326, NAD83) | The **UTM zone** covering the data's centre (e.g. Bengaluru → EPSG:32643) |
| Web Mercator (EPSG:3857) | Also UTM. 3857 is projected but stretches distances ~1.4× at 45° latitude. |
| Any other projected CRS (e.g. UTM, State Plane) | Used as-is. Its unit (metre, US foot, …) is converted to metres. |

The tests compare the results against `pyproj.Geod`, which gives exact ellipsoidal values, and they agree within 0.5% (typically ~0.1%).

**Measurement rules**
- `area` and `perimeter` come from Polygon/MultiPolygon features; `length` comes from LineString/MultiLineString features.
- Points are counted but have no area or length.
- A measurement that doesn't apply is `null`, never a misleading `0`.
- Polygon perimeter is never added into `length`.
- The bounding box is reported in the **source** CRS, and the response labels which CRS that is.
- GeoJSON without a `crs` member is treated as EPSG:4326, as RFC 7946 requires.

---

## Supported file formats

| Format | Upload as | Notes |
|---|---|---|
| GeoJSON | `.geojson` or `.json` | FeatureCollection, a single Feature, or a bare geometry. |
| Shapefile | **one `.zip`** containing `.shp`, `.shx`, `.dbf`, **`.prj`** | A bare `.shp` is rejected because it is unusable without its companion files. The `.prj` is required so units are known. A subfolder inside the ZIP is fine. |

**Invalid geometries** are **skipped and reported**, not repaired. This covers self-intersections, empty/null geometries and GeometryCollections. The result shows `invalid_count` and `invalid_features[{feature_index, reason}]`, with the first 50 listed. If *no* geometry is usable, the job is `FAILED`.

---

## Running with Docker (recommended)

```bash
docker compose up --build
```

This starts PostgreSQL 16 and the API on http://localhost:8000. On startup the API container runs `alembic upgrade head`, then `uvicorn`. Uploaded files go to the `uploads` volume.

To stop the stack:

```bash
docker compose down
```

Add `-v` to also delete the database and upload volumes.

## Running without Docker

Requires Python 3.12+ and a reachable PostgreSQL. You can start just the database with `docker compose up -d db`.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env               # adjust DATABASE_URL if needed
alembic upgrade head               # create tables
uvicorn app.main:app --reload
```

### Environment variables
| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://geo:geo@localhost:5432/geo` | SQLAlchemy URL (psycopg 3 driver) |
| `UPLOAD_DIR` | `./data/uploads` | Where uploaded files are stored |
| `MAX_UPLOAD_SIZE_MB` | `20` | Upload size limit (→ 413) |
| `MAX_UNCOMPRESSED_ZIP_MB` | `100` | Zip-bomb guard for Shapefile ZIPs |
| `LOG_LEVEL` | `INFO` | Python logging level |

### Database migrations
```bash
alembic upgrade head                                  # apply
alembic downgrade base                                # roll back everything
alembic revision --autogenerate -m "describe change"  # after editing app/db/models.py
```

---

## Using the API

Sample files are in [tests/fixtures/](tests/fixtures/).

**1. Upload**
```bash
curl -F "file=@tests/fixtures/mixed_with_invalid.geojson" http://localhost:8000/api/v1/geospatial/jobs
```
```json
{
  "job_id": "0df3d0fc-79d8-4a19-abe5-5eb804e1934b",
  "status": "QUEUED",
  "status_url": "/api/v1/geospatial/jobs/0df3d0fc-79d8-4a19-abe5-5eb804e1934b",
  "result_url": "/api/v1/geospatial/jobs/0df3d0fc-79d8-4a19-abe5-5eb804e1934b/result"
}
```

**2. Check status**
```bash
curl http://localhost:8000/api/v1/geospatial/jobs/0df3d0fc-79d8-4a19-abe5-5eb804e1934b
```
```json
{
  "job_id": "0df3d0fc-79d8-4a19-abe5-5eb804e1934b",
  "status": "COMPLETED",
  "file": { "filename": "mixed_with_invalid.geojson", "format": "geojson", "size_bytes": 1920 },
  "error_message": null,
  "created_at": "2026-10-07T13:52:15.769573Z",
  "updated_at": "2026-10-07T13:52:16.869117Z"
}
```

**3. Get measurements**
```bash
curl http://localhost:8000/api/v1/geospatial/jobs/0df3d0fc-79d8-4a19-abe5-5eb804e1934b/result
```
```json
{
  "job_id": "0df3d0fc-79d8-4a19-abe5-5eb804e1934b",
  "status": "COMPLETED",
  "file": { "filename": "mixed_with_invalid.geojson", "format": "geojson", "size_bytes": 1920 },
  "crs": { "source": "EPSG:4326", "measurement": "EPSG:32643" },
  "geometry": {
    "total_features": 5,
    "valid_count": 3,
    "invalid_count": 2,
    "types": ["LineString", "Point", "Polygon"],
    "invalid_features": [
      { "feature_index": 3, "reason": "Self-intersection[77.505 12.925]" },
      { "feature_index": 4, "reason": "empty geometry" }
    ]
  },
  "bounding_box": { "crs": "EPSG:4326", "min_x": 77.5, "min_y": 12.9, "max_x": 77.5946, "max_y": 12.9716 },
  "measurements": {
    "area": 1201853.256, "area_unit": "square_meters",
    "perimeter": 4385.365, "perimeter_unit": "meters",
    "length": 1085.622, "length_unit": "meters"
  }
}
```

A zipped Shapefile is uploaded the same way:

```bash
curl -F "file=@parcels.zip" http://localhost:8000/api/v1/geospatial/jobs
```

### Errors
Every error response has the same shape:
```json
{ "error": { "code": "UNSUPPORTED_FILE_TYPE", "message": "Unsupported file type '.txt'. Supported types: .geojson, .json, .zip.", "details": null } }
```

| Status | Code(s) | When |
|---|---|---|
| 400 | `INVALID_FILENAME`, `EMPTY_FILE`, `INVALID_FILE` | Bad name; empty file; not JSON/GeoJSON; corrupt ZIP; Shapefile part missing |
| 413 | `FILE_TOO_LARGE` | Over `MAX_UPLOAD_SIZE_MB` |
| 415 | `UNSUPPORTED_FILE_TYPE` | Not `.geojson` / `.json` / `.zip` (including a bare `.shp`) |
| 422 | `VALIDATION_ERROR` | Missing `file` field; job ID not a UUID |
| 404 | `JOB_NOT_FOUND` | Unknown job ID |
| 409 | `JOB_NOT_COMPLETED`, `JOB_FAILED` | Result requested before completion, or for a failed job |
| 500 | `INTERNAL_ERROR` | Unexpected bug. Details are logged, never returned. |

Problems found during background processing set the job to `FAILED` with a readable `error_message`. Examples: unparseable file, unreadable `.prj`, no valid geometries, coordinates outside ±180/±90, data too wide for UTM.

---

## Running tests

```bash
pip install -r requirements-dev.txt
pytest
```

The 145 tests run on an in-memory SQLite database and need no Docker, network or PostgreSQL. `TestClient` runs background tasks before returning, so job tests are deterministic. Small fixtures are in `tests/fixtures/`; Shapefile fixtures are generated in temporary folders.

| File | Covers |
|---|---|
| `test_validation.py` | filename, extension, size, empty, JSON sniffing, ZIP structure, zip bomb |
| `test_storage.py` | save/read/delete, path-traversal protection |
| `test_geo_reader.py` | GeoJSON/Shapefile parsing, CRS presence, invalid geometry detection |
| `test_crs.py` | UTM selection, unit conversion, Web Mercator, coordinate/extent guards |
| `test_measurements.py` | area/length/perimeter vs. `pyproj.Geod`, points, mixed data |
| `test_upload.py`, `test_jobs.py`, `test_shapefile_api.py`, `test_end_to_end.py` | the HTTP API end to end |
| `test_job_service.py` | status transitions, cleanup on DB failure |
| `test_health.py`, `test_models.py`, `test_error_handlers.py` | health check, ORM models, error format |

---

## Design decisions
- **Two-stage validation.** Cheap checks during the request give instant 4xx errors. Expensive parsing happens in the background and is reported as `FAILED` with a reason.
- **The extension is not trusted.** A `.geojson` must parse as GeoJSON, and a `.zip` must be a real ZIP containing a complete shapefile.
- **Skip-and-report invalid geometries.** One bad feature doesn't fail a large file, and no data is silently dropped.
- **No geometry auto-repair.** `make_valid` would change measured areas without the user knowing.
- **Safe storage.** Paths are built from the server-generated UUID only. ZIPs are read in place through GDAL's `/vsizip/` and never extracted, so zip-slip is impossible.
- **Separate tables.** `geospatial_jobs` holds the lifecycle and `measurement_results` holds the data, one-to-one. A failed job simply has no result row.
- **Clean errors.** Raw GDAL and Python errors are logged server-side. Users see a short, stable message.

## Known limitations
1. **Background tasks run inside the API process.** If it restarts mid-job, that job stays `QUEUED` or `PROCESSING`, and nothing retries it. A production system would use a durable queue (Celery/RQ).
2. **UTM-based measurement.** Lon/lat data wider than 12° of longitude (including antimeridian-crossing data), or near the poles, is rejected rather than measured inaccurately. Geodesic measurement with `pyproj.Geod` would remove this limit.
3. **Two formats only.** Only GeoJSON and zipped Shapefile are supported, and a ZIP must hold exactly one shapefile.
4. **No cleanup.** Uploaded files are kept for debugging and never deleted.
5. **No auth.** There is no authentication or rate limiting.
