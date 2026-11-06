#!/bin/bash
# The lock (specs/gcp.md, "The lock"): images/cloud/<type>/versions.lock, what
# agent-<type> is built from, every dependency resolved to an exact version. This
# script is its one reader and writer; nothing else parses a lock.
#
#   lock.sh resolve <type>        print a candidate lock, resolved from upstream
#                                 now (Linux only: Docker, Homebrew, curl, jq)
#   lock.sh bake-args <type>      print the lock as `docker buildx bake --set`
#                                 values, one per line (the bake-action's `set`)
#   lock.sh diff <old> <new>      print what moved, one line each, nothing if equal
#   lock.sh summary <old> <new>   one line for a title: `claude 2.1.318, go
#                                 1.27.4, apt 3 packages`
#
# A lock is `<kind> <name> <value>` lines, sorted, under a comment header:
#
#   base - <image ref>@sha256:...    the type's base image, by digest
#   apt-snapshot - <id>              the Ubuntu snapshot the build's apt reads
#   apt <package> <version>          what apt resolves the type's packages to
#   apt-floating <package> <version> one from a source with no snapshot
#                                    (NVIDIA's CUDA repo): recorded, not pinned
#   claude - <version>               Claude Code
#   rust - <version>                 the Rust toolchain, if the type has rust
#   terraform - <version>            Terraform, if the type has terraform
#   release <name> <tag>             a tool from a GitHub release (tflint,
#                                    virtualgl)
#   brew <formula> <version>         Homebrew, dependencies included: a record
#                                    and a build trigger, never a pin (owner,
#                                    2026-11-06)
#   pinned <what> <issue URL or ->   a pin where the dependency is declared
#                                    (gcp.md, "Pinning a dependency")
#
# Each value is resolved within the constraint declared where the dependency
# lives: BASE_IMAGE and CLAUDE_VERSION in docker-bake.hcl, packages.txt lines
# (name=version pins one), RUST_TOOLCHAIN, TERRAFORM_VERSION, TFLINT_VERSION and
# VIRTUALGL_VERSION in their toolsets' install.sh. The apt snapshot moves only
# when what apt resolves today differs from what the lock recorded.
#
#   LOCK_NOW       the snapshot id to use if apt moved (default: now, UTC)
#   LOCK_DIR       where the current locks are (default images/cloud)
# Run from the repo root.
set -euo pipefail
# A failure inside $(...) fails the script too: a lock resolved halfway must
# never be printed as if whole.
shopt -s inherit_errexit

# shellcheck source=scripts/cloud/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
AGENT_GCP_ENV_DIR=infra/gcp

readonly bake_file=images/cloud/docker-bake.hcl
readonly lock_dir="${LOCK_DIR:-images/cloud}"
readonly claude_releases=https://downloads.claude.ai/claude-code-releases
readonly rust_dist=https://static.rust-lang.org/dist
readonly terraform_index=https://releases.hashicorp.com/terraform/index.json

die() {
	printf 'lock: %s\n' "$*" >&2
	exit 1
}
usage() { die "usage: lock.sh resolve <type> | bake-args <type> | diff <old> <new> | summary <old> <new>"; }

lock_file() { printf '%s/%s/versions.lock' "$lock_dir" "$1"; }

# lock_value <file> <kind> [name]: the value of one line, or nothing.
lock_value() { awk -v kind="$2" -v name="${3:--}" '$1 == kind && $2 == name {print $3; exit}' "$1"; }

# --- Constraints, read where they're declared ---------------------------------

# declared <file> <NAME>: the default in `${NAME:-default}`, the form each
# toolset's install.sh declares its constraint in.
declared() {
	local value
	value="$(sed -n -E "s/.*\\\$\\{$2:-([^}]+)\\}.*/\\1/p" "$1" | head -n 1)"
	[[ -n "$value" ]] || die "no \${$2:-...} in $1"
	printf '%s' "$value"
}

