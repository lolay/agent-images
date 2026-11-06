import json, time, subprocess, os
os.environ["CPL_VSIL_NETWORK_STATS_ENABLED"]="YES"
import numpy as np, rasterio
from rasterio.windows import from_bounds
from rasterio.enums import Resampling
from osgeo import gdal
from pyproj import Transformer
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

R = os.environ["RRES"]; OUT = "/scratch/runner-test/out"
item = json.load(open("best_item.json"))
bbox = [-120.15, 36.65, -120.05, 36.75]
tr = Transformer.from_crs("EPSG:4326", "EPSG:32610", always_xy=True)
xs, ys = tr.transform([bbox[0], bbox[2], bbox[2], bbox[0]], [bbox[1], bbox[1], bbox[3], bbox[3]])
utm_bounds = (min(xs), min(ys), max(xs), max(ys))
print("utm bounds", utm_bounds)

def url(k): return "/vsicurl/" + item["assets"][k]["href"]
def size(k): return gdal.VSIStatL(url(k)).size

full_sizes = {k: size(k) for k in ["red", "nir", "scl"]}
print("full band sizes (bytes)", full_sizes)

gdal.NetworkStatsReset()
t0 = time.time()
with rasterio.open(url("red")) as src:
    win = from_bounds(*utm_bounds, transform=src.transform).round_offsets().round_lengths()
    red = src.read(1, window=win).astype("float32")
    red_tf = src.window_transform(win)
    scale_r = item["assets"]["red"]["raster:bands"][0]
with rasterio.open(url("nir")) as src:
    nir = src.read(1, window=win).astype("float32")
with rasterio.open(url("scl")) as src:
    wscl = from_bounds(*utm_bounds, transform=src.transform).round_offsets().round_lengths()
    scl = src.read(1, window=wscl, out_shape=red.shape, resampling=Resampling.nearest)
t_read = time.time() - t0
stats = json.loads(gdal.NetworkStatsGetAsSerializedJSON())
print("read seconds", round(t_read, 2), "network stats", json.dumps(stats)[:600])

red = red * scale_r["scale"] + scale_r["offset"]
nir = nir * scale_r["scale"] + scale_r["offset"]
bad = np.isin(scl, [0, 1, 3, 8, 9, 10])  # no data, saturated, shadow, cloud medium/high, cirrus
valid = (~bad) & (red > 0) & (nir > 0)  # non-positive reflectance is not usable for a ratio
ndvi = np.full(red.shape, -9999.0, dtype="float32")
ndvi[valid] = ((nir - red) / (nir + red))[valid]
v = ndvi[valid]
print("shape", ndvi.shape, "valid", int(valid.sum()), "of", ndvi.size, "min", v.min(), "max", v.max(), "mean", v.mean())
assert v.min() >= -1 and v.max() <= 1, "NDVI out of range"

tif = f"{OUT}/ndvi_raw.tif"
prof = dict(driver="GTiff", height=ndvi.shape[0], width=ndvi.shape[1], count=1, dtype="float32",
            crs="EPSG:32610", transform=red_tf, nodata=-9999.0)
with rasterio.open(tif, "w", **prof) as dst:
    dst.write(ndvi, 1)
subprocess.run(["gdal_translate", "-q", "-of", "COG", "-co", "COMPRESS=DEFLATE", "-co", "PREDICTOR=3",
                "-co", "BLOCKSIZE=256", "-co", "OVERVIEWS=AUTO", "-co", "RESAMPLING=AVERAGE",
                tif, f"{OUT}/ndvi.tif"], check=True)
os.remove(tif)
# verify COG
info = subprocess.run(["gdalinfo", "-json", f"{OUT}/ndvi.tif"], capture_output=True, text=True).stdout
ij = json.loads(info)
print("COG overviews:", len(ij["bands"][0].get("overviews", [])), "crs", ij["coordinateSystem"]["wkt"][:40])
val = subprocess.run(["rio", "cogeo", "validate", f"{OUT}/ndvi.tif"], capture_output=True, text=True)
print("rio cogeo validate:", val.stdout.strip() or val.stderr.strip()[:200])

# quicklook
cmap = plt.get_cmap("RdYlGn").copy(); cmap.set_bad((0.9, 0.9, 0.9, 1))
masked = np.ma.masked_where(ndvi <= -9998, ndvi)
fig, ax = plt.subplots(figsize=(8, 9), dpi=110)
im = ax.imshow(masked, cmap=cmap, vmin=-0.2, vmax=0.9)
ax.set_title("NDVI, Sentinel-2 S2B_10SGF_20250709 (cloud/shadow masked)")
ax.set_axis_off(); fig.colorbar(im, ax=ax, shrink=0.7, label="NDVI")
fig.savefig(f"{R}/ndvi.png", bbox_inches="tight"); plt.close(fig)

np.save(f"{OUT}/ndvi_valid.npy", valid)
json.dump({"utm_bounds": utm_bounds, "full_band_bytes": full_sizes, "network": stats,
           "read_seconds": t_read, "valid_pixels": int(valid.sum()), "total_pixels": int(ndvi.size),
           "ndvi_min": float(v.min()), "ndvi_max": float(v.max()), "ndvi_mean_box": float(v.mean())},
          open(f"{OUT}/ndvi_stats.json", "w"), indent=1)
