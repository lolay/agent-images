import os, time
from qgis.core import (QgsApplication, QgsProject, QgsRasterLayer, QgsVectorLayer, QgsMapSettings,
    QgsMapRendererSequentialJob, QgsRectangle, QgsColorRampShader, QgsSingleBandPseudoColorRenderer,
    QgsStyle, QgsFillSymbol, QgsMarkerSymbol, QgsSingleSymbolRenderer, QgsCoordinateReferenceSystem)
from qgis.PyQt.QtGui import QColor
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
R = os.environ["RRES"]
app = QgsApplication([], False); app.setPrefixPath(os.environ.get("QGIS_PREFIX_PATH", ""), True); app.initQgis()
t0 = time.time()
crs = QgsCoordinateReferenceSystem("EPSG:32610")
ndvi = QgsRasterLayer("/scratch/runner-test/out/ndvi.tif", "NDVI", "gdal"); assert ndvi.isValid()
fields = QgsVectorLayer("/scratch/runner-test/out/fields_utm10n.gpkg", "fields", "ogr"); assert fields.isValid()
trees = QgsVectorLayer(f"{R}/trees.geojson", "tree tops", "ogr"); assert trees.isValid()

shader = QgsColorRampShader(-0.2, 0.9)
ramp = QgsStyle.defaultStyle().colorRamp("RdYlGn")
ramp.invert() if False else None
items = [QgsColorRampShader.ColorRampItem(v, c) for v, c in
         [(-0.2, QColor("#a50026")), (0.0, QColor("#f7f7a0")), (0.3, QColor("#a6d96a")), (0.6, QColor("#1a9850")), (0.9, QColor("#00441b"))]]
shader.setColorRampItemList(items); shader.setColorRampType(QgsColorRampShader.Interpolated)
rend = QgsSingleBandPseudoColorRenderer(ndvi.dataProvider(), 1)
rend.setShader(QgsRasterShader := __import__("qgis.core", fromlist=["QgsRasterShader"]).QgsRasterShader())
rend.shader().setRasterShaderFunction(shader); ndvi.setRenderer(rend)

fields.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple(
    {"color": "0,0,0,0", "outline_color": "#222222", "outline_width": "0.25"})))
trees.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple(
    {"name": "circle", "color": "#00e5ff", "outline_color": "#003344", "size": "0.9"})))

ext = fields.extent(); ext.grow(ext.width() * 0.02)
s = QgsMapSettings()
s.setLayers([trees, fields, ndvi])  # top first in the list is drawn last, so reversed below
s.setLayers([trees, fields, ndvi])
s.setDestinationCrs(crs); s.setExtent(ext); s.setOutputSize(__import__("qgis.PyQt.QtCore", fromlist=["QSize"]).QSize(1400, 1400))
s.setBackgroundColor(QColor("white"))
job = QgsMapRendererSequentialJob(s); job.start(); job.waitForFinished()
img = job.renderedImage(); ok = img.save(f"{R}/map.png", "PNG")
print("rendered", ok, img.width(), img.height(), "secs", round(time.time() - t0, 2))
print("layers valid:", ndvi.isValid(), fields.featureCount(), trees.featureCount())
app.exitQgis()
