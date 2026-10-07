# Geospatial File Measurement API: High-Level Design

Status: **DRAFT, awaiting approval**. No implementation code exists yet.

---

## 1. What we are building

A small REST service that:

1. Accepts an uploaded geospatial file (GeoJSON or zipped Shapefile).
2. Validates the file cheaply and synchronously, so bad uploads are rejected immediately.
3. Stores the file on disk and creates a **job** row in PostgreSQL (`QUEUED`).
4. Processes the file in a background task. It parses the file, validates geometries, transforms them to a metric CRS and measures them.
5. Persists the job status and the measurement result.
6. Exposes endpoints to read the job status and the measurement result.

Out of scope: auth, users, frontend, S3, Redis/Celery, PostGIS, metrics, caching.

---

## 2. Key decisions (summary)

| # | Decision | Choice | Why |
|---|----------|--------|-----|
| D1 | Background processing | **FastAPI `BackgroundTasks`** | Files are small (size-capped) and processing takes seconds. We need a job lifecycle, but we do not need a separate worker or queue. Celery+Redis would add 2 services for no requirement. |
| D2 | Formats | **GeoJSON** (`.geojson`, `.json`) and **zipped Shapefile** (`.zip`) | These are the two formats the brief names. A bare `.shp` upload is rejected because it cannot work without `.shx`/`.dbf`/`.prj`. |
| D3 | Invalid geometries | **Skip and report** (partial success) | One bad feature should not fail a 10,000-feature file. Skipped features are counted and listed with a reason. If **all** geometries are unusable, the job is `FAILED`. |
| D4 | Geometry repair | **No auto-repair** (`make_valid` not used) | Repairing silently changes the measured area. We report invalid features and leave the decision to the user. |
| D5 | Measurement CRS | Geographic source → **auto-selected UTM zone** (`GeoDataFrame.estimate_utm_crs()`). Projected source → **use as-is**, converting units to metres. | Simple and standard, and easy to explain. Limitation: large extents spanning many UTM zones lose accuracy (documented). |
| D6 | Missing CRS | GeoJSON without a CRS is treated as **EPSG:4326** (RFC 7946 mandates WGS84). A Shapefile without a `.prj` is **rejected**. | This follows the GeoJSON spec. A Shapefile with no `.prj` has unknown units, so measuring it would be a guess. |
| D7 | Database | Plain **PostgreSQL**, no PostGIS | We never run spatial SQL queries. We only store numbers and metadata. |
| D8 | File storage | `StorageBackend` interface + `LocalStorage` | The server generates every path, so there is no path traversal. S3 could be added later by writing one more class. |
| D9 | Points | Counted, but area/length are `null` (not `0`) | `0` would wrongly suggest "measured and found to be zero". |
| D10 | Test DB | SQLite in-memory for unit/API tests. Same models via portable SQLAlchemy types (`Uuid`, `JSON`). | Tests run with plain `pytest` and need no Docker or internet. Postgres + Alembic are verified by the Docker smoke test. |

---

## 3. Architecture

```
          Client (curl / Postman / Swagger UI)
                         |
                         v
+----------------------------------------------------------+
| FastAPI app                                              |
|                                                          |
|  API layer (routes)   -- thin: parse request, call       |
|    health.py             services, map to response       |
|    jobs.py                                               |
|        |                                                 |
|        v                                                 |
|  Service layer                                           |
|    file_validation.py  -- extension, size, magic bytes,  |
|                           zip members, JSON structure    |
|    storage.py          -- StorageBackend / LocalStorage  |
|    job_service.py      -- create job, run job (bg task), |
|                           status transitions             |
|    geo_reader.py       -- file -> GeoDataFrame, CRS read,|
|                           geometry validation            |
|    measurement.py      -- pure functions: GeoDataFrame   |
|                           -> MeasurementResult           |
|    utils/crs.py        -- choose measurement CRS, units  |
|        |                         |                       |
+--------|-------------------------|-----------------------+
         v                         v
   PostgreSQL               Local disk (UPLOAD_DIR)
   (jobs, results)          /<job_id>/upload.<ext>
```

