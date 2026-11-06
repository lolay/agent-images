#!/bin/bash
# The gis toolset's environment: pixi installs pixi.toml's conda-forge packages
# into /opt/gis, owned by the image's user. Run by install-toolset.sh after the
# Brewfile (pixi, uv), with Homebrew on PATH and TOOLSET_RECORD_DIR set.
#
# Sessions don't activate the environment themselves: this records its bin for
# the front of PATH (gis.path) and what activation sets (GDAL_DATA, PROJ_DATA and
# the rest) as gis.profile.sh, which the cloud profile sources.
set -euo pipefail

: "${TOOLSET_RECORD_DIR:?}"

readonly root=/opt/gis
readonly env="$root/.pixi/envs/default"

log() { printf '==> gis: %s\n' "$*"; }

sudo install -d -o "$(id -un)" -g "$(id -gn)" -m 755 "$root"
install -m 644 "$(dirname "${BASH_SOURCE[0]}")/pixi.toml" "$root/pixi.toml"
log "solving and installing $root/pixi.toml from conda-forge"
# pixi reports progress on stderr, which a green build doesn't allow.
pixi install --manifest-path "$root/pixi.toml" 2>&1

# Plugins whose libraries don't resolve are dropped and listed in
# dropped-plugins.txt. conda-forge's builds don't always agree (2026-11-05: the
# TileDB plugins of GDAL and PDAL link an aws-c-s3 newer than hdf5 and
# aws-crt-cpp allow, and QGIS's MDAL provider an older hdf5), and a plugin that
# can't load makes PDAL print errors on every import. Dropping them keeps every
# other package at its newest; when the builds agree, nothing is dropped.
: >"$root/dropped-plugins.txt"
for plugin in "$env"/lib/libpdal_plugin_*.so "$env"/lib/gdalplugins/*.so "$env"/lib/qgis/plugins/*.so; do
	# PDAL's are symlinks (.so -> .so.20 -> .so.20.x.y); ldd follows them, and
	# the rm below takes the whole chain.
	[[ -e "$plugin" ]] || continue
	missing="$(LD_LIBRARY_PATH="$env/lib" ldd "$plugin" 2>/dev/null | awk '/not found/ {print $1}' | sort -u | paste -sd ' ')"
	[[ -n "$missing" ]] || continue
	log "dropping ${plugin#"$env"/}: missing $missing"
	printf '%s: missing %s\n' "${plugin#"$env"/}" "$missing" >>"$root/dropped-plugins.txt"
	rm -f "$plugin" "${plugin%.so}".so.*
done

# The activated environment as plain exports: what pixi shell-hook --json
# reports, from a minimal environment, less what that environment already had,
# PATH (gis.path covers it) and pixi's own shell state. Static values, so every
# shell, `set -u` ones included, gets them without running activation scripts.
minimal=(env -i "HOME=$HOME" "PATH=/usr/bin:/bin")
{
	printf '# The gis toolset environment (images/toolsets/gis/install.sh), from\n'
	printf '# pixi shell-hook --json at build time.\n'
	printf 'export GIS_ENV=%s\n' "$env"
	# Where the manifest is, for write-versions.sh's pixi section.
	printf 'export GIS_MANIFEST=%s\n' "$root/pixi.toml"
	# QGIS needs a display; sessions have none. Written literally, for the
	# session's shell to expand.
	# shellcheck disable=SC2016
	printf 'export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"\n'
	"${minimal[@]}" "$(command -v pixi)" shell-hook --manifest-path "$root/pixi.toml" --json |
		jq -r --argjson base "$("${minimal[@]}" jq -n env)" '
			.environment_variables | to_entries[]
			| select(.key != "PATH" and (.key | test("^(PIXI_|CONDA_SHLVL$|CONDA_PROMPT)") | not))
			| select($base[.key] != .value)
			| "export \(.key)=\(.value | @sh)"'
} | sudo tee "$TOOLSET_RECORD_DIR/gis.profile.sh" >/dev/null
printf '%s/bin\n' "$env" | sudo tee "$TOOLSET_RECORD_DIR/gis.path" >/dev/null

# The package cache isn't needed once the environment is linked (cloud images
# keep each toolset in its own layer).
pixi clean cache --yes 2>&1 || rm -rf "${XDG_CACHE_HOME:-$HOME/.cache}/rattler"

log "$("$env/bin/gdalinfo" --version), $("$env/bin/pdal" --version | grep -m1 '^pdal'), $("$env/bin/python" --version)"
log "$(du -sh "$env" | cut -f1) in $env"
