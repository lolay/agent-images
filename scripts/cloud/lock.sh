#!/bin/bash
# The lock (specs/gcp.md, "The lock"): images/cloud/<type>/versions.lock, what
# agent-<type> is built from, every dependency resolved to an exact version. This
# script is its one reader and writer; nothing else parses a lock.
#
#   lock.sh resolve <type>        print a candidate lock, resolved from upstream
#                                 now (Linux only: Docker, Homebrew, curl, jq)
#   lock.sh bake-args <type>      print the lock as `docker buildx bake --set`
#                                 values, one per line (the bake-action's `set`),
#                                 and APT_PINS, name=version@id, from the
#                                 type's package lists
#                                 (images/cloud/base/apt-pins.sh)
#   lock.sh diff <old> <new>      print what moved, one line each, nothing if equal
#   lock.sh summary <old> <new>   one line for a title: `claude 2.1.318, go
#                                 1.27.4, apt 3 packages`
#   lock.sh pins <type>           each pin in the type's lock, tab-separated:
#                                 what, locked value, upstream's newest now, and
#                                 the issue URL (or -)
#   lock.sh releases <type>       the type's release tools, as release_tools
#                                 lists them (no network: a check that each
#                                 locked release tool is still declared)
#   lock.sh path <type>           where the type's lock is, whether or not it
#                                 exists yet
#
# A lock is `<kind> <name> <value>` lines, sorted, under a comment header:
#
#   base - <image ref>@sha256:...    the type's base image, by digest
#   apt-snapshot - <id>              the Ubuntu snapshot the build's apt reads
#   apt <package> <version>          what apt resolves the type's packages to
#   apt-floating <package> <version> one from a source with no snapshot
#                                    (NVIDIA's CUDA repo): recorded, not pinned
#   apt-pin-snapshot <package> <version>@<id>
#                                    a pin that names the snapshot its version
#                                    comes from. A record: the build reads it
#                                    from the list, as it does the version. Its
#                                    own line, since a version the base image
#                                    already has gets no apt line
#   claude - <version>               Claude Code
#   rust - <version>                 the Rust toolchain, if the type has rust
#   terraform - <version>            Terraform, if the type has terraform
#   release <name> <tag>             a tool from an upstream release: each
#                                    one a toolset script declares a source
#                                    for (release_tools below)
#   brew <formula> <version>         Homebrew, dependencies included: a record
#                                    and a build trigger, never a pin (owner,
#                                    2026-11-06)
#   pinned <what> <issue URL or ->   a pin where the dependency is declared
#                                    (gcp.md, "Pinning a dependency"). An apt
#                                    pin is `pinned apt/<package> <URL>`, the
#                                    URL from its list line's `# pinned:`
#                                    comment
#
# Each value is resolved within the constraint declared where the dependency
# lives: BASE_IMAGE and CLAUDE_VERSION in docker-bake.hcl, packages.txt and
# build-packages.txt lines (name=version pins one, and `snapshot=<id>` in its
# `# pinned:` comment takes that version from that snapshot), RUST_TOOLCHAIN and
# TERRAFORM_VERSION in their toolsets' install.sh, and each release tool's
# NAME_VERSION in the toolset script that reads it, beside NAME_RELEASES, where
# its releases come from (release_tools). The apt snapshot moves only when what
# apt resolves today differs from what the lock recorded.
#
#   LOCK_NOW       the snapshot id to use if apt moved (default: now, UTC)
#   LOCK_DIR       where the current locks are (default images/cloud)
#   LOCK_REGISTRY_WAITS  seconds to wait before each retry of a rate-limited
#                  registry read (default "30 60 120 180 240"; tests set zeros)
# Run from the repo root.
set -euo pipefail
# A failure inside $(...) fails the script too: a lock resolved halfway must
# never be printed as if whole.
shopt -s inherit_errexit