**Rules:**
- Routes contain **no** geospatial logic.
- `geo_reader`, `measurement` and `crs` do **not** import FastAPI or SQLAlchemy. They are pure and can be unit-tested with an in-memory GeoDataFrame.
- `job_service` is the only module that ties DB + storage + geo processing together.

---

## 4. Request / processing flow

### 4.1 Upload (synchronous part, inside the request)

```
POST /api/v1/geospatial/jobs  (multipart, field "file")
  1. filename present & sane?              no  -> 400 INVALID_FILENAME
  2. extension in {.geojson,.json,.zip}?   no  -> 415 UNSUPPORTED_FILE_TYPE
  3. stream to temp, count bytes
       > MAX_UPLOAD_SIZE_MB                    -> 413 FILE_TOO_LARGE
       == 0 bytes                              -> 400 EMPTY_FILE
  4. content sniffing (don't trust extension):
       .zip  -> valid zip? exactly one .shp? matching .shx/.dbf present?
                .prj present? uncompressed size within limit (zip-bomb guard)?
       .geojson/.json -> valid JSON? top-level "type" is FeatureCollection /
                Feature / a geometry type?
       fail -> 400 INVALID_FILE (with a human message)
  5. storage.save(job_id, ext, stream)  -> storage_key
  6. INSERT job (status=QUEUED)
  7. background_tasks.add_task(run_job, job_id)
  8. 201 {job_id, status: "QUEUED", status_url, result_url}
```

The cheap checks (steps 1–4) give the client an immediate 4xx. The expensive work (GDAL parsing, reprojection) happens in the background.

### 4.2 Background job (`job_service.run_job(job_id)`)

```
open NEW db session (request session is already closed)
status = PROCESSING
try:
    gdf = geo_reader.read(path, format)        # pyogrio via geopandas
    geo_reader.ensure_crs(gdf, format)         # D6
    split = geo_reader.split_valid_invalid(gdf)
    if split.valid is empty -> raise NoValidGeometriesError
    result = measurement.measure(split)        # uses utils/crs.py
    INSERT measurement_result; status = COMPLETED     (one transaction)
except AppError as e:          # known, user-facing problem
    status = FAILED, error_message = e.message
except Exception:              # bug / unexpected
    log full traceback; status = FAILED,
    error_message = "Unexpected error while processing file"
```

Users never see a stack trace. The traceback goes to the server logs only.

---

## 5. Geometry handling rules

Each feature is classified **before** measuring:

| Case | Treatment | Reason string example |
|------|-----------|-----------------------|
| `None` / empty geometry | skipped, counted invalid | `"empty geometry"` |
| `shapely.is_valid == False` (self-intersection, etc.) | skipped, counted invalid | `explain_validity()` e.g. `"Self-intersection[77.5 12.9]"` |
| `GeometryCollection` | skipped, counted invalid | `"unsupported geometry type: GeometryCollection"` |
| Polygon / MultiPolygon | **area** + **perimeter** | — |
| LineString / MultiLineString (incl. LinearRing) | **length** | — |
| Point / MultiPoint | counted only | — |

**Mixed datasets** (e.g. points + lines + polygons in one FeatureCollection) are supported. Each measurement is aggregated over the geometry family it applies to:

- `area`, `perimeter`: sum over polygonal features, `null` if there are none.
- `length`: sum over linear features, `null` if there are none.
- Perimeter is **not** added into `length`, because that would mix two different quantities.

The invalid-feature list is capped at 50 entries (`{feature_index, reason}`) so the DB row stays bounded. The count is always exact.

**Bounding box**: computed over valid geometries, **in the source CRS** (so lon/lat for 4326 data). The response labels which CRS it is in.

---

## 6. CRS & measurement (`utils/crs.py` + `services/measurement.py`)

