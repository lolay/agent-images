# shellcheck shell=sh
# The go toolset (images/toolsets/go): what `go install` installs (gomobile, on
# the Android image) goes to GOBIN, or without it to the bin of GOPATH's first
# entry, ~/go by default, at the back of PATH. Only from the environment: what
# `go env -w` wrote isn't read, since `go env` would be a subprocess in every
# shell. Only while PATH is being set up (agent_set_path, toolset-profile.sh).
# shellcheck disable=SC2154 # toolset-profile.sh's
if [ -n "${agent_set_path:-}" ]; then
	agent_go_bin="${GOPATH:-$HOME/go}"
	agent_go_bin="${GOBIN:-${agent_go_bin%%:*}/bin}"
	case ":$PATH:" in
	*":$agent_go_bin:"*) ;;
	*) PATH="$PATH:$agent_go_bin" ;;
	esac
	unset agent_go_bin
fi
