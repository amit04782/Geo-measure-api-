# Geospatial File Measurement API

A FastAPI service that accepts a **zipped Shapefile** or a **KML**, extracts every feature
(index, geometry type, GeoJSON geometry, CRS, attributes) and returns **CRS-correct areas and lengths**.
Measurements are never computed in degrees: each geometry is projected to a local UTM zone first.

## Setup

Requires Python 3.10+ (wheels for GDAL/pyogrio, shapely and pyproj are bundled, so no system GDAL is needed).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload          # http://127.0.0.1:8000  (Swagger UI at /docs)
pytest                                  # run the test-suite
```

Docker alternative: `docker build -t geo-api . && docker run -p 8000:8000 geo-api`

Configuration (env vars, optional `.env`): `GEO_DATABASE_URL` (default `sqlite:///./data/app.db`),
`GEO_MAX_UPLOAD_MB` (50), `GEO_MAX_UNZIPPED_MB` (500), `GEO_MAX_ZIP_MEMBERS` (200).

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/files/` | Upload + process a `.zip` (Shapefile) or `.kml` |
| GET | `/api/files/{id}/` | File info / status |
| GET | `/api/files/{id}/features/` | Extracted features with geometry + properties |
| GET | `/api/files/{id}/measurements/` | Per-feature measurements + file-level summary |
| DELETE | `/api/files/{id}/` | Remove file and its features |
| GET | `/health` | Liveness |

### Upload
```bash
curl -F "file=@survey.kml" http://localhost:8000/api/files/
```
```json
{
  "id": "74c14215d57e4f59a5e192a129666457",
  "filename": "survey.kml",
  "file_type": "kml",
  "feature_count": 4,
  "crs": "EPSG:4326",
  "status": "COMPLETED",
  "error": null,
  "created_at": "2026-10-07T08:54:46.807590Z"
}
```
Errors: `415` wrong extension, `413` too large, `400` empty, `422` unreadable/invalid content
(the record is still stored with `status: "FAILED"` and an `error` message, retrievable via `GET`).

### Measurements
`GET /api/files/{id}/measurements/?limit=100&offset=0&geometry_type=Polygon&include_geometry=false&include_properties=true`
```json
{
  "file_id": "74c1...",
  "total": 4, "limit": 100, "offset": 0,
  "summary": {"total_area_sq_m": 1090165.1617, "total_length_m": 983.6972, "measured": 2, "skipped": 2},
  "results": [
    {
      "index": 0, "layer": "Survey", "geometry_type": "Polygon", "crs": "EPSG:4326",
      "properties": {"Name": "Plot A", "description": "test plot"},
      "geometry": null,
      "measurement": {"status": "OK", "area_sq_m": 1090165.1617, "perimeter_m": 4183.8708,
                      "length_m": null, "projected_crs": "EPSG:32643", "message": null}
    },
    {
      "index": 1, "layer": "Survey", "geometry_type": "LineString", "crs": "EPSG:4326",
      "measurement": {"status": "OK", "length_m": 983.6972, "projected_crs": "EPSG:32643"}
    }
  ]
}
```
`measurement.status` is one of:

| Status | Meaning |
|---|---|
| `OK` | measured (`area_sq_m` + `perimeter_m` for polygons, `length_m` for lines) |
| `NOT_APPLICABLE` | Point / MultiPoint - nothing to measure |
| `UNSUPPORTED` | GeometryCollection, empty/null geometry, or unknown CRS (e.g. shapefile without `.prj`) |
| `ERROR` | projection/calculation failed for this feature only |

### Features
`GET /api/files/{id}/features/` returns `index, layer, geometry_type, crs, geometry` (GeoJSON in the source CRS) and `properties`.

## Architecture

```
app/
  main.py            FastAPI app, lifespan creates tables
  config.py          env-driven settings
  database.py        SQLAlchemy engine/session
  models.py          UploadedFile, Feature (GeoJSON + measurement stored as JSON)
  schemas.py         Pydantic response models
  routers/files.py   HTTP layer only (validation, pagination, status codes)
  services/
    ingest.py        type detection, safe unzip, GDAL read, JSON-safe attributes
    crs.py           projected-CRS selection
    measure.py       area / length / perimeter for one geometry
    pipeline.py      upload -> parse -> measure -> persist, status transitions
