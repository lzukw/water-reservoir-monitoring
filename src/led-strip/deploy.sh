#!/usr/bin/env bash
# Deploy the project to the Pico filesystem.
#   ./deploy.sh                copy only modified/missing local files to the board
#   ./deploy.sh --dry-run      only list what differs (nothing is copied/wiped)
#   ./deploy.sh --wipe-first   wipe the board filesystem first, then copy all files
# Env: MPREMOTE=/path/to/mpremote   mpremote binary (default ~/.local/bin/mpremote)
set -euo pipefail
cd "$(dirname "$0")"
M="${MPREMOTE:-/home/zukw/.local/bin/mpremote}"

DRY_RUN=0
WIPE_FIRST=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --wipe-first) WIPE_FIRST=1 ;;
        *) echo "usage: $0 [--dry-run] [--wipe-first]" >&2; exit 2 ;;
    esac
done

# files that belong on the board root
# (logger.py, wifi.py, ping.py, timeutil.py are symlinks into the water-level
#  project - mpremote fs cp follows them and copies the file contents)
ROOT_FILES="boot.py config.json config.py logger.py timeutil.py ping.py wifi.py ledstrip.py microdot_app.py main.py"
SUBDIRS="lib templates"

local_files() {  # all local files that are meant to be on the board
    local f d
    for f in $ROOT_FILES; do [ -f "$f" ] && echo "$f"; done
    for d in $SUBDIRS; do
        [ -d "$d" ] && find "$d" -type f ! -path '*__pycache__*' ! -name '*.pyc'
    done
}

board_files() {  # recursive ls of the board, paths relative to : (like local)
    local -a stack=(":")
    local dir size name
    while [ "${#stack[@]}" -gt 0 ]; do
        dir="${stack[${#stack[@]}-1]}"
        stack=("${stack[@]:0:${#stack[@]}-1}")
        while read -r size name; do
            [ -n "${name:-}" ] || continue
            case "$name" in
                __pycache__/) continue ;;
                */) if [ "$dir" = ":" ]; then stack+=(":${name%/}"); else stack+=("$dir/${name%/}"); fi ;;
                *) if [ "$dir" = ":" ]; then echo "$name"; else echo "${dir#:}/$name"; fi ;;
            esac
        done < <($M fs ls "$dir" 2>/dev/null | awk '!/^ls /')
    done
}

deploy_one() {  # $1 = path relative to project root (same on board, relative to :)
    local f="$1"
    [ -f "$f" ] || return 0
    if ! $M fs cat ":$f" >/dev/null 2>&1; then
        echo "BOARD-MISSING  $f"
        if [ "$DRY_RUN" -eq 0 ]; then $M fs cp "$f" ":$f"; echo "  -> copied to board"; fi
    elif ! cmp -s <(tr -d '\r' < "$f") <($M fs cat ":$f" 2>/dev/null | tr -d '\r'); then
        echo "DIFFERS        $f"
        if [ "$DRY_RUN" -eq 0 ]; then $M fs cp "$f" ":$f"; echo "  -> copied to board"; fi
    fi
}

if [ "$WIPE_FIRST" -eq 1 ] && [ "$DRY_RUN" -eq 0 ]; then
    echo "== wiping Pico filesystem =="
    for entry in $($M fs ls : 2>/dev/null | awk '!/^ls /' | awk '{print $NF}'); do
        $M fs rm -r ":$entry" 2>/dev/null || true
    done
    echo "== copying all files =="
    for f in $ROOT_FILES; do [ -f "$f" ] && $M fs cp "$f" ":$f"; done
    $M fs cp -r lib :
    $M fs cp -r templates :
    echo "== done - board filesystem wiped and fully redeployed =="
    exit 0
fi

[ "$WIPE_FIRST" -eq 1 ] && echo "(dry run: --wipe-first would wipe the board filesystem first)"
echo "== local vs board =="
mapfile -t LOCALS < <(local_files)
for f in "${LOCALS[@]}"; do
    deploy_one "$f"
done

echo "== board-only files (not touched) =="
comm -23 <(board_files | sort -u) <(local_files | sort -u) | sed 's/^/BOARD-ONLY     /'

if [ "$DRY_RUN" -eq 1 ]; then
    echo "== dry run - nothing was copied =="
else
    echo "== done - modified files copied to board =="
fi
