import io
import os
import zipfile

os.environ["GEO_DATABASE_URL"] = "sqlite:///:memory:"

import geopandas as gpd  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from shapely.geometry import LineString, Point, Polygon  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<Folder><name>Survey</name>
 <Placemark><name>Plot A</name><description>test plot</description>
  <Polygon><outerBoundaryIs><LinearRing><coordinates>
   77.0,28.0,0 77.01,28.0,0 77.01,28.01,0 77.0,28.01,0 77.0,28.0,0
  </coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
 <Placemark><name>Road 1</name>
  <LineString><coordinates>77.0,28.0,0 77.01,28.0,0</coordinates></LineString></Placemark>
 <Placemark><name>Well</name><Point><coordinates>77.005,28.005,0</coordinates></Point></Placemark>
 <Placemark><name>Mixed</name><MultiGeometry>
   <Point><coordinates>77.0,28.0</coordinates></Point>
   <LineString><coordinates>77.0,28.0 77.1,28.1</coordinates></LineString>
 </MultiGeometry></Placemark>
</Folder></Document></kml>
"""

# Reference polygon/line (UTM-zone-43N territory) for the shapefile test.
POLY = Polygon([(77.0, 28.0), (77.01, 28.0), (77.01, 28.01), (77.0, 28.01)])
LINE = LineString([(77.0, 28.0), (77.01, 28.0)])


@pytest.fixture
def kml_bytes():
    return KML.encode()


@pytest.fixture
def shapefile_zip(tmp_path):
    gdf = gpd.GeoDataFrame(
        {"name": ["poly", "pt", "line"], "pop": [10, 20, 30]},
        geometry=[POLY, Point(77.0, 28.0), LINE],
        crs="EPSG:4326",
    )
    # Shapefiles hold a single geometry type, so write one file per type.
    for name, sub in {"polys": gdf.iloc[[0]], "pts": gdf.iloc[[1]], "lines": gdf.iloc[[2]]}.items():
        sub.to_file(tmp_path / f"{name}.shp")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for p in tmp_path.iterdir():
            z.write(p, p.name)
    return buf.getvalue()