tests/               API tests + accuracy checks against pyproj's geodesic solver
```

**File-processing flow.** The upload is streamed to a temp dir (size-capped) -> a `files` row is created
(`PROCESSING`) -> zips are extracted defensively (zip-slip, member-count and uncompressed-size checks;
`.shp/.shx/.dbf` presence verified) -> every layer is read with pyogrio/GDAL (KML Folders and Documents become
layers; a zip may contain several shapefiles) -> properties are made JSON-safe (NaN/NaT/numpy types) ->
features are measured and bulk-saved -> status becomes `COMPLETED` (or `FAILED` with a message).

**Measurement flow.** Per feature: null/empty -> `UNSUPPORTED`; Point -> `NOT_APPLICABLE`;
Polygon/MultiPolygon -> area (holes subtracted) and perimeter; LineString/MultiLineString -> length.
Z values are dropped first (KML is usually 3D). Anything else is reported, not raised. Results are computed at
ingest time and stored, so `GET .../measurements/` is a cheap read.

**CRS handling.** Whatever CRS the file declares (4326, another geographic CRS, a projected CRS, Web Mercator...),
the geometry's centroid is converted to WGS84 lon/lat, the matching **UTM zone** is chosen
(`EPSG:326xx` north / `327xx` south; UPS `32661/32761` beyond 84N / 80S), and the geometry is transformed into it
before `.area` / `.length` are taken (always `always_xy=True` to avoid axis-order bugs). The CRS used is returned per
feature as `projected_crs`. Files with no CRS cannot be measured and are marked per-feature rather than guessed.

## Design Decisions

- **FastAPI over Django/DRF.** The service is a thin, stateless API; FastAPI gives typed models, validation and
  OpenAPI docs with little code. Django would pay off if we needed auth, admin or GeoDjango/PostGIS.
- **Local UTM per feature vs. alternatives.** Alternatives considered: (a) one projected CRS for the whole file -
  simple but wrong for files spanning zones; (b) an equal-area projection (e.g. EPSG:6933) - correct areas
  but poor for lengths; (c) pure geodesic math (`pyproj.Geod`) - the most accurate over large extents.
  UTM is accurate to ~0.1% inside a zone, is what most GIS users expect, and its code is easy to explain. The tests
  use `Geod` as ground truth (tolerance 0.2%). Very large features (spanning many degrees) would be better served by (c).
- **Synchronous processing.** Typical files parse in well under a second, so the POST returns a finished result and the
  `status` field is already future-proof for async. For big files I would move `process_upload` to a task queue
  (Celery/RQ) and return `202` with `PENDING`.
- **SQLite + JSON columns.** Zero-setup for reviewers; features are written once and read as units. For spatial
  queries, switch to PostgreSQL/PostGIS with `geometry` columns (the SQLAlchemy URL is the only thing to change for Postgres + JSON).
- **Measure at ingest, not on read.** Reads are cheap and deterministic; the cost is recomputing if the
  strategy changes (fixable with a re-measure endpoint since geometry is stored).
- **Failure is data.** An unparsable file creates a `FAILED` record (`422` + `error`); a bad *feature* never fails the file.
- **pyogrio/GDAL instead of hand-parsing KML.** Handles folders, multi-geometries and the Shapefile quirks (encoding, `.prj`).
  Trade-off: GDAL's KML driver ignores some KML features (styles, network links, `gx:Track`).
- **Invalid geometries** are measured as-is with a warning in `message` instead of being silently "fixed".
- **Security.** Extension whitelist, streaming size cap, zip-slip and zip-bomb checks, extraction only inside a temp dir.

## Learnings

- Axis order is the classic trap: EPSG:4326 is lat/lon by definition but GDAL data is lon/lat; `always_xy=True` fixes it.
- Web Mercator area errors are huge away from the equator (about 2x at 45 degrees), so "already projected" does not mean "safe to measure".
- Shapefiles store one geometry type per file, while KML mixes them freely; pandas NaN/NaT silently break JSON serialisation.
- Validating against an independent geodesic solver caught more than eyeballing numbers.

## Future Scope

- Async processing + `202`/polling or webhooks; object storage for originals; re-measure endpoint.
- PostGIS storage, bbox/spatial filters, vector-tile or GeoJSON FeatureCollection export.
- Geodesic mode (`?method=geodesic`) and user-selectable target CRS / units (ha, acres, km, ft).
- More formats (GeoJSON, KMZ, GeoPackage, DXF), KML styles and `gx:Track`.
- Auth, rate limiting, per-user quotas, Alembic migrations, CI (lint + tests), structured logging/metrics.
- Antimeridian-crossing and multi-zone geometry handling; optional geometry repair (`make_valid`).
