import json, time
from pystac_client import Client
t0=time.time()
c = Client.open("https://earth-search.aws.element84.com/v1")
bbox=[-120.15,36.65,-120.05,36.75]
s = c.search(collections=["sentinel-2-l2a"], bbox=bbox, datetime="2025-06-01/2025-08-31",
             query={"eo:cloud_cover":{"lt":100}}, max_items=200)
items=list(s.items())
print("n items", len(items), "search s", round(time.time()-t0,2))
rows=[]
for it in items:
    rows.append((it.properties.get("eo:cloud_cover"), it.id, it.properties.get("proj:code") or it.properties.get("proj:epsg"), it.datetime.isoformat(), it.properties.get("s2:mgrs_tile")))
rows.sort(key=lambda r: r[0])
for r in rows[:8]: print(r)
best=items[[i.id for i in items].index(rows[0][1])]
print("assets:", sorted(best.assets.keys()))
for k in ["red","nir","scl"]:
    a=best.assets.get(k)
    print(k, a.href if a else None, a.extra_fields.get("proj:shape") if a else None)
json.dump(best.to_dict(), open("best_item.json","w"))
