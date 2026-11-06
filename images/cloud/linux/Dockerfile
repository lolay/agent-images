# agent-linux's toolsets (specs/gcp.md, "Runner types"; specs/image-toolsets.md):
# everyday development, on agent-base's core. The Android image's list
# (images/android/toolsets.txt) less android: Cloud Run has no KVM. apt toolsets
# first: they move slowest. images/cloud/runner then adds the runner files and
# Claude Code. Build it with images/cloud/docker-bake.hcl, which supplies
# agent-base.
FROM agent-base

ARG APT_DROP_LISTS=true
RUN --mount=type=bind,source=images/toolsets/install-toolset.sh,target=/tmp/toolsets/install-toolset.sh \
	--mount=type=bind,source=images/toolsets/native,target=/tmp/toolsets/native \
	bash /tmp/toolsets/install-toolset.sh native
RUN --mount=type=bind,source=images/toolsets/install-toolset.sh,target=/tmp/toolsets/install-toolset.sh \
	--mount=type=bind,source=images/toolsets/jvm,target=/tmp/toolsets/jvm \
	bash /tmp/toolsets/install-toolset.sh jvm
RUN --mount=type=bind,source=images/toolsets/install-toolset.sh,target=/tmp/toolsets/install-toolset.sh \
	--mount=type=bind,source=images/toolsets/browser,target=/tmp/toolsets/browser \
	bash /tmp/toolsets/install-toolset.sh browser
RUN --mount=type=bind,source=images/toolsets/install-toolset.sh,target=/tmp/toolsets/install-toolset.sh \
	--mount=type=bind,source=images/toolsets/display,target=/tmp/toolsets/display \
	bash /tmp/toolsets/install-toolset.sh display
RUN --mount=type=bind,source=images/toolsets/install-toolset.sh,target=/tmp/toolsets/install-toolset.sh \
	--mount=type=bind,source=images/toolsets/node,target=/tmp/toolsets/node \
	bash /tmp/toolsets/install-toolset.sh node
RUN --mount=type=bind,source=images/toolsets/install-toolset.sh,target=/tmp/toolsets/install-toolset.sh \
	--mount=type=bind,source=images/toolsets/python,target=/tmp/toolsets/python \
	bash /tmp/toolsets/install-toolset.sh python
RUN --mount=type=bind,source=images/toolsets/install-toolset.sh,target=/tmp/toolsets/install-toolset.sh \
	--mount=type=bind,source=images/toolsets/go,target=/tmp/toolsets/go \
	bash /tmp/toolsets/install-toolset.sh go
RUN --mount=type=bind,source=images/toolsets/install-toolset.sh,target=/tmp/toolsets/install-toolset.sh \
	--mount=type=bind,source=images/toolsets/rust,target=/tmp/toolsets/rust \
	bash /tmp/toolsets/install-toolset.sh rust
RUN --mount=type=bind,source=images/toolsets/install-toolset.sh,target=/tmp/toolsets/install-toolset.sh \
	--mount=type=bind,source=images/toolsets/proto,target=/tmp/toolsets/proto \
	bash /tmp/toolsets/install-toolset.sh proto
RUN --mount=type=bind,source=images/toolsets/install-toolset.sh,target=/tmp/toolsets/install-toolset.sh \
	--mount=type=bind,source=images/toolsets/terraform,target=/tmp/toolsets/terraform \
	bash /tmp/toolsets/install-toolset.sh terraform
