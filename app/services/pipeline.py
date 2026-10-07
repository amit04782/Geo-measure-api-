"""Orchestrates: upload -> parse -> measure -> persist."""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import Feature, FileStatus, UploadedFile
from app.services.ingest import InvalidUpload, detect_type, parse_geospatial_file

log = logging.getLogger(__name__)


def process_upload(db: Session, filename: str, tmp_path: Path, workdir: Path) -> UploadedFile:
    record = UploadedFile(
        filename=filename, file_type=detect_type(filename), status=FileStatus.PROCESSING.value
    )
    db.add(record)
    db.commit()

    try:
        parsed = parse_geospatial_file(tmp_path, record.file_type, workdir)
        record.features = [
            Feature(
                index=f.index, layer=f.layer, geometry_type=f.geometry_type, crs=f.crs,
                geometry=f.geometry, properties=f.properties, measurement=f.measurement,
            )
            for f in parsed.features
        ]
        record.crs = parsed.crs
        record.feature_count = len(parsed.features)
        record.status = FileStatus.COMPLETED.value
    except InvalidUpload as exc:
        record.status, record.error = FileStatus.FAILED.value, str(exc)
    except Exception:  # noqa: BLE001
        log.exception("Unexpected failure processing %s", record.id)
        record.status, record.error = FileStatus.FAILED.value, "Unexpected error while processing file."
    db.commit()
    return record
