#!/bin/bash
# The gis toolset's smoke test (specs/gcp.md, m4): GDAL reads a GeoJSON, and the
# pixi environment's Python imports its core modules, with nothing printed (PDAL
# reports a plugin it can't load and carries on), on one GDAL.
set -euo pipefail

cat >points.geojson <<'GEOJSON'
{"type": "FeatureCollection", "features": [
  {"type": "Feature", "properties": {"name": "a"}, "geometry": {"type": "Point", "coordinates": [-122.4, 37.8]}},
  {"type": "Feature", "properties": {"name": "b"}, "geometry": {"type": "Point", "coordinates": [-73.9, 40.7]}}
]}
GEOJSON
count="$(ogrinfo -ro -so -al points.geojson | sed -n 's/^Feature Count: //p')"
[[ "$count" == 2 ]] || {
	echo "gis: ogrinfo read ${count:-no} features from points.geojson, expected 2"
	exit 1
}

modules=(osgeo.gdal rasterio geopandas pdal qgis.core pystac_client duckdb pyarrow geoarrow.pyarrow
	polars zarr dask_geopandas odc.stac h3 osmium pmtiles)
out="$(
	IFS=,
	python3 -c "import ${modules[*]}" 2>&1
)"
[[ -z "$out" ]] || {
	echo "$out"
	echo "gis: importing the Python modules printed the above"
	exit 1
}
gdal="$(python3 -c 'import osgeo.gdal, rasterio; a, b = osgeo.gdal.__version__, rasterio.__gdal_version__; assert a == b, (a, b); print(a)')"
echo "gis: ogrinfo read 2 features; ${#modules[@]} modules import, on one GDAL $gdal"
