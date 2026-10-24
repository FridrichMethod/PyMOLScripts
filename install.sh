#!/bin/sh
# Install the PyMOL startup files configs/.pymolrc and configs/.pymolrc.py as
# DIR/.pymolrc and DIR/.pymolrc.py, where DIR is --prefix or $HOME.
#
#   curl -fsSL https://raw.githubusercontent.com/FridrichMethod/PyMOLScripts/main/install.sh | sh
#   ./install.sh [--prefix DIR]          # from a clone: install the clone's copies
#
# Source: the configs/ directory next to this script when it holds both files,
# otherwise both are downloaded (curl, or wget without curl) from
#   ${PYMOLSCRIPTS_BASE_URL:-https://raw.githubusercontent.com/FridrichMethod/PyMOLScripts}/${PYMOLSCRIPTS_REF:-main}/configs/<name>
#
# The same command installs and updates; there is no state file. A file whose
# installed copy is already a regular file with identical content is left
# alone, so a run with nothing to change writes nothing. Changed files are
# written to a temporary directory inside DIR and renamed into place, which
# replaces a symlink (dangling or not) instead of writing through it, and
# nothing is renamed unless both files were fetched.
#
# Exit status: 0 success (including "already current"), 1 error, 2 usage error.

set -u

prog=install.sh
default_base_url=https://raw.githubusercontent.com/FridrichMethod/PyMOLScripts

usage() {
    cat <<'EOF'
Usage: install.sh [--prefix DIR]

Install PyMOLScripts' .pymolrc and .pymolrc.py into DIR (default: $HOME).
Rerun the same command to update; files that are already current are left
untouched.

Options:
  --prefix DIR  absolute directory to install into (default: $HOME)
  -h, --help    show this help and exit

Environment:
  PYMOLSCRIPTS_REF       branch, tag or commit to download (default: main)
  PYMOLSCRIPTS_BASE_URL  download base URL
                         (default: https://raw.githubusercontent.com/FridrichMethod/PyMOLScripts)
EOF
}

say() {
    printf 'PyMOLScripts: %s\n' "$*"
}

die() {
    printf '%s: error: %s\n' "$prog" "$*" >&2
    exit 1
}

usage_error() {
    printf '%s: %s\n' "$prog" "$*" >&2
    printf "Try '%s --help'.\n" "$prog" >&2
    exit 2
}

# --- arguments -------------------------------------------------------------

prefix=
prefix_set=0
while [ $# -gt 0 ]; do
    case $1 in
        --prefix)
            [ $# -ge 2 ] || usage_error "--prefix needs a directory"
            prefix=$2
            prefix_set=1
            shift 2
            ;;
        --prefix=*)
            prefix=${1#--prefix=}
            prefix_set=1
            shift
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        --)
            shift
            [ $# -eq 0 ] || usage_error "unexpected argument: $1"
            ;;
        -*) usage_error "unknown option: $1" ;;
        *) usage_error "unexpected argument: $1" ;;
    esac
done

if [ "$prefix_set" = 1 ]; then
    case $prefix in
        /*) dir=$prefix ;;
        *) usage_error "--prefix must be an absolute path: '$prefix'" ;;
    esac
else
    [ -n "${HOME:-}" ] || die "HOME is not set; pass --prefix DIR"
    case $HOME in
        /*) dir=$HOME ;;
        *) die "HOME is not an absolute path: $HOME; pass --prefix DIR" ;;
    esac
fi
dir=${dir%/}
dir_shown=${dir:-/}

# --- preflight: refuse what a rename cannot replace -------------------------

if [ -e "$dir_shown" ] && [ ! -d "$dir_shown" ]; then
    die "$dir_shown exists and is not a directory"
fi
for name in .pymolrc .pymolrc.py; do
    if [ -d "$dir/$name" ]; then
        die "$dir/$name is a directory or a link to one; remove it and rerun"
    fi
done

for tool in cmp mktemp; do
    command -v "$tool" >/dev/null 2>&1 || die "$tool not found; it is needed to install"
done

# --- source ----------------------------------------------------------------

work=  # temporary directory inside $dir, created only when a file changes
stage= # download staging directory under $TMPDIR
# shellcheck disable=SC2317 # invoked by the EXIT trap
cleanup() {
    [ -z "$work" ] || rm -rf "$work"
    [ -z "$stage" ] || rm -rf "$stage"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

# Piped (curl | sh) or run via `sh -s`, $0 names the shell, not this file.
src=
case ${0##*/} in
    sh | -sh | ash | bash | -bash | dash | ksh | mksh | posh | yash | zsh | busybox) ;;
    *)
        case $0 in
            */*) script_dir=${0%/*} ;;
            *) script_dir=. ;;
        esac
        if [ -f "$0" ] && [ -f "$script_dir/configs/.pymolrc" ] &&
            [ -f "$script_dir/configs/.pymolrc.py" ]; then
            src=$script_dir/configs
        fi
        ;;
esac
if [ -z "$src" ]; then
    if command -v curl >/dev/null 2>&1; then
        fetcher=curl
    elif command -v wget >/dev/null 2>&1; then
        fetcher=wget
    else
        die "curl not found (wget would also do); it is needed to download the configs"
    fi
    base_url=${PYMOLSCRIPTS_BASE_URL:-$default_base_url}
    base_url=${base_url%/}
    ref=${PYMOLSCRIPTS_REF:-main}
    tmp_root=${TMPDIR:-/tmp}
    tmp_root=${tmp_root%/}
    stage=$(mktemp -d "$tmp_root/pymolscripts-install.XXXXXX" 2>/dev/null) ||
        die "cannot create a temporary directory in $tmp_root"
    for name in .pymolrc .pymolrc.py; do
        url=$base_url/$ref/configs/$name
        if [ "$fetcher" = curl ]; then
            curl -fsL --connect-timeout 20 --max-time 300 -o "$stage/$name" "$url"
        else
            wget -q -T 30 -O "$stage/$name" "$url"
        fi
        status=$?
        [ "$status" -eq 0 ] || die "download failed ($fetcher exit $status): $url"
        [ -s "$stage/$name" ] || die "downloaded file is empty: $url"
    done
    src=$stage
fi

# --- compare, stage, rename ------------------------------------------------

set --
for name in .pymolrc .pymolrc.py; do
    dest=$dir/$name
    if [ -f "$dest" ] && [ ! -L "$dest" ] && cmp -s "$src/$name" "$dest"; then
        continue
    fi
    set -- "$@" "$name"
done
if [ $# -eq 0 ]; then
    say ".pymolrc and .pymolrc.py in $dir_shown are already current"
    exit 0
fi

if [ ! -d "$dir_shown" ]; then
    err=$(mkdir -p "$dir_shown" 2>&1) || die "cannot create $dir_shown: $err"
fi
work=$(mktemp -d "$dir/.pymolscripts-install.XXXXXX" 2>/dev/null) ||
    die "cannot create a temporary directory in $dir_shown"
for name in "$@"; do
    err=$(cat "$src/$name" 2>&1 >"$work/$name") || die "cannot write $work/$name: $err"
done
for name in "$@"; do
    dest=$dir/$name
    if [ -f "$dest" ] && [ ! -L "$dest" ]; then
        verb=updated
    else
        verb=installed
    fi
    [ ! -d "$dest" ] || die "$dest is a directory or a link to one; remove it and rerun"
    err=$(mv -f "$work/$name" "$dest" 2>&1) || die "cannot rename $work/$name to $dest: $err"
    say "$verb $dest"
done
exit 0
