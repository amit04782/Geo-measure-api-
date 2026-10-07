"""File ingestion: validate, safely unpack, read with GDAL (via pyogrio), normalise."""
from __future__ import annotations

import datetime as dt
import math
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import shapely
from pyogrio import list_layers, read_dataframe

from app.config import settings
from app.services.crs import crs_label
from app.services.measure import measure_geometry


class InvalidUpload(Exception):
    """The upload is unusable; message is safe to show to the client."""


@dataclass
class ParsedFeature:
    index: int
    layer: str | None
    geometry_type: str | None
    crs: str | None
    geometry: dict | None
    properties: dict
    measurement: dict


@dataclass
class ParsedFile:
    crs: str | None
    features: list[ParsedFeature] = field(default_factory=list)


def detect_type(filename: str) -> str:
    name = filename.lower()
    if name.endswith(".zip"):
        return "shapefile"
    if name.endswith(".kml"):
        return "kml"
    raise InvalidUpload("Unsupported file type. Upload a .zip (Shapefile) or a .kml file.")


def safe_extract_zip(zip_path: Path, dest: Path) -> list[Path]:
    """Extract, rejecting zip-slip paths and oversized archives."""
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise InvalidUpload("File is not a valid zip archive.") from exc

    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir() and "__MACOSX" not in i.filename]
        if len(infos) > settings.max_zip_members:
            raise InvalidUpload("Zip contains too many files.")
        if sum(i.file_size for i in infos) > settings.max_unzipped_mb * 1024 * 1024:
            raise InvalidUpload("Zip expands beyond the allowed size.")
        dest = dest.resolve()
        out: list[Path] = []
        for info in infos:
            target = (dest / info.filename).resolve()
            if dest not in target.parents:
                raise InvalidUpload("Zip contains an unsafe path.")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                dst.write(src.read())
            out.append(target)
        return out


def _find_shapefiles(files: list[Path]) -> list[Path]:
    shps = [f for f in files if f.suffix.lower() == ".shp"]
    if not shps:
        raise InvalidUpload("No .shp file found in the zip archive.")
    by_stem = {(f.parent, f.stem.lower()): set() for f in shps}
    for f in files:
        key = (f.parent, f.stem.lower())
        if key in by_stem:
            by_stem[key].add(f.suffix.lower())
    for (_, stem), exts in by_stem.items():
        missing = {".shx", ".dbf"} - exts
        if missing:
            raise InvalidUpload(
                f"Shapefile '{stem}' is missing required component(s): {', '.join(sorted(missing))}"
            )
    return shps


def _json_safe(v: Any) -> Any:
    """Convert pandas/numpy scalars (NaN, NaT, Timestamp, np.int64...) to JSON types."""
    if v is None or v is pd.NaT:
        return None
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    if isinstance(v, (np.integer, int)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(v, (pd.Timestamp, dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, bytes):
        return v.decode("utf-8", errors="replace")
    if isinstance(v, str):
        return v
    return str(v)


def _read_layer(path: Path, layer: str | None, start_index: int) -> tuple[ParsedFile, int]:
    try:
        gdf = read_dataframe(path, layer=layer)
    except Exception as exc:  # pyogrio raises DataSourceError / DataLayerError etc.
        raise InvalidUpload(f"Could not read geospatial data: {exc}") from exc

    crs = gdf.crs
    label = crs_label(crs)
    parsed = ParsedFile(crs=label)
    prop_cols = [c for c in gdf.columns if c != gdf.geometry.name]
    idx = start_index
    for geom, row in zip(gdf.geometry, gdf[prop_cols].itertuples(index=False, name=None)):
        props = {c: _json_safe(v) for c, v in zip(prop_cols, row)}
        has_geom = geom is not None and not geom.is_empty
        parsed.features.append(
            ParsedFeature(
                index=idx,
                layer=layer,
                geometry_type=geom.geom_type if geom is not None else None,
                crs=label,
                geometry=_to_geojson(geom) if has_geom else None,
                properties=props,
                measurement=measure_geometry(geom, crs),
            )
        )
        idx += 1
    return parsed, idx


def _to_geojson(geom) -> dict:
    import json

    return json.loads(shapely.to_geojson(geom))


def parse_geospatial_file(path: Path, file_type: str, workdir: Path) -> ParsedFile:
    """Read every layer of the file and return normalised, already-measured features."""
    if file_type == "shapefile":
        shapefiles = _find_shapefiles(safe_extract_zip(path, workdir / "unzipped"))
        sources = [(shp, None, shp.stem) for shp in shapefiles]
    else:
        try:
            layers = [str(name) for name, _ in list_layers(path)]
        except Exception as exc:
            raise InvalidUpload(f"Not a readable KML file: {exc}") from exc
        sources = [(path, name, name) for name in layers]  # KML Folders/Documents -> layers

    result = ParsedFile(crs=None)
    next_idx = 0
    for src, gdal_layer, display_name in sources:
        part, next_idx = _read_layer(src, gdal_layer, next_idx)
        for f in part.features:
            f.layer = display_name
        result.features.extend(part.features)
        result.crs = result.crs or part.crs
    return result
