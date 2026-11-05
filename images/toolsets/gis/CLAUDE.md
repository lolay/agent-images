- The GIS stack, from conda-forge in one pixi environment (`$GIS_ENV`): GDAL,
  PDAL and QGIS (`qgis_process`, PyQGIS, offscreen) with their Python bindings;
  rasterio, geopandas, shapely, pyproj, rioxarray; cloud-native formats and scale
  with DuckDB, pyarrow and geoarrow (GeoParquet), polars, Zarr, Dask and
  dask-geopandas, pystac-client, odc-stac, planetary-computer and rio-cogeo;
  untwine for COPC; tippecanoe, pmtiles, h3 and osmium. Its Python is the
  `python3` on PATH. Plugins conda-forge's builds left unloadable are listed in
  `/opt/gis/dropped-plugins.txt`. Add conda packages with
  `pixi add --manifest-path /opt/gis/pixi.toml <name>`; for a project's own
  dependencies, `uv venv --python "$GIS_ENV/bin/python" --system-site-packages`.
  Don't pip-install GDAL or rasterio wheels on top: they bring a second GDAL.
