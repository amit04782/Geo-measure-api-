import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Feature, FileStatus, UploadedFile
from app.schemas import (
    FeatureMeasurementOut, FeatureOut, FeaturesOut, FileOut, MeasurementsOut, MeasurementSummary,
)
from app.services.ingest import InvalidUpload, detect_type
from app.services.pipeline import process_upload

router = APIRouter(prefix="/api/files", tags=["files"])


def _get_file(db: Session, file_id: str) -> UploadedFile:
    f = db.get(UploadedFile, file_id)
    if f is None:
        raise HTTPException(404, "File not found")
    return f


def _completed_file(db: Session, file_id: str) -> UploadedFile:
    f = _get_file(db, file_id)
    if f.status != FileStatus.COMPLETED.value:
        raise HTTPException(409, f"File is not ready (status={f.status}). {f.error or ''}".strip())
    return f


@router.post("/", response_model=FileOut, status_code=201)
def upload_file(file: UploadFile, db: Session = Depends(get_db)):
    """Upload a zipped Shapefile or a KML; it is parsed and measured synchronously."""
    filename = Path(file.filename or "").name
    try:
        detect_type(filename)
    except InvalidUpload as exc:
        raise HTTPException(415, str(exc))

    limit = settings.max_upload_mb * 1024 * 1024
    with tempfile.TemporaryDirectory(prefix="geo_") as tmp:
        workdir = Path(tmp)
        dest = workdir / ("upload" + Path(filename).suffix.lower())
        size = 0
        with open(dest, "wb") as out:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"File exceeds {settings.max_upload_mb} MB limit")
                out.write(chunk)
        if size == 0:
            raise HTTPException(400, "Uploaded file is empty")
        record = process_upload(db, filename, dest, workdir)

    body = FileOut.model_validate(record).model_dump(mode="json")
    if record.status == FileStatus.FAILED.value:
        return JSONResponse(body, status_code=422)  # record kept so the error is retrievable
    return body


@router.get("/{file_id}/", response_model=FileOut)
def get_file(file_id: str, db: Session = Depends(get_db)):
    return _get_file(db, file_id)


@router.get("/{file_id}/features/", response_model=FeaturesOut)
def list_features(
    file_id: str,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Extracted features: index, geometry type, geometry (GeoJSON, source CRS), CRS, properties."""
    f = _completed_file(db, file_id)
    rows = db.scalars(
        select(Feature).where(Feature.file_id == file_id).order_by(Feature.index).limit(limit).offset(offset)
    ).all()
    return FeaturesOut(
        file_id=file_id, total=f.feature_count, limit=limit, offset=offset,
        results=[FeatureOut.model_validate(r) for r in rows],
    )


@router.get("/{file_id}/measurements/", response_model=MeasurementsOut)
def get_measurements(
    file_id: str,
    geometry_type: str | None = Query(None, description="Filter, e.g. Polygon, LineString"),
    include_geometry: bool = False,
    include_properties: bool = True,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    f = _completed_file(db, file_id)
    filters = [Feature.file_id == file_id]
    if geometry_type:
        filters.append(func.lower(Feature.geometry_type) == geometry_type.lower())

    total = db.scalar(select(func.count()).select_from(Feature).where(*filters)) or 0
    rows = db.scalars(
        select(Feature).where(*filters).order_by(Feature.index).limit(limit).offset(offset)
    ).all()

    # Summary covers the whole (filtered) set, not just the current page.
    area = length = 0.0
    measured = skipped = 0
    for (m,) in db.execute(select(Feature.measurement).where(*filters)):
        if m.get("status") == "OK":
            measured += 1
            area += m.get("area_sq_m") or 0.0
            length += m.get("length_m") or 0.0
        else:
            skipped += 1

    return MeasurementsOut(
        file_id=file_id, total=total, limit=limit, offset=offset,
        summary=MeasurementSummary(
            total_area_sq_m=round(area, 4), total_length_m=round(length, 4),
            measured=measured, skipped=skipped,
        ),
        results=[
            FeatureMeasurementOut(
                index=r.index, layer=r.layer, geometry_type=r.geometry_type, crs=r.crs,
                properties=r.properties if include_properties else None,
                geometry=r.geometry if include_geometry else None,
                measurement=r.measurement,
            )
            for r in rows
        ],
    )


@router.delete("/{file_id}/", status_code=204)
def delete_file(file_id: str, db: Session = Depends(get_db)):
    db.delete(_get_file(db, file_id))
    db.commit()
