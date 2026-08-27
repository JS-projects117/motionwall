#!/usr/bin/env bash
# Fetch mpv + libmpv and only the shared libraries that are missing on this
# system into a private runtime directory. No root required: uses
# `apt-get download` (plain HTTP fetch of .deb files) and `dpkg -x`.
set -euo pipefail

RT="${MOTIONWALL_RUNTIME:-${XDG_DATA_HOME:-$HOME/.local/share}/motionwall/runtime}"
ARCH="$(dpkg --print-architecture)"
MULTIARCH="$(dpkg-architecture -qDEB_HOST_MULTIARCH 2>/dev/null || echo x86_64-linux-gnu)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

log() { printf '\033[1;34m[fetch-local-mpv]\033[0m %s\n' "$*"; }

is_installed() { dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q 'install ok installed'; }

# Direct (non-recursive) dependencies of a package, amd64 only, no virtuals.
direct_deps() {
  apt-cache depends --no-recommends --no-suggests --no-conflicts --no-breaks \
    --no-replaces --no-enhances "$1" 2>/dev/null \
    | sed -n 's/^ *|\{0,1\}Depends: \([^<].*\)$/\1/p' | grep -v ':' || true
}

mkdir -p "$RT"
cd "$TMP"

declare -A DONE
QUEUE=(mpv libmpv2)
while [ "${#QUEUE[@]}" -gt 0 ]; do
  pkg="${QUEUE[0]}"; QUEUE=("${QUEUE[@]:1}")
  [ -n "${DONE[$pkg]:-}" ] && continue
  DONE[$pkg]=1
  if is_installed "$pkg"; then continue; fi
  log "downloading $pkg"
  if ! apt-get download "$pkg" >/dev/null 2>&1; then
    log "warning: could not download $pkg (skipping)"; continue
  fi
  for dep in $(direct_deps "$pkg"); do
    [ -z "${DONE[$dep]:-}" ] && QUEUE+=("$dep")
  done
done

for deb in *.deb; do
  [ -e "$deb" ] || { log "nothing downloaded (is mpv already installed?)"; exit 0; }
  dpkg -x "$deb" "$RT"
done

# Keep only what mpv really needs: prune libraries that ldd does not resolve to.
export LD_LIBRARY_PATH="$RT/usr/lib/$MULTIARCH${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
missing="$(ldd "$RT/usr/bin/mpv" "$RT/usr/lib/$MULTIARCH/libmpv.so.2" 2>/dev/null | grep 'not found' | sort -u || true)"
if [ -n "$missing" ]; then
  log "ERROR: still unresolved libraries:"; echo "$missing"; exit 1
fi

"$RT/usr/bin/mpv" --version | head -1
log "runtime ready at $RT"
