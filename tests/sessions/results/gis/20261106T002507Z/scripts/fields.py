import json, os, time
import numpy as np, geopandas as gpd, rasterio
from rasterio.features import geometry_mask
from shapely.geometry import Polygon, MultiPolygon, box, LineString
from shapely.ops import linemerge, polygonize, unary_union
R = os.environ["RRES"]; OUT = "/scratch/runner-test/out"
bbox = box(-120.15, 36.65, -120.05, 36.75)
d = json.load(open("ovp.json"))
els = d["elements"]
print("elements", len(els), {t: sum(1 for e in els if e["type"] == t) for t in ["way", "relation"]})

def coords(geom): return [(p["lon"], p["lat"]) for p in geom]

def ring_from_segments(segs):
    merged = linemerge([LineString(coords(s)) for s in segs])
    lines = [merged] if merged.geom_type == "LineString" else list(merged.geoms)
    return [l for l in lines if l.is_ring or (l.coords[0] == l.coords[-1] and len(l.coords) >= 4)]

feats = []; skipped = {"open_way": 0, "relation_no_rings": 0}
for e in els:
    tags = e.get("tags", {})
    base = {"osm_id": f"{e['type']}/{e['id']}", "landuse": tags.get("landuse"), "name": tags.get("name")}
    if e["type"] == "way":
        g = e.get("geometry", [])
        if len(g) >= 4 and (g[0]["lat"], g[0]["lon"]) == (g[-1]["lat"], g[-1]["lon"]):
            feats.append({**base, "geometry": Polygon(coords(g))})
        else:
            skipped["open_way"] += 1
    else:
        if tags.get("type") != "multipolygon":
            continue
        outers = [m["geometry"] for m in e.get("members", []) if m.get("role") == "outer" and m.get("geometry")]
        inners = [m["geometry"] for m in e.get("members", []) if m.get("role") == "inner" and m.get("geometry")]
        orings = ring_from_segments(outers) if outers else []
        irings = ring_from_segments(inners) if inners else []
        if not orings:
            skipped["relation_no_rings"] += 1; continue
        polys = []
        for orr in orings:
            holes = [Polygon(r) for r in irings if Polygon(orr).contains(Polygon(r))]
            polys.append(Polygon(orr.coords, [h.exterior.coords for h in holes]))
        geom = polys[0] if len(polys) == 1 else MultiPolygon(polys)
        feats.append({**base, "geometry": geom})

gdf = gpd.GeoDataFrame(feats, geometry="geometry", crs="EPSG:4326")
gdf["geometry"] = gdf.geometry.buffer(0)
gdf = gdf.clip(bbox)
gdf = gdf[gdf.geom_type.isin(["Polygon", "MultiPolygon"]) & ~gdf.geometry.is_empty].reset_index(drop=True)
gdf = gdf[gdf.area > 0].reset_index(drop=True)
gdf["area_ha"] = gdf.to_crs(32610).area / 1e4
print("features kept", len(gdf), "types", gdf.geom_type.value_counts().to_dict(), "skipped", skipped)
print("by landuse", gdf.landuse.value_counts().to_dict(), "total ha", round(gdf.area_ha.sum(), 1))

gdf.to_file(f"{OUT}/fields_wgs84.gpkg", layer="fields", driver="GPKG")
gdf.to_crs(32610).to_file(f"{OUT}/fields_utm10n.gpkg", layer="fields", driver="GPKG")
gdf.to_file(f"{R}/fields_wgs84.geojson", driver="GeoJSON")
gdf.to_crs(32610).to_file(f"{R}/fields_utm10n.geojson", driver="GeoJSON")

# zonal stats on NDVI (cropland mask = union of field polygons)
with rasterio.open(f"{OUT}/ndvi.tif") as src:
    ndvi = src.read(1); tf = src.transform; nodata = src.nodata
    utm = gdf.to_crs(src.crs)
    rows = []
    for i, row in utm.iterrows():
        m = geometry_mask([row.geometry], out_shape=ndvi.shape, transform=tf, invert=True)
        vals = ndvi[m & (ndvi != nodata)]
        if vals.size == 0:
            rows.append({"fid": i, "osm_id": row.osm_id, "n_pixels": 0}); continue
        rows.append({"fid": i, "osm_id": row.osm_id, "landuse": row.landuse, "area_ha": round(row.area_ha, 2),
                     "n_pixels": int(vals.size), "ndvi_mean": float(vals.mean()), "ndvi_min": float(vals.min()),
                     "ndvi_max": float(vals.max()), "ndvi_std": float(vals.std(ddof=0))})
    crop_union = unary_union(list(utm.geometry))
    mu = geometry_mask([crop_union], out_shape=ndvi.shape, transform=tf, invert=True)
    cv = ndvi[mu & (ndvi != nodata)]
    print("cropland-mask pixels", int(cv.size), "mean NDVI", float(cv.mean()) if cv.size else None)
import pandas as pd
df = pd.DataFrame(rows)
df.to_csv(f"{R}/fields_ndvi.csv", index=False, float_format="%.4f")
ok = df[df.n_pixels > 0].sort_values("ndvi_mean")
print("fields with valid NDVI", len(ok), "of", len(df))
print("lowest 5:\n", ok.head(5)[["osm_id", "landuse", "area_ha", "n_pixels", "ndvi_mean"]].to_string(index=False))
print("highest 5:\n", ok.tail(5)[["osm_id", "landuse", "area_ha", "n_pixels", "ndvi_mean"]].iloc[::-1].to_string(index=False))
json.dump({"cropland_mean_ndvi": float(cv.mean()) if cv.size else None, "cropland_pixels": int(cv.size)},
          open(f"{OUT}/cropland_stats.json", "w"), indent=1)
