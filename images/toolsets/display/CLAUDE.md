- A virtual display (`$DISPLAY`, Xvfb at 1920x1080) with Mesa's software OpenGL:
  headed browsers and desktop apps run on it, without a GPU. There's no window
  manager: size a window before a screenshot (`xdotool search --name QGIS
  windowsize %@ 1920 1080`, or a browser's `--window-size`), then
  `import -window root display.png`.
