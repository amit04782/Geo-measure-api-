"""Measurement calculation for a single geometry."""
from __future__ import annotations

import math

import shapely
from pyproj import CRS
from shapely.geometry.base import BaseGeometry

from app.services.crs import projector_for

POLYGONAL = {"Polygon", "MultiPolygon"}
LINEAR = {"LineString", "MultiLineString", "LinearRing"}
POINTLIKE = {"Point", "MultiPoint"}


def measure_geometry(geom: BaseGeometry | None, crs: CRS | None) -> dict:
    """Never raises: every failure mode is reported in the returned dict."""
    if geom is None or geom.is_empty:
        return {"status": "UNSUPPORTED", "message": "Empty or null geometry"}

    gtype = geom.geom_type
    if gtype in POINTLIKE:
        return {"status": "NOT_APPLICABLE", "message": "Points have no measurement"}
    if gtype not in POLYGONAL | LINEAR:
        return {"status": "UNSUPPORTED", "message": f"Measurement not supported for {gtype}"}
    if crs is None:
        return {"status": "UNSUPPORTED", "message": "Unknown CRS (no .prj?) - cannot measure"}

    try:
        geom2d = shapely.force_2d(geom)  # KML is often 3D; Z is irrelevant for planar measures
        transformer, epsg = projector_for(geom2d, crs)
        projected = shapely.transform(
            geom2d, lambda xy: _apply(transformer, xy)
        )
        if not shapely.is_valid(projected) or not all(
            math.isfinite(v) for v in shapely.get_coordinates(projected).ravel()
        ):
            # Still measure, but flag it - self-intersecting rings give unreliable areas.
            note = "Geometry is invalid (e.g. self-intersection); result may be unreliable"
        else:
            note = None

        out: dict = {"status": "OK", "projected_crs": f"EPSG:{epsg}", "message": note}
        if gtype in POLYGONAL:
            out["area_sq_m"] = round(projected.area, 4)
            out["perimeter_m"] = round(projected.length, 4)
        else:
            out["length_m"] = round(projected.length, 4)
        return out
    except Exception as exc:  # noqa: BLE001 - one bad feature must not fail the file
        return {"status": "ERROR", "message": f"{type(exc).__name__}: {exc}"}


def _apply(transformer, xy):
    import numpy as np

    x, y = transformer.transform(xy[:, 0], xy[:, 1])
    return np.column_stack([x, y])