### Why transformation is required
EPSG:4326 coordinates are **degrees**. `polygon.area` on lon/lat gives "square degrees", which is not a real unit. The size of a degree also changes with latitude: 1° of longitude is ~111 km at the equator and ~0 km at the poles. To get metres we must first project to a CRS whose units are metres and whose distortion is small for the data's location.

### Algorithm

```
def choose_measurement_crs(gdf) -> CRS:
    src = gdf.crs
    if src.is_geographic:
        return gdf.estimate_utm_crs()        # UTM zone at the data's centroid
    if src.is_projected:
        return src                           # already planar
    raise UnsupportedCRSError                # e.g. geocentric / engineering

def linear_unit_factor(crs) -> float:
    # metres per CRS unit, e.g. 1.0 for metre, 0.3048 for foot
    return crs.axis_info[0].unit_conversion_factor

measure:
    m_crs = choose_measurement_crs(gdf)
    projected = gdf.to_crs(m_crs)
    f = linear_unit_factor(m_crs)
    area      = sum(polygons.area)      * f**2   -> "square_meters"
    perimeter = sum(polygons.length)    * f      -> "meters"
    length    = sum(lines.length)       * f      -> "meters"
```

### Documented assumptions / limits
- UTM is accurate to well under 0.1% inside its 6°-wide zone. Datasets spanning several zones (country/continent scale) will have growing error. A future improvement is geodesic measurement with `pyproj.Geod`.
- UTM is undefined beyond 84°N / 80°S. Such data fails with a clear error.
- A projected source CRS is trusted as given. Web Mercator (EPSG:3857) is **projected but heavily distorted**, so we treat 3857 like geographic data and re-project to UTM. This is a single explicit special case.
- Values are rounded to 3 decimals in responses.

**Correctness test**: a known 0.01° square or a 1° line near the equator is measured. The result is compared against `pyproj.Geod` (geodesic ground truth) within 0.5%.

---

## 7. API contracts

Base path: `/api/v1`. All errors share one shape:

```json
{ "error": { "code": "FILE_TOO_LARGE", "message": "File exceeds the 20 MB limit.", "details": null } }
```

### 7.1 `GET /health` → 200
```json
{ "status": "ok", "database": "ok" }
```
It runs `SELECT 1`. If the DB is unreachable it returns **503** with `"database": "unavailable"`.

### 7.2 `POST /api/v1/geospatial/jobs` → 201
Request: `multipart/form-data`, field `file`.
```json
{
  "job_id": "3f1c…",
  "status": "QUEUED",
  "status_url": "/api/v1/geospatial/jobs/3f1c…",
  "result_url": "/api/v1/geospatial/jobs/3f1c…/result"
}
```
Errors: 400 `INVALID_FILENAME` / `EMPTY_FILE` / `INVALID_FILE`, 413 `FILE_TOO_LARGE`, 415 `UNSUPPORTED_FILE_TYPE`, 422 (missing `file` field, from FastAPI validation, reshaped into the common error format).

### 7.3 `GET /api/v1/geospatial/jobs/{job_id}` → 200
```json
{
  "job_id": "3f1c…",
  "status": "FAILED",
  "file": { "filename": "parcels.zip", "format": "shapefile", "size_bytes": 48213 },
  "error_message": "Shapefile has no CRS (.prj missing).",
  "created_at": "2026-10-07T13:10:00Z",
  "updated_at": "2026-10-07T13:10:01Z"
}
```
Errors: 404 `JOB_NOT_FOUND`, 422 if `job_id` is not a UUID.

