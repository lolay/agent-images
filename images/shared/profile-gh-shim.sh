# shellcheck shell=sh
# Installed as /etc/agent-images/profile-gh-shim.sh and sourced last by each
# Linux image's profile (images/cloud/runner/profile.sh,
# images/android/files/profile.sh). Safe to source any number of times.
#
# Claude Code's agent proxy (the runner's --use-anthropic-git-proxy) writes a gh
# shim into each session's config dir. The shim wraps Homebrew's gh and, when no
# token is set, sends it through the session's GitHub relay, so it has to come
# before Homebrew: behind it, the real gh runs signed out. Claude Code puts the
# shim first in each Bash command's PATH, but its shell snapshot then exports a
# PATH without it, and a profile that puts Homebrew first moves it behind; this
# puts it back in front, and drops any other copy of it.
if [ -n "${CLAUDE_CONFIG_DIR:-}" ] && [ -x "$CLAUDE_CONFIG_DIR/ccr/bin/gh" ]; then
	agent_gh_shim="$CLAUDE_CONFIG_DIR/ccr/bin"
	case "$PATH" in
	"$agent_gh_shim" | "$agent_gh_shim":*) ;;
	*)
		# Shell only, no awk or sed: PATH may not lead to them yet.
		agent_gh_left="$PATH:" agent_gh_path="$agent_gh_shim"
		while [ -n "$agent_gh_left" ]; do
			agent_gh_dir="${agent_gh_left%%:*}"
			agent_gh_left="${agent_gh_left#*:}"
			[ "$agent_gh_dir" = "$agent_gh_shim" ] || agent_gh_path="$agent_gh_path:$agent_gh_dir"
		done
		PATH="$agent_gh_path"
		;;
	esac
	export PATH
	unset agent_gh_shim agent_gh_left agent_gh_path agent_gh_dir
fi
