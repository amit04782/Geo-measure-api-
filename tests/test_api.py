import io
import zipfile

import pytest
from pyproj import Geod

from app.services.measure import measure_geometry
from tests.conftest import LINE, POLY

GEOD = Geod(ellps="WGS84")


def upload(client, name, data, ctype="application/octet-stream"):
    return client.post("/api/files/", files={"file": (name, data, ctype)})


def test_kml_upload_and_measurements(client, kml_bytes):
    r = upload(client, "survey.kml", kml_bytes)
    assert r.status_code == 201
    info = r.json()
    assert info["status"] == "COMPLETED" and info["crs"] == "EPSG:4326"
    assert info["feature_count"] == 4

    assert client.get(f"/api/files/{info['id']}/").json()["filename"] == "survey.kml"

    m = client.get(f"/api/files/{info['id']}/measurements/").json()
    by_type = {r["geometry_type"]: r for r in m["results"]}

    area = by_type["Polygon"]["measurement"]["area_sq_m"]
    ref_area, _ = GEOD.geometry_area_perimeter(POLY)
    assert area == pytest.approx(abs(ref_area), rel=0.002)       # matches geodesic truth
    assert area != pytest.approx(0.01 * 0.01, rel=1)             # i.e. not degrees^2

    length = by_type["LineString"]["measurement"]["length_m"]
    assert length == pytest.approx(GEOD.geometry_length(LINE), rel=0.002)
    assert by_type["Polygon"]["measurement"]["projected_crs"] == "EPSG:32643"

    assert by_type["Point"]["measurement"]["status"] == "NOT_APPLICABLE"
    assert by_type["GeometryCollection"]["measurement"]["status"] == "UNSUPPORTED"
    assert m["summary"]["measured"] == 2 and m["summary"]["skipped"] == 2


def test_shapefile_upload(client, shapefile_zip):
    r = upload(client, "data.zip", shapefile_zip)
    assert r.status_code == 201, r.text
    fid = r.json()["id"]
    assert r.json()["feature_count"] == 3

    feats = client.get(f"/api/files/{fid}/features/").json()["results"]
    assert {f["geometry_type"] for f in feats} == {"Polygon", "Point", "LineString"}
    poly = next(f for f in feats if f["geometry_type"] == "Polygon")
    assert poly["properties"] == {"name": "poly", "pop": 10}
    assert poly["crs"] == "EPSG:4326" and poly["geometry"]["type"] == "Polygon"

    m = client.get(f"/api/files/{fid}/measurements/?geometry_type=polygon").json()
    assert m["total"] == 1 and m["results"][0]["measurement"]["area_sq_m"] > 0


def test_pagination_and_geometry_flag(client, kml_bytes):
    fid = upload(client, "s.kml", kml_bytes).json()["id"]
    m = client.get(f"/api/files/{fid}/measurements/?limit=2&offset=1&include_geometry=true").json()
    assert len(m["results"]) == 2 and m["total"] == 4
    assert m["results"][0]["geometry"] is not None


def test_rejects_bad_extension(client):
    assert upload(client, "x.txt", b"hello").status_code == 415


def test_corrupt_kml_is_failed_not_500(client):
    r = upload(client, "bad.kml", b"<not really kml")
    assert r.status_code == 422 and r.json()["status"] == "FAILED"
    assert client.get(f"/api/files/{r.json()['id']}/measurements/").status_code == 409


def test_zip_without_shp(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("readme.txt", "hi")
    r = upload(client, "empty.zip", buf.getvalue())
    assert r.status_code == 422 and "No .shp" in r.json()["error"]


def test_zip_slip_rejected(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../evil.shp", "x")
    r = upload(client, "evil.zip", buf.getvalue())
    assert r.status_code == 422 and "unsafe" in r.json()["error"]


def test_unknown_id_404(client):
    assert client.get("/api/files/nope/").status_code == 404
    assert client.get("/api/files/nope/measurements/").status_code == 404


def test_unknown_crs_skips_measurement():
    assert measure_geometry(POLY, None)["status"] == "UNSUPPORTED"


def test_southern_hemisphere_and_web_mercator():
    import shapely
    from pyproj import CRS, Transformer

    poly = shapely.box(151.20, -33.90, 151.21, -33.89)  # Sydney
    ref, _ = GEOD.geometry_area_perimeter(poly)
    out = measure_geometry(poly, CRS.from_epsg(4326))
    assert out["projected_crs"] == "EPSG:32756"
    assert out["area_sq_m"] == pytest.approx(abs(ref), rel=0.002)

    # Same polygon delivered in Web Mercator must still give the right area.
    t = Transformer.from_crs(4326, 3857, always_xy=True)
    merc = shapely.transform(poly, lambda xy: __import__("numpy").column_stack(t.transform(xy[:, 0], xy[:, 1])))
    out2 = measure_geometry(merc, CRS.from_epsg(3857))
    assert out2["area_sq_m"] == pytest.approx(abs(ref), rel=0.002)