### 7.4 `GET /api/v1/geospatial/jobs/{job_id}/result` → 200
```json
{
  "job_id": "3f1c…",
  "status": "COMPLETED",
  "file": { "filename": "roads.geojson", "format": "geojson" },
  "crs": { "source": "EPSG:4326", "measurement": "EPSG:32643" },
  "geometry": {
    "total_features": 122,
    "valid_count": 120,
    "invalid_count": 2,
    "types": ["LineString", "MultiLineString"],
    "invalid_features": [
      { "feature_index": 17, "reason": "Self-intersection[77.51 12.93]" },
      { "feature_index": 40, "reason": "empty geometry" }
    ]
  },
  "bounding_box": { "crs": "EPSG:4326", "min_x": 77.5, "min_y": 12.8, "max_x": 77.7, "max_y": 13.1 },
  "measurements": {
    "area": null,          "area_unit": null,
    "perimeter": null,     "perimeter_unit": null,
    "length": 15342.421,   "length_unit": "meters"
  }
}
```
Errors: 404 `JOB_NOT_FOUND`. If the job is not finished yet, the endpoint returns 409 `JOB_NOT_COMPLETED` (status included in `details`). If the job failed, it returns 409 `JOB_FAILED` with the error message.

**Status code summary**: 201 create · 200 read · 400 bad content · 413 too large · 415 wrong type · 422 malformed request · 404 unknown job · 409 result not available · 503 DB down (health) · 500 only for unexpected errors.

---

## 8. Database schema

### `geospatial_jobs`: one row per upload, tracks lifecycle
| Column | Type | Why |
|---|---|---|
| `id` | UUID PK | Unguessable public ID. The server generates it. |
| `original_filename` | varchar(255) | Shown back to the user only. It is **never** used as a path. |
| `file_format` | varchar(20) | `geojson` \| `shapefile`. Tells the reader which driver to use. |
| `file_size_bytes` | bigint | Audit/debug. |
| `storage_key` | varchar(255) | Where the storage backend put the file (`<job_id>/upload.zip`). |
| `status` | varchar(20) | `QUEUED` \| `PROCESSING` \| `COMPLETED` \| `FAILED` (string + Python Enum, no DB enum type, which keeps migrations simple). Indexed. |
| `error_message` | text, nullable | User-safe failure reason. |
| `created_at`, `updated_at` | timestamptz | Lifecycle timing. |

### `measurement_results`: 1:1 with a completed job
| Column | Type | Why |
|---|---|---|
| `id` | UUID PK | |
| `job_id` | UUID FK → jobs.id, **unique**, on delete cascade | 1:1 relationship. |
| `total_features`, `valid_count`, `invalid_count` | int | Transparency about skipped data. |
| `geometry_types` | JSON (list of str) | Distinct types among valid geometries. |
| `invalid_features` | JSON (list of `{feature_index, reason}`) | Capped at 50. |
| `source_crs`, `measurement_crs` | varchar(100) | E.g. `EPSG:4326`, `EPSG:32643`. Needed to interpret the numbers. |
| `min_x`, `min_y`, `max_x`, `max_y` | double | Bounding box in source CRS. |
| `total_area` / `area_unit` | double / varchar, nullable | Null when there are no polygons. |
| `total_perimeter` / `perimeter_unit` | double / varchar, nullable | Null when there are no polygons. |
| `total_length` / `length_unit` | double / varchar, nullable | Null when there are no lines. |
| `created_at` | timestamptz | |

**Why two tables?** A job exists from the moment of upload, and it may fail and never have a result. Keeping results separate avoids a wide table that is mostly nulls for queued/failed jobs. It also keeps "lifecycle" and "data" concerns apart.

Uploaded files are **not** stored in Postgres.

---

## 9. File storage

```python
class StorageBackend(ABC):
    def save(self, job_id: UUID, extension: str, source: BinaryIO) -> str: ...  # returns storage_key
    def get_path(self, storage_key: str) -> Path: ...
    def delete(self, storage_key: str) -> None: ...

class LocalStorage(StorageBackend):   # root = settings.UPLOAD_DIR
```

- The key is always `f"{job_id}/upload{ext}"`, built from a UUID and a whitelisted extension. The user's filename is never used in it.
- `get_path` resolves the path and asserts it is still inside `UPLOAD_DIR`. This is a defence-in-depth guard against path traversal.
- Zip files are **not extracted**. GDAL reads them in place via its virtual filesystem (`zip://…`), so "zip-slip" attacks are not possible.
- Uploaded files are kept after processing so failed jobs can be debugged. No cleanup job is included, which is a documented limitation.

