import os, json, numpy as np, rasterio, geopandas as gpd
from scipy import ndimage
from shapely.geometry import Point
R = os.environ["RRES"]
with rasterio.open("dsm_1m.tif") as d, rasterio.open("dtm_1m.tif") as t:
    dsm = d.read(1); dtm = t.read(1); prof = d.profile; tf = d.transform
    nd = -9999
    ok = (dsm != nd) & (dtm != nd)
chm = np.where(ok, dsm - dtm, nd).astype("float32")
chm[ok & (chm < 0)] = 0.0  # DSM below DTM is noise, not negative height
v = chm[ok]
p = {q: float(np.percentile(v, q)) for q in [50, 90, 95, 99, 99.9]}
print("CHM stats: max", float(v.max()), "mean", float(v.mean()), "pct", {k: round(x, 2) for k, x in p.items()})
print("fraction of cells > 2 m:", round(float((v > 2).mean()), 3))
prof.update(dtype="float32", nodata=nd, compress="deflate", tiled=True)
with rasterio.open("chm.tif", "w", **prof) as dst:
    dst.write(np.where(ok, chm, nd).astype("float32"), 1)
# tree tops: local maxima in a 5x5 m window (2 m radius) above 2 m
sm = ndimage.gaussian_filter(np.where(ok, chm, 0), sigma=0.5)
mx = ndimage.maximum_filter(sm, size=5)
peaks = (sm == mx) & (sm > 2.0) & ok
rows, cols = np.nonzero(peaks)
xs, ys = rasterio.transform.xy(tf, rows, cols, offset="center")
xs = np.asarray(xs); ys = np.asarray(ys)
heights = chm[rows, cols]
gdf = gpd.GeoDataFrame({"height_m": heights.round(2).astype(float)},
                       geometry=[Point(x, y) for x, y in zip(xs, ys)], crs="EPSG:32610")
print("tree tops", len(gdf), "height median", round(float(np.median(heights)), 2) if len(gdf) else None)
gdf.to_crs(4326).to_file(f"{R}/trees.geojson", driver="GeoJSON")
print("geom types", gdf.geom_type.unique().tolist())
json.dump({"chm_p": p, "chm_max": float(v.max()), "tree_count": int(len(gdf)),
           "cells": int(v.size)}, open("chm_stats.json", "w"), indent=1)