# shellcheck source=scripts/cloud/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
AGENT_GCP_ENV_DIR=infra/gcp
# github_latest: the release lookup the toolsets' builds use, so the lock reads
# a GitHub release as the build does. Before die below, which replaces its die
# with the lock's own.
# shellcheck source=images/toolsets/install-toolset.sh
source "$(dirname "${BASH_SOURCE[0]}")/../../images/toolsets/install-toolset.sh"

readonly bake_file=images/cloud/docker-bake.hcl
readonly lock_dir="${LOCK_DIR:-images/cloud}"
readonly claude_releases=https://downloads.claude.ai/claude-code-releases
readonly rust_dist=https://static.rust-lang.org/dist
readonly terraform_index=https://releases.hashicorp.com/terraform/index.json
# The one *_VERSION constraint a toolset declares that lock.sh resolves itself
# (resolve_terraform) rather than as a release tool (release_tools).
readonly terraform_var=TERRAFORM_VERSION
readonly registry_waits="${LOCK_REGISTRY_WAITS:-30 60 120 180 240}"

die() {
	printf 'lock: %s\n' "$*" >&2
	exit 1
}
usage() { die "usage: lock.sh resolve <type> | bake-args <type> | diff <old> <new> | summary <old> <new> | pins <type> | releases <type> | path <type>"; }

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

