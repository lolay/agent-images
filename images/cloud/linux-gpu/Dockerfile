# agent-linux-gpu's toolsets (specs/gcp.md, "Runner types"; specs/image-toolsets.md):
# linux plus the GPU. agent-linux here is images/cloud/linux's toolsets built on
# base-gpu (CUDA's devel image) rather than on agent-base, so linux-gpu has
# everything linux has and can't drift from it; this adds only the gpu toolset,
# last, since it changes most while GPU rendering on Cloud Run is being worked
# out. images/cloud/runner then adds the runner files and Claude Code. Build it
# with images/cloud/docker-bake.hcl, which supplies agent-linux.
FROM agent-linux

ARG APT_DROP_LISTS=true
RUN --mount=type=bind,source=images/toolsets/install-toolset.sh,target=/tmp/toolsets/install-toolset.sh \
	--mount=type=bind,source=images/toolsets/gpu,target=/tmp/toolsets/gpu \
	bash /tmp/toolsets/install-toolset.sh gpu
