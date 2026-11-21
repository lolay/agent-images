# shellcheck shell=sh
# toolset-profile.sh: a session's PATH and the environment its image's toolsets
# recorded (install-toolset.sh), the one assembly both Linux images' profiles
# source after their own settings (images/cloud/runner/profile.sh,
# images/android/files/profile.sh). Installed as
# /etc/agent-images/toolset-profile.sh. Safe to source any number of times.
#
# The front of PATH is ~/bin, ~/.local/bin, the toolsets' PATH entries (<name>.path),
# then Homebrew ahead of the system, as `brew shellenv` puts it, so python3 and the
# session tools are Homebrew's rather than Ubuntu's. Then the toolsets' profile
# fragments (<name>.profile.sh), which can add to the back of PATH (the rust and go
# toolsets' install bins), and Claude Code's gh shim last, in front of everything.
#
#   HOMEBREW_PREFIX       brew shellenv's name for the prefix (default
#                         /home/linuxbrew/.linuxbrew); the tests point it at a fake one
#   AGENT_TOOLSETS_DIR    the record (default /etc/agent-images/toolsets)
#   AGENT_GH_SHIM_PROFILE the gh shim's helper (default /etc/agent-images/profile-gh-shim.sh)
HOMEBREW_PREFIX="${HOMEBREW_PREFIX:-/home/linuxbrew/.linuxbrew}"
export HOMEBREW_PREFIX
export AGENT_TOOLSETS_DIR="${AGENT_TOOLSETS_DIR:-/etc/agent-images/toolsets}"
# PATH is set up once per environment, and then it's the caller's: a test that
# puts a fake gh or curl first and runs a bash script must reach the fake, and on
# the cloud images that bash re-sources this through BASH_ENV.
# AGENT_PROFILE_PATH_DONE, exported at the end, marks it. Login and interactive
# shells set it up again (a fresh shell gets the image's PATH and the gh shim in
# front), and so does a caller that unsets the marker first: the session-profile
# hook does, since Claude Code's shell snapshot puts back a PATH without the shim.
agent_set_path=
[ -n "${AGENT_PROFILE_PATH_DONE:-}" ] || agent_set_path=1
case "$-" in *i*) agent_set_path=1 ;; esac
case "$0" in -*) agent_set_path=1 ;; esac
# shellcheck disable=SC3044 # bash only, and guarded
if [ -n "${BASH_VERSION:-}" ] && shopt -q login_shell 2>/dev/null; then agent_set_path=1; fi
if [ -n "$agent_set_path" ]; then
	# The toolsets' PATH entries go after ~/bin and before Homebrew: the gis
	# toolset's Python ahead of any Homebrew one, rustup's proxies ahead of a
	# Homebrew rust. One directory per line, # starts a comment.
	agent_toolset_path=""
	for agent_file in "$AGENT_TOOLSETS_DIR"/*.path; do
		[ -f "$agent_file" ] || continue
		while IFS= read -r agent_dir || [ -n "$agent_dir" ]; do
			case "$agent_dir" in "" | \#*) continue ;; esac
			agent_toolset_path="${agent_toolset_path:+$agent_toolset_path:}$agent_dir"
		done <"$agent_file"
	done
	# The front of PATH is always these, in this order, whatever PATH the shell
	# inherited (a session's can lack them, or have them behind /usr/bin); duplicates
	# go. A PATH that already starts with them is left alone.
	agent_path_front="$HOME/bin:$HOME/.local/bin${agent_toolset_path:+:$agent_toolset_path}:$HOMEBREW_PREFIX/bin:$HOMEBREW_PREFIX/sbin"
	case "$PATH" in
	"$agent_path_front" | "$agent_path_front":*) ;;
	*)
		PATH="$agent_path_front:$(printf '%s\n' "$PATH" | awk -v front="$agent_path_front" '
			BEGIN { RS = ":"; n = split(front, dirs, ":"); for (i = 1; i <= n; i++) skip[dirs[i]] = 1 }
			{ sub(/\n$/, "") }
			$0 != "" && !($0 in skip) && !seen[$0]++ { out = out (out == "" ? "" : ":") $0 }
			END { printf "%s", out }')"
		;;
	esac
	unset agent_path_front agent_toolset_path agent_file agent_dir
	export PATH
fi
# Every time, so each shell has the toolsets' variables (DISPLAY, JAVA_HOME,
# ANDROID_HOME, the gis environment's).
for agent_file in "$AGENT_TOOLSETS_DIR"/*.profile.sh; do
	# shellcheck source=/dev/null
	[ -f "$agent_file" ] && . "$agent_file"
done
unset agent_file
# Last, so nothing above moves it: Claude Code's gh shim goes in front of
# everything (images/shared/profile-gh-shim.sh).
if [ -n "$agent_set_path" ]; then
	agent_file="${AGENT_GH_SHIM_PROFILE:-/etc/agent-images/profile-gh-shim.sh}"
	# shellcheck source=/dev/null
	[ -f "$agent_file" ] && . "$agent_file"
	unset agent_file
fi
export AGENT_PROFILE_PATH_DONE=1
unset agent_set_path