# pin_issue <line>: the issue the line's `# pinned:` note names (gcp.md,
# "Pinning a dependency"): the first word after `pinned:`, with any spaces or
# tabs, or none, after the `#` and after the colon; - when that word is - or
# the note's `snapshot=<id>`; nothing when the line has no note. The one reader
# of the note, for every kind of pin.
pin_issue() {
	[[ "$1" =~ \#[[:space:]]*pinned:[[:space:]]*([^[:space:]]+) ]] || return 0
	if [[ "${BASH_REMATCH[1]}" == snapshot=* ]]; then
		printf -- -
	else
		printf '%s' "${BASH_REMATCH[1]}"
	fi
}

# pin_note <file> <text>: the issue of the first `# pinned:` note (pin_issue)
# on the line declaring <text>, then the line above it, then, when that line
# opens a block (the bake file's `variable "CLAUDE_VERSION" {`, a comment after
# the brace allowed), any line of the block, nested blocks included, such as
# its `default = ...`; - when there's none. Braces in strings and comments
# don't count.
pin_note() {
	local lines line issue
	# Assigned first: a failure in <(...) would pass as no note.
	lines="$(awk -v text="$2" '
		# code(line): the line as HCL sees its structure, for finding and
		# counting braces: quoted strings emptied, then /* */ on the line and a
		# trailing # or // comment dropped (strings first, so a URL in one
		# stays whole). A /* */ spanning lines is not followed.
		function code(line,  cut) {
			gsub(/"([^"\\]|\\.)*"/, "\"\"", line)
			gsub(/\/\*([^*]|\*+[^*\/])*\*+\//, "", line)
			cut = index(line, "#")
			if (index(line, "//") && (!cut || index(line, "//") < cut)) cut = index(line, "//")
			if (cut) line = substr(line, 1, cut - 1)
			return line
		}
		# The lines a note may be on, in order: the declaring line, the one
		# above it, then the block it opens.
		depth {
			print
			line = code($0)
			depth += gsub(/\{/, "", line)
			depth -= gsub(/\}/, "", line)
			if (depth <= 0) exit
			next
		}
		index($0, text) {
			print
			print previous
			if (code($0) ~ /\{[[:space:]]*$/) { depth = 1; next }
			exit
		}
		{ previous = $0 }' "$1")"
	while IFS= read -r line; do
		issue="$(pin_issue "$line")"
		if [[ -n "$issue" ]]; then
			printf '%s\n' "$issue"
			return
		fi
	done <<<"$lines"
	printf -- '-\n'
}

# bake_print <type>: the type's build graph as bake resolves it, with nothing
# from the environment overriding the file's own defaults.
bake_print() {
	env -u CLAUDE_VERSION -u REGISTRY -u TAG -u COMMIT \
		docker buildx bake -f "$bake_file" --print "$1" 2>/dev/null
}

# --- Upstream ----------------------------------------------------------------

# fetch <url>: its body. curl's own error doesn't name the URL (lock-watch run
# 43715897836 failed on a bare "curl: (22) ... 403"), so the failure does.
fetch() { curl -fsSL --retry 3 "$1" || die "couldn't fetch $1"; }

# image_digest <ref>: the digest a tag names now. mirror.gcr.io answers 429
# when Google's quota on its reads from Docker Hub runs out (lock-watch run
# 43141207783, 2026-11-12), a limit that clears in minutes, so a 429 is retried
# after each of registry_waits, the way curl's --retry treats one above. The
# waits add up to about ten minutes: the quota has outlasted three and a half
# (runs 43792252860 and 43793578932, 2026-11-16), while another read cleared
# after the first 30 seconds. Any other failure fails at once.
image_digest() {
	local wait out err
	err="$(mktemp)"
	for wait in $registry_waits -; do
		if out="$(docker buildx imagetools inspect "$1" --format '{{json .Manifest}}' 2>"$err")"; then
			rm -f "$err"
			jq -er .digest <<<"$out" || die "no digest for $1 in: $out"
			return
		fi
		if [[ "$wait" == - ]] || ! grep -qE '429 Too Many Requests|toomanyrequests' "$err"; then
			cat "$err" >&2
			rm -f "$err"
			die "couldn't read $1's digest"
		fi
		printf 'lock: %s: registry rate limit (429), retrying in %ss\n' "$1" "$wait" >&2
		sleep "$wait"
	done
}

resolve_claude() {
	local constraint="$1" version
	if [[ "$constraint" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
		printf 'claude - %s\npinned claude %s\n' "$constraint" "$(pin_note "$bake_file" 'variable "CLAUDE_VERSION"')"
		return
	fi
	version="$(claude_channel "$constraint")"
	printf 'claude - %s\n' "$version"
}

# claude_channel <channel>: the version Claude Code's channel (latest, stable)
# names now.
claude_channel() {
	local version
	version="$(fetch "$claude_releases/$1")"
	[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "Claude Code's $1 channel gave '$version'"
	printf '%s' "$version"
}

# rust_channel <channel>: the version rustup's channel (stable, or a minor line
# such as 1.91) names now, from its manifest. Fetched whole first: the manifest
# is large, and a reader that stops at the version would close curl's pipe,
# which fails it (curl exits 23).
rust_channel() {
	local manifest version
	manifest="$(fetch "$rust_dist/channel-rust-$1.toml")"
	version="$(awk '/^\[pkg\.rust\]/ {found = 1} found && /^version = / {gsub(/"/, "", $3); print $3; exit}' <<<"$manifest")"
	[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "rust $1: no version in its channel manifest"
	printf '%s' "$version"
}

resolve_rust() {
	local file=images/toolsets/rust/install.sh constraint version
	constraint="$(declared "$file" RUST_TOOLCHAIN)"
	if [[ "$constraint" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
		printf 'rust - %s\npinned rust %s\n' "$constraint" "$(pin_note "$file" 'RUST_TOOLCHAIN:-')"
		return
	fi
	version="$(rust_channel "$constraint")"
	[[ "$constraint" == stable ]] || printf 'pinned rust %s\n' "$(pin_note "$file" 'RUST_TOOLCHAIN:-')"
	printf 'rust - %s\n' "$version"
}

# terraform_newest [x.y.]: the newest stable Terraform release, on that line if
# given; nothing if none matches.
terraform_newest() {
	local versions
	versions="$(fetch "$terraform_index" | jq -r '.versions | keys[]')"
	grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' <<<"$versions" | grep -F "${1:-}" | sort -V | tail -n 1 || true
}

resolve_terraform() {
	local file=images/toolsets/terraform/install.sh constraint version line
	constraint="$(declared "$file" "$terraform_var")"
	if [[ "$constraint" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
		printf 'terraform - %s\npinned terraform %s\n' "$constraint" "$(pin_note "$file" "$terraform_var:-")"
	else
		# latest, or ~> x.y: the newest stable release on that line.
		line=""
		if [[ "$constraint" =~ ^~\>[[:space:]]*([0-9]+\.[0-9]+)$ ]]; then
			line="${BASH_REMATCH[1]}."
		elif [[ "$constraint" != latest ]]; then
			die "terraform: can't resolve '$constraint' (a version, ~> x.y or latest)"
		fi
		version="$(terraform_newest "$line")"
		[[ -n "$version" ]] || die "terraform: no release matches '$constraint'"
		[[ "$constraint" == latest ]] || printf 'pinned terraform %s\n' "$(pin_note "$file" "$terraform_var:-")"
		printf 'terraform - %s\n' "$version"
	fi
}

# release_tools <type>: the table of tools the type's toolsets install from an
# upstream release, one `<name> <file> <NAME>_VERSION <source>` line each. Each
# tool is declared in the toolset script that reads it: its constraint,
# `${NAME_VERSION:-...}`, and beside it `NAME_RELEASES=<source>`, which the
# script's own downloads use too. So adding a release tool is that one edit, and
# nothing here names one. <name> is NAME in lower case with - for _, the lock's
# `release <name>` line, which bake-args turns back into NAME_VERSION. A source
# is one of:
#
#   https://github.com/<owner>/<repo>/releases
#                  the tag GitHub redirects /releases/latest to
#   <script>.sh    a script beside the declaring one whose `latest` prints the
#                  newest release (the 3d toolset's blender.sh)
#
# A `${NAME_VERSION:-...}` with no NAME_RELEASES beside it fails (except
# terraform_var, resolved on its own): a declaration deleted or written in a
# form this doesn't read would otherwise drop the tool from the lock, and the
# build would take its newest release unlocked. So in a toolset script that
# form is reserved for locked tools; a setting of another kind takes another
# name (gcp.md, "The lock").
release_tools() {
	local toolsets toolset file var source sources constraints
	[[ -f "images/cloud/$1/Dockerfile" ]] || die "no type $1 (images/cloud/$1/Dockerfile)"
	# Assigned first: a failure in <(...) would pass as no toolsets.
	toolsets="$(cloud_toolsets "$1")"
	while read -r toolset; do
		[[ -n "$toolset" ]] || continue
		for file in "images/toolsets/$toolset"/*.sh; do
			[[ -f "$file" ]] || continue
			sources="$(sed -n -E 's/^[[:space:]]*(readonly[[:space:]]+)?([A-Z][A-Z0-9_]*)_RELEASES=([^[:space:]#;]+).*/\2 \3/p' "$file")"
			constraints="$(grep -oE '\$\{[A-Z][A-Z0-9_]*_VERSION:-' "$file" | sed -E 's/^\$\{(.*)_VERSION:-$/\1/' | LC_ALL=C sort -u)" || true
			for var in $constraints; do
				[[ "${var}_VERSION" == "$terraform_var" ]] || grep -qE "^$var " <<<"$sources" ||
					die "$file: \${${var}_VERSION:-...} has no ${var}_RELEASES=<source> beside it (lock.sh release_tools)"
			done
			while read -r var source; do
				[[ -n "$var" ]] || continue
				# The constraint must be there too: a source alone resolves nothing.
				declared "$file" "${var}_VERSION" >/dev/null
				printf '%s %s %s_VERSION %s\n' "$(tr '[:upper:]_' '[:lower:]-' <<<"$var")" "$file" "$var" "$source"
			done <<<"$sources"
		done
	done <<<"$toolsets"
}

# release_latest <file> <source>: upstream's newest release from a source that
# <file> declares (release_tools).
release_latest() {
	local file="$1" source="$2" tag
	case "$source" in
	https://github.com/*/*/releases) tag="$(github_latest "$source")" ;;
	*.sh)
		[[ "$source" =~ ^[a-z0-9][a-z0-9-]*\.sh$ && -f "$(dirname "$file")/$source" ]] ||
			die "$file: release source '$source' isn't a script beside it"
		tag="$(bash "$(dirname "$file")/$source" latest)" || die "couldn't read the newest release from $(dirname "$file")/$source"
		;;
	*) die "$file: unknown release source '$source' (a GitHub releases URL or a script beside it)" ;;
	esac
	[[ "$tag" =~ ^[A-Za-z0-9][A-Za-z0-9._+-]*$ ]] || die "$source gave '$tag' as its newest release"
	printf '%s' "$tag"
}

# resolve_release <name> <file> <NAME_VERSION> <source>: a release_tools row,
# from its source or its pin.
resolve_release() {
	local name="$1" file="$2" var="$3" source="$4" constraint tag
	constraint="$(declared "$file" "$var")"
	if [[ "$constraint" == latest ]]; then
		# Assigned first: a failed substitution in printf's arguments wouldn't fail.
		tag="$(release_latest "$file" "$source")"
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

# package_lines <type>: the lines of every apt list the type's image installs,
# comments included: the base's list, then each toolset's packages.txt and
# build-packages.txt (what a builder stage installs, whose runtime libraries the
# image keeps at those versions).
package_lines() {
	local toolset list
	cat images/cloud/base/packages.txt
	while read -r toolset; do
		for list in packages.txt build-packages.txt; do
			[[ ! -f "images/toolsets/$toolset/$list" ]] || cat "images/toolsets/$toolset/$list"
		done
	done < <(cloud_toolsets "$1")
}

# packages_of <type>: the apt packages the type's image asks for, as the build
# reads them (install-toolset.sh's install_apt).
packages_of() { package_lines "$1" | sed -e 's/#.*//' -e 's/[[:space:]]*$//' -e '/^$/d'; }

# apt_pins_of <type>: each pin whose `# pinned:` comment names its snapshot
# (`name=version  # pinned: <issue URL> snapshot=<id>`), as name=version@id, one
# per line: the list images/cloud/base/apt-pins.sh takes. The builds read the
# line as an ordinary pin, the comment stripped.
apt_pins_of() {
	local line package snapshot
	while IFS= read -r line; do
		[[ "$line" == *'#'*snapshot=* ]] || continue
		package="${line%%#*}"
		package="${package//[[:space:]]/}"
		[[ -n "$package" ]] || continue
		# A word of its own, or the note's first word straight after `pinned:`,
		# which pin_issue accepts too (`#pinned:snapshot=<id>`).
		[[ "${line#*#}" =~ (^|[[:space:]]|pinned:)snapshot=([^[:space:]]*) ]] ||
			die "$1: '$line': snapshot= is a word of its own in the comment"
		snapshot="${BASH_REMATCH[2]}"
		[[ "$package" =~ ^[a-z0-9][a-z0-9.+-]*=[^=@]+$ ]] ||
			die "$1: '$line': a snapshot needs a name=version pin"
		[[ "$snapshot" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] ||
			die "$1: '$line': '$snapshot' isn't a snapshot id (YYYYMMDDTHHMMSSZ)"
		printf '%s@%s\n' "$package" "$snapshot"
	done < <(package_lines "$1")
}

# apt_pin_notes <type>: a `pinned apt/<name> <issue URL or ->` line for each
# name=version pin in the type's package lists, the URL its `# pinned:` note
# names (pin_issue; - when there is none). A package pinned the same way in two
# lists (curl, in the base's and core's) is one line. Two lists that name
# different issues for one package fail: the pin's issue is one fact, and which
# one the watch should show is not for a script to guess. A list that names an
# issue beside one that names none gives the issue.
apt_pin_notes() {
	local line package url
	local -A noted=()
	while IFS= read -r line; do
		package="${line%%#*}"
		package="${package//[[:space:]]/}"
		[[ "$package" =~ ^([a-z0-9.+-]+)= ]] || continue
		package="${BASH_REMATCH[1]}"
		url="$(pin_issue "$line")"
		url="${url:--}"
		if [[ "${noted[$package]:--}" != - && "$url" != - && "${noted[$package]}" != "$url" ]]; then
			die "$1: apt/$package's pin names two issues: ${noted[$package]} and $url"
		fi
		[[ "$url" != - ]] || url="${noted[$package]:--}"
		noted[$package]="$url"
	done < <(package_lines "$1")
	for package in "${!noted[@]}"; do
		printf 'pinned apt/%s %s\n' "$package" "${noted[$package]}"
	done | LC_ALL=C sort
}

# apt_pins <type>: the type's APT_PINS, for the resolve and the build alike: one
# per identical pin, since a package can be in two lists (curl, in the base's and
# core's); apt-pins.sh still refuses two that differ.
apt_pins() { apt_pins_of "$1" | LC_ALL=C sort -u; }

# resolve_apt <type> <base ref> <old lock or empty>: what apt resolves the type's
# packages to today, in a stock container at the locked base digest, and the
# snapshot that holds it: the old one if nothing moved, else now. The container
# runs the build's own apt bootstrap, pins included, then simulates the install
# (images/cloud/base/apt-prepare.sh --simulate), so the lock lists what the
# build installs.
resolve_apt() {
	local type="$1" base="$2" old="$3" packages pins notes newest_pin now resolved
	mapfile -t packages < <(packages_of "$type")
	# Assigned, not read through <(...): a bad pin's die has to stop the resolve.
	pins="$(apt_pins "$type")"
	notes="$(apt_pin_notes "$type")"
	now="${LOCK_NOW:-$(date -u +%Y%m%dT%H%M%SZ)}"
	# --entrypoint: CUDA's entrypoint prints a banner on stdout. apt-prepare.sh
	# reads its helpers from beside it, as in the base Dockerfile's mounts.
	# --mount, unlike -v, refuses a missing source.
	resolved="$(docker run --rm --entrypoint /bin/bash \
		--mount "type=bind,source=$PWD/images/cloud/base/apt-prepare.sh,target=/tmp/apt-prepare.sh,readonly" \
		--mount "type=bind,source=$PWD/images/cloud/base/apt-build.conf,target=/tmp/apt-build.conf,readonly" \
		--mount "type=bind,source=$PWD/images/cloud/base/apt-pins.sh,target=/tmp/apt-pins.sh,readonly" \
		--mount "type=bind,source=$PWD/images/toolsets/install-toolset.sh,target=/tmp/install-toolset.sh,readonly" \
		-e APT_PINS="${pins//$'\n'/ }" "$base" /tmp/apt-prepare.sh --simulate "${packages[@]}")" ||
		die "apt couldn't resolve $type's packages in $base"
	# Inst <name> [<installed>] (<version> <origin>[, <origin>...] [<arch>])
	resolved="$(sed -n 's/^Inst //p' <<<"$resolved" |
		sed -E 's/^([^ ]+) (\[[^]]*\] )?\(([^ ]+) (.*) \[[^]]*\]\).*/\1 \3 \4/' |
		awk '{kind = ($3 ~ /^Ubuntu:/) ? "apt" : "apt-floating"; print kind, $1, $2}' | LC_ALL=C sort)"
	[[ -n "$resolved" ]] || die "apt resolved nothing for $type"
	# The old snapshot stays only if no pin's snapshot is newer: the build
	# refuses one that is (apt-pins.sh), since its whole index is a source.
	# Snapshot ids are timestamps, so they sort as text.
	newest_pin="$(sed -n 's/.*@//p' <<<"$pins" | LC_ALL=C sort | tail -n 1)"
	if [[ -n "$old" ]] && [[ "$(lock_value "$old" base)" == "$base" ]] &&
		[[ "$(grep -E '^apt(-floating)? ' "$old" | LC_ALL=C sort)" == "$resolved" ]] &&
		! [[ "$newest_pin" > "$(lock_value "$old" apt-snapshot)" ]]; then
		printf 'apt-snapshot - %s\n' "$(lock_value "$old" apt-snapshot)"
	else
		printf 'apt-snapshot - %s\n' "$now"
	fi
	printf '%s\n' "$resolved"
	# A name=version line pins that package, and its snapshot, if it names one.
	[[ -z "$notes" ]] || printf '%s\n' "$notes"
	[[ -z "$pins" ]] || sed -E 's/^([^=]+)=(.*)$/apt-pin-snapshot \1 \2/' <<<"$pins"
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

# Homebrew on PATH (a hosted runner has it installed but not on PATH, so the
# workflows needn't), the taps the Brewfiles name trusted the way
# install-toolset.sh trusts them, and its formula data brought up to date.
prepare_brew() {
	local tap
	if ! command -v brew >/dev/null; then
		[[ -x "$(brew_path)" ]] || die "no brew on PATH or at $(brew_path)"
		brew_env
	fi
	export HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_ANALYTICS=1 HOMEBREW_NO_ENV_HINTS=1
	brew update --quiet >&2 2>&1
	sed -n 's/^tap "\([^"]*\)".*/\1/p' images/toolsets/*/Brewfile | sort -u | while read -r tap; do
		brew tap "$tap" >&2 2>&1
		brew trust --tap "$tap" >&2
	done
}

cmd_resolve() {
	local type="$1" graph base base_lines claude old toolsets releases body name file var source
	[[ "$(uname -s)" == Linux ]] || die "resolve needs Linux: Homebrew resolves dependencies for the OS it runs on"
	[[ -f "infra/gcp/$type.env" && "$(gcp_backend "$type")" == run ]] || die "$type isn't a Cloud Run type in infra/gcp"
	graph="$(bake_print "$type")" || die "docker buildx bake --print $type failed"
	base="$(jq -er '[.target[] | select(.dockerfile == "images/cloud/base/Dockerfile") | .args.BASE_IMAGE][0] // empty' <<<"$graph")" ||
		die "$type's build graph has no base with a BASE_IMAGE"
	claude="$(jq -er --arg type "$type" '.target[$type].args.CLAUDE_VERSION // empty' <<<"$graph")" ||
		die "$type's build graph has no CLAUDE_VERSION"
	old="$(lock_file "$type")"
	[[ -f "$old" ]] || old=""
	# Assigned first: a failure in <(...) would pass as no toolsets.
	toolsets="$(cloud_toolsets "$type")"
	mapfile -t toolsets <<<"$toolsets"
	releases="$(release_tools "$type")"
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
		while read -r name file var source; do
			[[ -n "$name" ]] || continue
			resolve_release "$name" "$file" "$var" "$source"
		done <<<"$releases"
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
# declares it. APT_PINS comes from the package lists, not the lock, the way
# install_apt takes a pin's version: a pin needs no resolving, and a pull
# request that adds one builds with it before the watch re-locks main.
cmd_bake_args() {
	local file kind name value pins
	file="$(lock_file "$1")"
	# First, so a bad pin fails before anything is printed.
	pins="$(apt_pins "$1")"
	# Not printed: the build doesn't read it, but a pull request that names two
	# issues for one package fails here, in cloud-build, not in the next resolve.
	apt_pin_notes "$1" >/dev/null
	[[ -f "$file" ]] || die "no lock for $1 ($file): cloud-build resolves a new type's lock on its pull request for the author to commit (AGENTS.md)"
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
		apt | apt-floating | apt-pin-snapshot | brew | pinned | \#*) ;;
		*) die "$file: unknown line kind '$kind'" ;;
		esac
	done < <(grep -v '^#' "$file")
	[[ -z "$pins" ]] || printf '*.args.APT_PINS=%s\n' "${pins//$'\n'/ }"
}

# entries <file>: `<kind> <name> <value>` without the header.
entries() { grep -v '^#' "$1" | LC_ALL=C sort; }

# cmd_diff <old> <new>: in C's collation throughout, since join reads its input
# in the caller's locale, and one that orders `apt/` before `apt-` (en_US.UTF-8)
# misjoins the C-sorted keys (r3.f6).
cmd_diff() {
	local -x LC_ALL=C
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
		# A package counts once, whichever of its lines moved: a pin whose
		# version the base already has has no apt line, only its pin line.
		kind == "apt" || kind == "apt-floating" || kind == "apt-pin-snapshot" {
			if (!(name in moved)) { moved[name] = 1; apt++ }
			next
		}
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

# release_row <releases> <what>: the release_tools row for a `pinned <what>`
# line, or nothing.
release_row() { awk -v name="$2" '$1 == name' <<<"$1"; }

# pinned_value <file> <what> <releases>: the locked value a `pinned <what>` line
# holds: a release tool's on its `release` line (the release_tools table in
# <releases>). An apt pin the base image already satisfies has no apt line; its
# apt-pin-snapshot line holds the version.
pinned_value() {
	local value
	case "$2" in
	apt/*)
		value="$(lock_value "$1" apt "${2#apt/}")"
		[[ -n "$value" ]] || value="$(lock_value "$1" apt-pin-snapshot "${2#apt/}")"
		printf '%s' "${value%@*}"
		;;
	*)
		if [[ -n "$(release_row "$3" "$2")" ]]; then
			lock_value "$1" release "$2"
		else
			lock_value "$1" "$2"
		fi
		;;
	esac
}

# upstream_newest <what> <locked value> <releases>: what upstream would give
# with no pin, now. apt's needs a container at the base, so it isn't resolved
# here (-).
upstream_newest() {
	local digest row file source
	case "$1" in
	claude) claude_channel latest ;;
	rust) rust_channel stable ;;
	terraform) terraform_newest ;;
	base)
		digest="$(image_digest "${2%@*}")"
		printf '%s@%s' "${2%@*}" "$digest"
		;;
	apt/*) printf '%s' - ;;
	*)
		row="$(release_row "$3" "$1")"
		[[ -n "$row" ]] || die "pinned '$1': no upstream known"
		read -r _ file _ source <<<"$row"
		release_latest "$file" "$source"
		;;
	esac
}

cmd_pins() {
	local file what issue value newest releases
	file="$(lock_file "$1")"
	[[ -f "$file" ]] || die "no lock for $1 ($file)"
	releases="$(release_tools "$1")"
	while read -r _ what issue; do
		value="$(pinned_value "$file" "$what" "$releases")"
		newest="$(upstream_newest "$what" "$value" "$releases")"
		printf '%s\t%s\t%s\t%s\n' "$what" "${value:--}" "${newest:--}" "$issue"
	done < <(awk '$1 == "pinned"' "$file")
}

# Sourced (tests): the functions alone.
[[ "${BASH_SOURCE[0]}" == "$0" ]] || return 0

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
pins)
	(($# == 2)) || usage
	cmd_pins "$2"
	;;
releases)
	(($# == 2)) || usage
	[[ "$2" =~ ^[a-z0-9-]+$ ]] || usage
	release_tools "$2"
	;;
path)
	(($# == 2)) || usage
	[[ "$2" =~ ^[a-z0-9-]+$ ]] || usage
	lock_file "$2"
	printf '\n'
	;;
*) usage ;;
esac
