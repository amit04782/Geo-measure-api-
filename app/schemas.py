from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

MeasurementStatus = Literal["OK", "NOT_APPLICABLE", "UNSUPPORTED", "ERROR"]


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    file_type: str
    feature_count: int
    crs: str | None
    status: str
    error: str | None = None
    created_at: datetime


class Measurement(BaseModel):
    status: MeasurementStatus
    area_sq_m: float | None = None
    perimeter_m: float | None = None
    length_m: float | None = None
    projected_crs: str | None = None
    message: str | None = None


class FeatureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    index: int
    layer: str | None
    geometry_type: str | None
    crs: str | None
    geometry: dict[str, Any] | None
    properties: dict[str, Any]


class FeatureMeasurementOut(BaseModel):
    index: int
    layer: str | None
    geometry_type: str | None
    crs: str | None
    properties: dict[str, Any] | None = None
    geometry: dict[str, Any] | None = None
    measurement: Measurement


class MeasurementSummary(BaseModel):
    total_area_sq_m: float
    total_length_m: float
    measured: int
    skipped: int


class MeasurementsOut(BaseModel):
    file_id: str
    total: int
    limit: int
    offset: int
    summary: MeasurementSummary
    results: list[FeatureMeasurementOut]


class FeaturesOut(BaseModel):
    file_id: str
    total: int
    limit: int
    offset: int
    results: list[FeatureOut]
