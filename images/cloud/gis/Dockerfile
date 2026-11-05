# agent-gis's toolsets (specs/gcp.md, "Runner types"; specs/image-toolsets.md,
# "gis"): GDAL, PDAL and QGIS from conda-forge, on agent-base's core. No Go,
# Rust, Terraform, JDK or Chromium libraries: a GIS session that needs one
# installs it. images/cloud/runner then adds the runner files and Claude Code.
# Build it with images/cloud/docker-bake.hcl, which supplies agent-base.
FROM agent-base

ARG APT_DROP_LISTS=true
RUN --mount=type=bind,source=images/toolsets/install-toolset.sh,target=/tmp/toolsets/install-toolset.sh \
	--mount=type=bind,source=images/toolsets/native,target=/tmp/toolsets/native \
	bash /tmp/toolsets/install-toolset.sh native
RUN --mount=type=bind,source=images/toolsets/install-toolset.sh,target=/tmp/toolsets/install-toolset.sh \
	--mount=type=bind,source=images/toolsets/node,target=/tmp/toolsets/node \
	bash /tmp/toolsets/install-toolset.sh node
RUN --mount=type=bind,source=images/toolsets/install-toolset.sh,target=/tmp/toolsets/install-toolset.sh \
	--mount=type=bind,source=images/toolsets/gis,target=/tmp/toolsets/gis \
	bash /tmp/toolsets/install-toolset.sh gis
