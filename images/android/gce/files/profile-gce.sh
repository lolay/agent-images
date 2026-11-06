# shellcheck shell=sh
# Installed as /etc/profile.d/agent-images-gce.sh by the Packer build, beside
# the Android image's agent-images.sh. On Compute Engine the session's scratch
# is a directory on the VM's boot disk, which goes with the VM; the path is the
# same as on the Cloud Run types, so sessions and the tests don't care where
# they run.
export AGENT_SCRATCH=/scratch