# pin_note <file> <text>: the `# pinned: <URL>` note on the line declaring
# <text> or the line above it; - when there's none.
pin_note() {
	awk -v text="$2" '
		index($0, text) {
			if (match($0, /# pinned: [^ ]+/)) { print substr($0, RSTART + 10, RLENGTH - 10); found = 1; exit }
			if (match(previous, /# pinned: [^ ]+/)) { print substr(previous, RSTART + 10, RLENGTH - 10); found = 1; exit }
			exit
		}
		{ previous = $0 }
		END { if (!found) print "-" }' "$1"
}

# bake_print <type>: the type's build graph as bake resolves it, with nothing
# from the environment overriding the file's own defaults.
bake_print() {
	env -u CLAUDE_VERSION -u REGISTRY -u TAG -u COMMIT \
		docker buildx bake -f "$bake_file" --print "$1" 2>/dev/null
}

# --- Upstream ----------------------------------------------------------------

fetch() { curl -fsSL --retry 3 "$@"; }

# github_latest <owner/repo>: the tag GitHub redirects /releases/latest to (no
# API call, so no rate limit).
github_latest() {
	local url
	url="$(curl -fsSLI --retry 3 -o /dev/null -w '%{url_effective}' "https://github.com/$1/releases/latest")"
	[[ "$url" == */releases/tag/* ]] || die "no latest release for $1 ($url)"
	printf '%s' "${url##*/}"
}

image_digest() {
	docker buildx imagetools inspect "$1" --format '{{json .Manifest}}' | jq -er .digest
}

resolve_claude() {
	local constraint="$1" version
	if [[ "$constraint" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
		printf 'claude - %s\npinned claude %s\n' "$constraint" "$(pin_note "$bake_file" 'variable "CLAUDE_VERSION"')"
		return
	fi
	version="$(fetch "$claude_releases/$constraint")"
	[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "Claude Code's $constraint channel gave '$version'"
	printf 'claude - %s\n' "$version"
}

resolve_rust() {
	local file=images/toolsets/rust/install.sh constraint version
	constraint="$(declared "$file" RUST_TOOLCHAIN)"
	if [[ "$constraint" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
		printf 'rust - %s\npinned rust %s\n' "$constraint" "$(pin_note "$file" 'RUST_TOOLCHAIN:-')"
		return
	fi
	# stable, or a minor line (1.91): rustup's channel manifest names its version.
	version="$(fetch "$rust_dist/channel-rust-$constraint.toml" |
		awk '/^\[pkg\.rust\]/ {found = 1} found && /^version = / {gsub(/"/, "", $3); print $3; exit}')"
	[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "rust $constraint: no version in its channel manifest"
	[[ "$constraint" == stable ]] || printf 'pinned rust %s\n' "$(pin_note "$file" 'RUST_TOOLCHAIN:-')"
	printf 'rust - %s\n' "$version"
}

resolve_terraform() {
	local file=images/toolsets/terraform/install.sh constraint version line
	constraint="$(declared "$file" TERRAFORM_VERSION)"
	if [[ "$constraint" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
		printf 'terraform - %s\npinned terraform %s\n' "$constraint" "$(pin_note "$file" 'TERRAFORM_VERSION:-')"
	else
		# latest, or ~> x.y: the newest stable release on that line.
		line=""
		if [[ "$constraint" =~ ^~\>[[:space:]]*([0-9]+\.[0-9]+)$ ]]; then
			line="${BASH_REMATCH[1]}."
		elif [[ "$constraint" != latest ]]; then
			die "terraform: can't resolve '$constraint' (a version, ~> x.y or latest)"
		fi
		version="$(fetch "$terraform_index" | jq -r '.versions | keys[]' |
			grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' | grep -F "$line" | sort -V | tail -n 1)"
		[[ -n "$version" ]] || die "terraform: no release matches '$constraint'"
		[[ "$constraint" == latest ]] || printf 'pinned terraform %s\n' "$(pin_note "$file" 'TERRAFORM_VERSION:-')"
		printf 'terraform - %s\n' "$version"
	fi
	resolve_release tflint terraform-linters/tflint "$file" TFLINT_VERSION
}

# resolve_release <name> <owner/repo> <file> <NAME>: a tool from a GitHub release.
resolve_release() {
	local name="$1" repo="$2" file="$3" var="$4" constraint tag
	constraint="$(declared "$file" "$var")"
	if [[ "$constraint" == latest ]]; then
		# Assigned first: a failed substitution in printf's arguments wouldn't fail.
		tag="$(github_latest "$repo")"
		printf 'release %s %s\n' "$name" "$tag"
	else
		printf 'release %s %s\npinned %s %s\n' "$name" "$constraint" "$name" "$(pin_note "$file" "$var:-")"
	fi
}

resolve_base() {
	local constraint="$1" digest
	if [[ "$constraint" == *@sha256:* ]]; then
		printf 'base - %s\npinned base %s\n' "$constraint" "$(pin_note "$bake_file" "$constraint")"
		return
	fi
	digest="$(image_digest "$constraint")"
	printf 'base - %s@%s\n' "$constraint" "$digest"
}

# packages_of <type>: the apt packages the type's image asks for: the base's
# list, then each toolset's.
packages_of() {
	local toolset
	{
		cat images/cloud/base/packages.txt
		while read -r toolset; do
			[[ ! -f "images/toolsets/$toolset/packages.txt" ]] || cat "images/toolsets/$toolset/packages.txt"
		done < <(cloud_toolsets "$1")
	} | sed -e 's/#.*//' -e 's/[[:space:]]*$//' -e '/^$/d'
}

# resolve_apt <type> <base ref> <old lock or empty>: what apt resolves the type's
# packages to today, in a stock container at the locked base digest, and the
# snapshot that holds it: the old one if nothing moved, else now.
resolve_apt() {
	local type="$1" base="$2" old="$3" packages now resolved
	mapfile -t packages < <(packages_of "$type")
	now="${LOCK_NOW:-$(date -u +%Y%m%dT%H%M%SZ)}"
	# --entrypoint: CUDA's entrypoint prints a banner on stdout.
	resolved="$(docker run --rm --entrypoint /bin/bash -e PACKAGES="${packages[*]}" "$base" -c '
		set -euo pipefail
		apt-get update -q >/tmp/update.log 2>&1 || { cat /tmp/update.log >&2; exit 1; }
		# shellcheck disable=SC2086 # a list of names
		apt-get install --simulate --no-install-recommends $PACKAGES | sed -n "s/^Inst //p"')" ||
		die "apt couldn't resolve $type's packages in $base"
	# Inst <name> [<installed>] (<version> <origin>[, <origin>...] [<arch>])
	resolved="$(sed -E 's/^([^ ]+) (\[[^]]*\] )?\(([^ ]+) (.*) \[[^]]*\]\).*/\1 \3 \4/' <<<"$resolved" |
		awk '{kind = ($3 ~ /^Ubuntu:/) ? "apt" : "apt-floating"; print kind, $1, $2}' | LC_ALL=C sort)"
	[[ -n "$resolved" ]] || die "apt resolved nothing for $type"
	if [[ -n "$old" ]] && [[ "$(lock_value "$old" base)" == "$base" ]] &&
		[[ "$(grep -E '^apt(-floating)? ' "$old" | LC_ALL=C sort)" == "$resolved" ]]; then
		printf 'apt-snapshot - %s\n' "$(lock_value "$old" apt-snapshot)"
	else
		printf 'apt-snapshot - %s\n' "$now"
	fi
	printf '%s\n' "$resolved"
	# A name=version line pins that package.
	packages_of "$type" | sed -n -E 's/^([a-z0-9.+-]+)=.*/pinned apt\/\1 -/p'
}

# resolve_brew <type>: every formula the type's Brewfiles install, dependencies
# included, at the version Homebrew would install now. Homebrew resolves a
# formula's dependencies for the OS it runs on, hence Linux only.
resolve_brew() {
	local toolset formulae=() closure
	while read -r toolset; do
		[[ -f "images/toolsets/$toolset/Brewfile" ]] || continue
		mapfile -t -O "${#formulae[@]}" formulae < <(brew bundle list --formula --file="images/toolsets/$toolset/Brewfile")
	done < <(cloud_toolsets "$1")
	((${#formulae[@]} > 0)) || return 0
	closure="$({
		printf '%s\n' "${formulae[@]}"
		brew deps --union --full-name "${formulae[@]}"
	} | LC_ALL=C sort -u)"
	# shellcheck disable=SC2086 # one formula per word
	brew info --json=v2 --formula $closure |
		jq -er '.formulae[] | "brew \(.full_name) \(.versions.stable)\(if .revision > 0 then "_\(.revision)" else "" end)"'
}

# Taps the Brewfiles name, trusted the way install-toolset.sh trusts them, and
# Homebrew's formula data brought up to date.
prepare_brew() {
	local tap
	export HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_ANALYTICS=1 HOMEBREW_NO_ENV_HINTS=1
	brew update --quiet >&2 2>&1
	sed -n 's/^tap "\([^"]*\)".*/\1/p' images/toolsets/*/Brewfile | sort -u | while read -r tap; do
		brew tap "$tap" >&2 2>&1
		brew trust --tap "$tap" >&2
	done
}

cmd_resolve() {
	local type="$1" graph base base_lines claude old toolsets body
	[[ "$(uname -s)" == Linux ]] || die "resolve needs Linux: Homebrew resolves dependencies for the OS it runs on"
	[[ -f "infra/gcp/$type.env" && "$(gcp_backend "$type")" == run ]] || die "$type isn't a Cloud Run type in infra/gcp"
	graph="$(bake_print "$type")" || die "docker buildx bake --print $type failed"
	base="$(jq -er '[.target[] | select(.dockerfile == "images/cloud/base/Dockerfile") | .args.BASE_IMAGE][0] // empty' <<<"$graph")" ||
		die "$type's build graph has no base with a BASE_IMAGE"
	claude="$(jq -er --arg type "$type" '.target[$type].args.CLAUDE_VERSION // empty' <<<"$graph")" ||
		die "$type's build graph has no CLAUDE_VERSION"
	old="$(lock_file "$type")"
	[[ -f "$old" ]] || old=""
	mapfile -t toolsets < <(cloud_toolsets "$type")
	prepare_brew

	base_lines="$(resolve_base "$base")"
	# Functions run as plain commands, never after || or in a test, where bash
	# switches errexit off inside them.
	body="$(
		printf '%s\n' "$base_lines"
		resolve_claude "$claude"
		resolve_apt "$type" "$(awk '$1 == "base" {print $3}' <<<"$base_lines")" "$old"
		if [[ " ${toolsets[*]} " == *" rust "* ]]; then resolve_rust; fi
		if [[ " ${toolsets[*]} " == *" terraform "* ]]; then resolve_terraform; fi
		if [[ " ${toolsets[*]} " == *" gpu "* ]]; then
			resolve_release virtualgl VirtualGL/virtualgl images/toolsets/gpu/install.sh VIRTUALGL_VERSION
		fi
		resolve_brew "$type"
	)"
	printf '# %s: what agent-%s is built from (specs/gcp.md, "The lock").\n' "$(lock_file "$type")" "$type"
	printf '# Written by scripts/cloud/lock.sh resolve; edit the constraint where it is\n'
	printf '# declared, never this file.\n'
	LC_ALL=C sort -u <<<"$body"
}

# --- Reading a lock ----------------------------------------------------------

# The build arguments a lock sets, by line kind. `*` reaches every target in the
# type's graph; each argument is read by the one Dockerfile or install.sh that
# declares it.
cmd_bake_args() {
	local file kind name value
	file="$(lock_file "$1")"
	[[ -f "$file" ]] || die "no lock for $1 ($file); make cloud-lock TYPE=$1 WRITE=1"
	for kind in base apt-snapshot claude; do
		[[ -n "$(lock_value "$file" "$kind")" ]] || die "$file has no $kind line"
	done
	while read -r kind name value; do
		case "$kind" in
		base) printf '*.args.BASE_IMAGE=%s\n' "$value" ;;
		apt-snapshot) printf '*.args.APT_SNAPSHOT=%s\n' "$value" ;;
		claude) printf '*.args.CLAUDE_VERSION=%s\n' "$value" ;;
		rust) printf '*.args.RUST_TOOLCHAIN=%s\n' "$value" ;;
		terraform) printf '*.args.TERRAFORM_VERSION=%s\n' "$value" ;;
		release) printf '*.args.%s_VERSION=%s\n' "$(tr '[:lower:]-' '[:upper:]_' <<<"$name")" "$value" ;;
		apt | apt-floating | brew | pinned | \#*) ;;
		*) die "$file: unknown line kind '$kind'" ;;
		esac
	done < <(grep -v '^#' "$file")
}

# entries <file>: `<kind> <name> <value>` without the header.
entries() { grep -v '^#' "$1" | LC_ALL=C sort; }

cmd_diff() {
	join -a 1 -a 2 -e '(none)' -o '0,1.2,2.2' \
		<(entries "$1" | awk '{print $1 "/" $2, $3}' | LC_ALL=C sort) \
		<(entries "$2" | awk '{print $1 "/" $2, $3}' | LC_ALL=C sort) |
		awk '$2 != $3 {print $1 ": " $2 " -> " $3}'
}

cmd_summary() {
	# diff lines: <kind>/<name>: <old> -> <new>. A name can hold a slash
	# (lolay/tap/nowline), so split on the first one only.
	cmd_diff "$1" "$2" | awk '
		{
			key = substr($1, 1, length($1) - 1)
			kind = substr(key, 1, index(key, "/") - 1)
			name = substr(key, index(key, "/") + 1)
		}
		kind == "apt" || kind == "apt-floating" { apt++; next }
		kind == "apt-snapshot" || kind == "pinned" { next }
		{
			if (name == "-") name = kind
			sub(/.*\//, "", name)
			# Most telling first: Claude, the base, the toolchains, then Homebrew.
			rank = (kind == "claude") ? 1 : (kind == "base") ? 2 : (kind == "brew") ? 4 : 3
			part[rank] = part[rank] (part[rank] == "" ? "" : ", ") (($NF == "(none)") ? name " removed" : name " " $NF)
		}
		END {
			out = ""
			for (rank = 1; rank <= 4; rank++) if (part[rank] != "") out = out (out == "" ? "" : ", ") part[rank]
			if (apt) out = out (out == "" ? "" : ", ") "apt " apt " package" (apt == 1 ? "" : "s")
			print out
		}'
}

case "${1:-}" in
resolve)
	(($# == 2)) || usage
	cmd_resolve "$2"
	;;
bake-args)
	(($# == 2)) || usage
	cmd_bake_args "$2"
	;;
diff)
	(($# == 3)) || usage
	cmd_diff "$2" "$3"
	;;
summary)
	(($# == 3)) || usage
	cmd_summary "$2" "$3"
	;;
*) usage ;;
esac
