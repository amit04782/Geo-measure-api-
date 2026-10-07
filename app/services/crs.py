"""Projected-CRS selection.

Strategy: regardless of the CRS a file arrives in, measure in a *local* UTM zone
chosen from the geometry's own centroid (WGS84 lon/lat). Polar geometries
(|lat| > 84) use UPS. This avoids two traps:
  * measuring in degrees (EPSG:4326), and
  * measuring in a globally-distorted projected CRS such as Web Mercator.
"""
from __future__ import annotations

from functools import lru_cache

from pyproj import CRS, Transformer
from shapely.geometry.base import BaseGeometry

WGS84 = CRS.from_epsg(4326)


def crs_label(crs: CRS | None) -> str | None:
    """Human-friendly CRS id, e.g. 'EPSG:4326'; falls back to the CRS name."""
    if crs is None:
        return None
    try:
        auth = crs.to_authority()
        if auth:
            return f"{auth[0]}:{auth[1]}"
    except Exception:  # pragma: no cover - defensive
        pass
    return crs.name


def utm_epsg_for(lon: float, lat: float) -> int:
    if lat > 84:
        return 32661  # WGS84 / UPS North
    if lat < -80:
        return 32761  # WGS84 / UPS South
    lon = ((lon + 180.0) % 360.0) - 180.0
    zone = min(int((lon + 180.0) // 6) + 1, 60)
    return (32600 if lat >= 0 else 32700) + zone


@lru_cache(maxsize=256)
def _transformer(src_wkt: str, dst_epsg: int) -> Transformer:
    return Transformer.from_crs(CRS.from_wkt(src_wkt), CRS.from_epsg(dst_epsg), always_xy=True)


@lru_cache(maxsize=64)
def _to_wgs84(src_wkt: str) -> Transformer:
    return Transformer.from_crs(CRS.from_wkt(src_wkt), WGS84, always_xy=True)


def lonlat_centroid(geom: BaseGeometry, src: CRS) -> tuple[float, float]:
    c = geom.centroid
    if src.equals(WGS84):
        return c.x, c.y
    return _to_wgs84(src.to_wkt()).transform(c.x, c.y)


def projector_for(geom: BaseGeometry, src: CRS) -> tuple[Transformer, int]:
    """Return (transformer src->local UTM, epsg) for this geometry."""
    lon, lat = lonlat_centroid(geom, src)
    epsg = utm_epsg_for(lon, lat)
    return _transformer(src.to_wkt(), epsg), epsg
