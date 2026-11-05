# Test: `gis` runner

You're in a session on the `gis` runner type: a CPU Linux container with the GIS
stack (GDAL, PDAL, QGIS and their Python bindings), thrown away after the session.
Do a small but real analysis on public data, the kind of thing a geospatial engineer
does in a day, and write a report. If a check fails, record it and carry on. Time
every step. Work in `$AGENT_SCRATCH/runner-test` (`$HOME/runner-test` if that isn't set), and write outputs to
`tests/sessions/results/gis/<UTC timestamp>/` in the repo.

The area: farmland west of Fresno, California, bounding box
`-120.15,36.65,-120.05,36.75` (WGS 84). If anything below has no data there, pick a
nearby box that does and say so.

## 1. Facts

The shared facts (see [linux.md](linux.md), step 1), plus: `gdalinfo --version`,
`pdal --version`, `qgis_process --version`, the PROJ version, and whether Python can
import `osgeo.gdal`, `rasterio`, `geopandas`, `pdal` and `qgis.core`. Anything
missing is a finding; install it to carry on.

## 2. Imagery: NDVI from Sentinel-2

Search the public Earth Search STAC API
(`https://earth-search.aws.element84.com/v1`, collection `sentinel-2-l2a`) for the
least cloudy scene over the box between June and August of last year. Read only the
box from the red and near-infrared bands, straight from the cloud-optimized GeoTIFFs
over HTTP (`/vsicurl/` or rasterio windows; don't download whole scenes). Compute
NDVI, mask clouds and no-data with the scene classification band, and write:

- `ndvi.tif`: a cloud-optimized GeoTIFF in the scene's UTM zone, with overviews.
- `ndvi.png`: a color quicklook.

Pass if every valid value is within -1 to 1 and the box's mean NDVI over cropland is
above 0.3 (summer, irrigated). Record bytes read over the network against the size of
the full band files.

## 3. Vectors: fields and zonal statistics

Get field polygons for the box from OpenStreetMap (Overpass API: `landuse=farmland`
and `landuse=orchard`). If Overpass doesn't answer, use a 500 m grid instead and say
so. Write them as a GeoPackage and as GeoJSON (Polygons and MultiPolygons only), in
WGS 84 and in the UTM zone. Compute mean, min, max and standard deviation of NDVI per
polygon to `fields_ndvi.csv`, and record the five highest and lowest.

## 4. LiDAR: a canopy height model from USGS 3DEP

Find a USGS 3DEP lidar project covering a 500 m by 500 m square in the box with
trees in it (orchards), using the public index of 3DEP Entwine point tiles
(`https://raw.githubusercontent.com/hobuinc/usgs-lidar/master/boundaries/resources.geojson`).
With a PDAL pipeline reading only that square from the EPT:

- classify ground (`filters.smrf`) and write a 1 m DTM and a 1 m DSM (`writers.gdal`);
- compute the canopy height model as DSM minus DTM, `chm.tif`;
- count trees as local maxima above 2 m with a 2 m minimum spacing, `trees.geojson`
  (Points only);
- write `hillshade.png` from the DTM (`gdaldem hillshade`).

Pass if the CHM's 99th percentile is plausible for orchard trees (2 to 15 m) and the
tree count is non-zero. Record points read.

## 5. QGIS

- With `qgis_process`, run a native algorithm that repeats one step (zonal statistics
  for three of the fields, or the hillshade) and compare the result with yours. Pass
  if they agree within rounding.
- With PyQGIS and `QT_QPA_PLATFORM=offscreen`, render a map of NDVI with the field
  outlines and tree points on top to `map.png`.
- If `$DISPLAY` is set, open QGIS desktop with the same layers and screenshot the
  display to `qgis-desktop.png`.

## 6. Git and report

Commit the results directory (report, PNGs, CSV, the GeoJSON files) on the
session's branch and push, and copy the GeoTIFFs and the cropped point cloud
(`writers.las`, as LAZ) to `$AGENT_OUTPUTS`, listed in the report with their `gs://`
paths; no pull request. Write
`report.md` in the format from [README.md](README.md) and end the session with the
check table, the scene used, and the slowest step.