---

## 10. Error handling

```
AppError(code, message, http_status)          # base, in core/exceptions.py
 ├─ InvalidFilenameError         400
 ├─ EmptyFileError               400
 ├─ InvalidFileError             400
 ├─ FileTooLargeError            413
 ├─ UnsupportedFileTypeError     415
 ├─ JobNotFoundError             404
 ├─ JobNotCompletedError         409
 ├─ JobFailedError               409
 └─ (processing-only, become FAILED status, not HTTP responses)
     ├─ FileParseError             "Could not read file as GeoJSON/Shapefile"
     ├─ MissingCRSError
     ├─ UnsupportedCRSError
     └─ NoValidGeometriesError
```

Global handlers in `main.py`:
- `AppError`: returns `{error:{code,message,details}}` with its status.
- `RequestValidationError`: returns 422 in the same shape.
- `Exception`: logs the traceback and returns 500 `INTERNAL_ERROR` with a generic message.

---

## 11. Configuration (`core/config.py`, pydantic-settings, `.env`)

| Var | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://geo:geo@localhost:5432/geo` | psycopg 3 driver |
| `UPLOAD_DIR` | `./data/uploads` | |
| `MAX_UPLOAD_SIZE_MB` | `20` | |
| `MAX_UNCOMPRESSED_ZIP_MB` | `100` | zip-bomb guard |
| `LOG_LEVEL` | `INFO` | |

---

## 12. Project structure

```
geospatial-measurement-api/
├── app/
│   ├── main.py                  # app factory, routers, exception handlers
│   ├── api/
│   │   ├── deps.py              # get_db, get_storage
│   │   └── routes/
│   │       ├── health.py
│   │       └── jobs.py
│   ├── core/
│   │   ├── config.py
│   │   └── exceptions.py
│   ├── db/
│   │   ├── base.py              # DeclarativeBase
│   │   ├── session.py           # engine, SessionLocal
│   │   └── models.py            # GeospatialJob, MeasurementResult
│   ├── schemas/
│   │   ├── jobs.py              # Pydantic response models
│   │   └── errors.py
│   ├── services/
│   │   ├── file_validation.py
│   │   ├── storage.py
│   │   ├── geo_reader.py
│   │   ├── measurement.py
│   │   └── job_service.py       # includes run_job (background task)
│   └── utils/
│       └── crs.py
├── alembic/  + alembic.ini
├── tests/
│   ├── conftest.py              # SQLite session, TestClient, tmp UPLOAD_DIR, fixture builders
│   ├── fixtures/                # tiny hand-written .geojson files
│   ├── test_health.py
│   ├── test_upload.py           # happy path + 400/413/415/422
│   ├── test_validation.py       # file_validation unit tests (zip members, json sniffing)
│   ├── test_storage.py
│   ├── test_crs.py
│   ├── test_measurements.py     # area/length/points/mixed/invalid vs pyproj.Geod
│   └── test_jobs.py             # status, result, 404, 409, failed jobs
├── docs/HLD.md
├── Dockerfile
├── docker-compose.yml
├── requirements.txt / requirements-dev.txt
├── .env.example
├── .gitignore
└── README.md
```

Differences from the suggested layout, with reasons:
- `workers/tasks.py` is dropped. The background task is a single function (`run_job`) in `job_service.py`, and a separate package would be empty ceremony.
- `geospatial_service.py` is split into `geo_reader.py` (reading/validating) and `measurement.py` (maths), so each can be tested on its own.
- `file_service.py` is renamed `file_validation.py` to say what it does.

Shapefile test fixtures are **generated in `conftest.py`** with `geopandas.to_file()` into `tmp_path` and then zipped. This is deterministic, and no binary blobs go into git.

---

## 13. Testing strategy

