# gis runner test, 20261106T002507Z

Session: cse_01BNKwNbez31ogmEHb1veHvj. Scene: S2B_10SGF_20250709_0_L2A (Sentinel-2 L2A, UTM 10N, baseline 05.11), the least cloudy Jun–Aug 2025 scene over the box. LiDAR: CA_SanJoaquin_3_2021 (EPT), 500 m square at UTM 10N 763090–763590 E, 4062281–4062781 N, all orchard.

| Check | Result | Value |
| --- | --- | --- |
| 1. Facts recorded (OS, CPU, memory, disk, user, Claude version) | pass | Ubuntu 24.04.5, 9 CPU, 31 GiB RAM, user `ubuntu` |
| 1. Claude Code vs published latest | pass | 2.1.318 and 2.1.318 |
| 1. `sudo -n true` | fails as expected | no-new-privileges set |
| 1. Credential-like env var names | pass | only exempt names: `CLAUDE_*`, `CCR_*`, `GIT_CONFIG_KEY_0..2`/`VALUE_*`/`COUNT` |
| 1. `$DISPLAY` | unset | desktop QGIS and display screenshots skipped |
| 1. Session notice, time line | pass | ends 08:24 UTC (earlier of 8 h session limit and `AGENT_RETIRE_AT` 09:39:39 UTC); idle limit 60 min |
| 1. Session notice, cost line | skipped | `AGENT_COST_USD_PER_HOUR` and `AGENT_COST_DETAIL` not set, no cost line shown |
| 1. GIS tools and bindings | pass | GDAL 3.13.3, PDAL 2.10.2 (pdal py 3.5.5), QGIS 4.2.3, PROJ 9.9.0; osgeo.gdal, rasterio 1.5.2, geopandas 1.2.0, pdal, qgis.core all import |
| 2. STAC search, least cloudy scene | pass | 44 items, best S2B_10SGF_20250709_0_L2A (eo:cloud_cover 0.0006) |
| 2. NDVI valid values in [-1, 1] | pass | min -0.854, max 0.9996 over 634,174 valid pixels |
| 2. Mean NDVI over cropland > 0.3 | pass | 0.520 over field polygons (205,711 px); 0.481 over all valid box pixels |
| 2. Bytes over network vs full band files | pass (measure) | 10.9 MB read (7 GETs) vs 436.8 MB for red+nir+SCL full files (2.5%) |
| 2. `ndvi.tif` valid COG with overviews | pass | `rio cogeo validate` ok, 3 overviews, EPSG:32610 |
| 3. Field polygons from Overpass | pass | 237 features (83 farmland, 154 orchard), 4,660 ha clipped to box; no 500 m grid fallback needed |
| 3. Zonal NDVI stats | pass | 237 of 237 fields with valid pixels |
| 4. Project covers the box | pass | San Joaquin 2021 covers 100%; FEMAR9Fresno 2019 covers 98.4% |
| 4. CHM 99th percentile plausible (2 to 15 m) | pass | p99 3.78 m (p50 3.31 m, p90 3.59 m) |
| 4. Tree count non-zero | pass | 6,071 tree tops above 2 m, 2 m-radius window; median height 3.43 m |
| 5. QGIS zonal stats agree with mine | pass | mean, min, max, stdev over 237 fields agree within 5e-5 (my CSV is rounded to 4 dp) |
| 5. PyQGIS offscreen map | pass | `map.png` 1400×1400, rendered |
| 5. QGIS desktop screenshot | skipped | `$DISPLAY` unset |
| 6. Results committed and pushed | pending | see git push below |
| 6. GeoTIFFs and LAZ to `$AGENT_OUTPUTS` | pass | checksums match, see below |

## Timings

