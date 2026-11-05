- The GIS stack, from conda-forge in one pixi environment (`$GIS_ENV`): GDAL,
  PDAL and QGIS (`qgis_process`, PyQGIS, offscreen) with their Python bindings,
  and rasterio, geopandas, shapely, pyproj, pystac-client, rioxarray and DuckDB.
  Its Python is the `python3` on PATH. Add conda packages with
  `pixi add --manifest-path /opt/gis/pixi.toml <name>`; for a project's own
  dependencies, `uv venv --python "$GIS_ENV/bin/python" --system-site-packages`.
  Don't pip-install GDAL or rasterio wheels on top: they bring a second GDAL.