| Area | Tests |
|---|---|
| Health | 200 + `database: ok` |
| Upload | valid GeoJSON → 201 & job COMPLETED; valid shapefile zip → COMPLETED |
| Validation | unsupported ext (`.txt`, bare `.shp`) → 415; empty → 400; oversized (limit lowered via settings override) → 413; non-JSON `.geojson` → 400; corrupt zip → 400; zip missing `.shx`/`.dbf`/`.prj` → 400 |
| CRS | 4326 → UTM zone picked correctly (e.g. Bengaluru → EPSG:32643); projected metre CRS used as-is; foot-based CRS converted; 3857 re-projected; shapefile zip without `.prj` → 400 at upload; `.prj` present but unreadable → job FAILED |
| Measurements | polygon area vs `pyproj.Geod` within 0.5%; line length vs Geod; points → area/length `null`; mixed dataset → each aggregate correct; GeometryCollection skipped |
| Invalid geometry | bow-tie polygon skipped + reported with reason; all-invalid → FAILED |
| Jobs | status fields & timestamps; result for COMPLETED; 404 unknown UUID; 422 non-UUID; 409 for QUEUED and FAILED jobs |
| Storage | save/get_path round trip; key never uses user filename; traversal key rejected |

Notes:
- `TestClient` executes `BackgroundTasks` before returning, so job tests are deterministic and need no sleeps.
- The background task opens its own session via an injectable session factory, which tests point to SQLite.
- No network access is needed. pyproj ships its own PROJ database.

---

## 14. Docker

- **Dockerfile**: `python:3.12-slim`. pip installs wheels for shapely/pyproj/pyogrio that bundle GEOS/PROJ/GDAL, so no system GDAL install is needed. It runs as a non-root user.
- **docker-compose.yml**: two services only.
  - `db`: `postgres:16-alpine`, named volume, healthcheck `pg_isready`.
  - `api`: `depends_on: db (service_healthy)`, command `alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000`, uploads volume, healthcheck on `/health`.
- Migrations run on container start. That is fine for a single-instance assignment and is documented.

---

## 15. Known limitations (to put in README)

1. `BackgroundTasks` runs in the API process. If the process restarts mid-job, that job stays `QUEUED`/`PROCESSING` and there are no retries. A real deployment would use a queue (Celery/RQ) for durability.
2. UTM-based measurement loses accuracy for data spanning many UTM zones, and fails near the poles.
3. Only GeoJSON and zipped Shapefile are supported. A zip must contain exactly one shapefile.
4. Invalid geometries are skipped, not repaired.
5. Uploaded files are never cleaned up.
6. There is no auth and no rate limiting.

---

## 16. Implementation plan (one commit per phase, one-line messages, no trailers)

| # | Commit message | Contents | Tests run |
|---|---|---|---|
| 1 | `chore: initialize FastAPI project` | config, app factory, `/health` (no DB yet), requirements, Dockerfile, compose skeleton, .gitignore, HLD | health |
| 2 | `feat: add geospatial database models` | SQLAlchemy models, session, Alembic init + first migration, DB check in `/health` | health, model create/read on SQLite |
| 3 | `feat: add upload validation and local storage` | `file_validation.py`, `storage.py`, exceptions + error handlers | validation, storage |
| 4 | `feat: implement geospatial file parsing` | `geo_reader.py`: read, CRS presence, valid/invalid split | reader tests |
| 5 | `feat: add CRS transformation and measurements` | `utils/crs.py`, `measurement.py` | crs, measurements |
| 6 | `feat: implement geospatial job processing` | `job_service.py`, `POST /jobs`, background `run_job` | upload |
| 7 | `feat: add job status and result APIs` | GET endpoints, response schemas | jobs |
| 8 | `fix: handle geospatial processing edge cases` | gaps found while testing (all-invalid, 3857, GeometryCollection, zip bomb, etc.) | full suite |
| 9 | `test: complete geospatial test suite` | remaining cases from §13 | full suite |
| 10 | `docs: add setup and API documentation` | README, .env.example, compose finalised, Docker smoke test | full suite + `docker compose up` smoke test |