| Step | Wall time |
| --- | --- |
| LiDAR: EPT read of square, SMRF, cropped LAZ write (`pdal pipeline`, max RSS 978 MB) | 40.7 s (slowest) |
| Overpass query (overpass-api.de, 205 KB response) | 10.7 s |
| DTM 1 m (ground, IDW) | 7.7 s |
| QGIS `native:zonalstatisticsfb`, 237 fields | 6.2 s |
| DSM 1 m (first returns, max) | 5.1 s |
| NDVI windowed read, 3 bands over HTTP | 4.0 s |
| STAC search | 2.0 s |
| PyQGIS map render | 0.9 s |

Not separately timed: NDVI write and COG build, fields and zonal script, CHM and tree detection, hillshade, QGIS init.

## Outputs

`$AGENT_OUTPUTS` = `gs://lolay-agents-outputs/gis/2026-11-06/cse_01BNKwNbez31ogmEHb1veHvj/`

- `gs://lolay-agents-outputs/gis/2026-11-06/cse_01BNKwNbez31ogmEHb1veHvj/ndvi.tif` (3.2 MB)
- `gs://lolay-agents-outputs/gis/2026-11-06/cse_01BNKwNbez31ogmEHb1veHvj/dsm_1m.tif` (0.25 MB)
- `gs://lolay-agents-outputs/gis/2026-11-06/cse_01BNKwNbez31ogmEHb1veHvj/dtm_1m.tif` (0.69 MB)
- `gs://lolay-agents-outputs/gis/2026-11-06/cse_01BNKwNbez31ogmEHb1veHvj/chm.tif` (0.74 MB)
- `gs://lolay-agents-outputs/gis/2026-11-06/cse_01BNKwNbez31ogmEHb1veHvj/hillshade.tif` (0.25 MB)
- `gs://lolay-agents-outputs/gis/2026-11-06/cse_01BNKwNbez31ogmEHb1veHvj/cropped_classified.laz` (34.9 MB, 8,968,188 points, ground classified)

All six were copied and checked against their scratch originals with md5.

## Missing from the image

Nothing had to be installed. Everything the test named was present. The desktop QGIS check could not run because the session has no `$DISPLAY`, which is a session setting, not a missing tool.

## Notes

- **Finding, NDVI masking.** 39.8% of 10 m red pixels in the box have DN ≤ 1000, which is zero or negative reflectance after the L2A offset. NIR is fine (99.8% above 1000). The SCL band calls these pixels vegetation or bare soil, with no cloud or shadow in the box. The same share shows up in the two other top scenes over tile 10SGF (38% and 41%), so it is not one bad scene. These pixels are excluded, and they are probably the high-NDVI end, so the cropland mean is probably biased low. Not verified. This is the first thing to look at before trusting the NDVI numbers.
- **Finding, DSM and CHM.** The DSM max is 139.7 m and the CHM max is 76.3 m, so there is noise in the upper tail. The p99 is robust to it. Mean CHM is 3.14 m and 96% of cells are above 2 m, which fits a dense young orchard but was not checked against an independent canopy product.
- **Finding, grid size.** `writers.gdal` with `bounds` gives 501 × 501 cells, not 500 × 500, because the upper bound is inclusive.
- **Finding, map.** At full-scene extent the 6,071 tree points render as a solid navy block over the LiDAR square. The tree layer needs a zoomed inset to read.
- **Overpass.** `overpass-api.de` returned 406 to a raw `--data-binary` POST and answered to `--data-urlencode`. `overpass.kumi.systems` did not answer within 120 s, so I stopped that request and did not use it.
- **Byte counts.** GDAL's network statistics are off by default. `CPL_VSIL_NETWORK_STATS_ENABLED=YES` is needed to see the counters.
- **My own mistakes, fixed.** The first NDVI run failed its range assert because I had allowed non-positive reflectance into the ratio. The `qgis_process` zonal call failed twice on parameter names. Both are fixed in the scripts under `scripts/`.
- **Scope.** No pull request. Scripts are committed under `scripts/` so the run can be reproduced. The GeoPackages are committed beside the GeoJSON.
