#!/bin/sh
# Install the PyMOL startup files configs/.pymolrc and configs/.pymolrc.py as
# DIR/.pymolrc and DIR/.pymolrc.py, where DIR is --prefix or $HOME.
#
#   curl -fsSL https://raw.githubusercontent.com/FridrichMethod/PyMOLScripts/main/install.sh | sh
#   ./install.sh [--prefix DIR] [--force]   # from a clone: install the clone's copies
#
# Source: the configs/ directory next to this script when the script is named
# install.sh, sits in a git checkout (.git next to it), configs/ holds both
# files, and .git, configs/ and both files belong to the user running it and
# are not symlinks; otherwise both are downloaded (curl, or wget without curl)
# from
#   ${PYMOLSCRIPTS_BASE_URL:-https://raw.githubusercontent.com/FridrichMethod/PyMOLScripts}/${PYMOLSCRIPTS_REF:-main}/configs/<name>
# A copy run from a shared directory such as /tmp therefore never installs a
# .git and configs/ that someone else put next to it, whatever its name.
#
# The same command installs and updates. After writing a file it keeps a copy
# of the bytes it wrote, the record, at
#   ${XDG_STATE_HOME:-$HOME/.local/state}/pymolscripts/installed<DIR>/<name>
# (XDG_STATE_HOME only when it is absolute), in a directory created private.
# Before writing anything it classifies both files, comparing with CR bytes
# ignored because PyMOL's File > Edit pymolrc saves these CRLF files with LF:
#   - absent, or a dangling symlink: installed;
#   - byte-identical to the new upstream: left alone, and only recorded;
#   - otherwise equal to the new upstream, to the record or, with no record
#     when downloading, to the configs at $legacy_ref: updated;
#   - anything else is a local edit: nothing at all is written, a message on
#     stderr says how to keep or overwrite the edit, and the exit status is 3.
# Files installed before records existed have none; they are recorded or
# updated without a stop when they match the new upstream or the configs at
# $legacy_ref, which is what the installer always wrote before.
# --force overwrites local edits after copying each edited regular file to
# DIR/<name>.local-YYYYmmddHHMMSS (plus -N when taken), a name PyMOL never
# runs, as a hard link to a private copy, which never writes through a link
# someone else put at that name; an edited symlink is replaced and its target
# left untouched.
# Host-only settings belong in DIR/.pymolrc.local.pml, which PyMOL runs after
# both files and this installer never touches.
#
# Changed files are written to a temporary directory inside DIR and renamed
# into place, which replaces a symlink instead of writing through it; nothing
# is renamed unless both files were fetched, and the records are written only
# after the renames, so an interrupted run never makes the next one stop.
#
# Exit status: 0 success (including "already current"), 1 error, 2 usage
# error, 3 stopped for a local edit (nothing was changed).

set -u

prog=install.sh
default_base_url=https://raw.githubusercontent.com/FridrichMethod/PyMOLScripts
# The configs every install wrote before records existed. An unrecorded file
# equal to them is updated rather than reported as a local edit. Remove after
# 2027-04-30, by when every host has run this installer and holds a record.
legacy_ref=41785ca151497dce4e613ef9e19f812102a7b564

usage() {
    cat <<'EOF'
Usage: install.sh [--prefix DIR] [--force]

Install PyMOLScripts' .pymolrc and .pymolrc.py into DIR (default: $HOME).
Rerun the same command to update; files that are already current are left
untouched. A copy of what was installed is kept under
${XDG_STATE_HOME:-$HOME/.local/state}/pymolscripts. A file that differs from
it and from the new upstream is a local edit: the run then writes nothing,
says how to keep or overwrite the edit, and exits 3. Files installed before
these copies existed count as unedited when they match the new or the
previous upstream. Put host-only settings in DIR/.pymolrc.local.pml, which
PyMOL runs after both files and this installer never touches.

Options:
  --prefix DIR  absolute directory to install into (default: $HOME)
  --force       overwrite local edits, first copying each edited file to
                DIR/<name>.local-YYYYmmddHHMMSS
  -h, --help    show this help and exit

Environment:
  PYMOLSCRIPTS_REF       branch, tag or commit to download (default: main)
  PYMOLSCRIPTS_BASE_URL  download base URL
                         (default: https://raw.githubusercontent.com/FridrichMethod/PyMOLScripts)
  XDG_STATE_HOME         where the copies are kept (default: $HOME/.local/state)

Exit status: 0 done or already current, 1 error, 2 usage error,
3 stopped for a local edit (nothing was changed).
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

# quote WORD: WORD as one shell word, quoted only when it needs to be.
quote() {
    case $1 in
        '' | *[!A-Za-z0-9_./:@%+=,-]*) ;;
        *)
            printf '%s' "$1"
            return
            ;;
    esac
    q_rest=$1
    q_done=
    while :; do
        case $q_rest in
            *\'*)
                q_done=$q_done${q_rest%%\'*}\'\\\'\'
                q_rest=${q_rest#*\'}
                ;;
            *) break ;;
        esac
    done
    printf "'%s%s'" "$q_done" "$q_rest"
}

# --- arguments -------------------------------------------------------------

prefix=
prefix_set=0
force=0
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
        --force)
            force=1
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

state_home=${XDG_STATE_HOME:-}
case $state_home in
    /*) ;;
    *)
        case ${HOME:-} in
            /*) state_home=$HOME/.local/state ;;
            *) die "cannot locate the record directory; set XDG_STATE_HOME or HOME to an absolute path" ;;
        esac
        ;;
esac
record_dir=${state_home%/}/pymolscripts/installed$dir

# --- preflight: refuse what a rename cannot replace -------------------------

if [ -e "$dir_shown" ] && [ ! -d "$dir_shown" ]; then
    die "$dir_shown exists and is not a directory"
fi
for name in .pymolrc .pymolrc.py; do
    if [ -d "$dir/$name" ]; then
        die "$dir/$name is a directory or a link to one; remove it and rerun"
    fi
    if [ -e "$dir/$name" ] && [ ! -f "$dir/$name" ]; then
        die "$dir/$name is not a regular file; remove it and rerun"
    fi
    if [ -e "$record_dir/$name" ] && [ ! -f "$record_dir/$name" ]; then
        die "$record_dir/$name is not a regular file; remove it and rerun"
    fi
done

for tool in mktemp od tr; do
    command -v "$tool" >/dev/null 2>&1 || die "$tool not found; it is needed to install"
done

# dump FILE: the bytes of FILE as text, so that the shell can compare them.
# od is POSIX and comes with every coreutils; cmp does not (minimal images
# ship no diffutils). text FILE: the same with every CR byte deleted.
dump() {
    od -An -v -tx1 "$1"
}
text() {
    tr -d '\015' <"$1" | od -An -v -tx1
}

# mine PATH: PATH is no symlink and belongs to the user running this script.
# POSIX test has no -O, so the owner comes from ls -n and is compared with id.
uid=
mine() {
    if [ ! -e "$1" ] || [ -L "$1" ]; then
        return 1
    fi
    [ -n "$uid" ] || uid=$(id -u 2>/dev/null) || return 1
    # Only the third field, the numeric owner, is read; names do not matter.
    # shellcheck disable=SC2012
    m_owner=$(ls -dn -- "$1" 2>/dev/null | {
        read -r _ _ m_uid _ && printf '%s' "$m_uid"
    })
    [ -n "$uid" ] && [ "$m_owner" = "$uid" ]
}

# --- source ----------------------------------------------------------------

work=    # temporary directory inside $dir, created only when a file changes
stage=   # download staging directory under $TMPDIR, set in download mode
partial= # record being written
# shellcheck disable=SC2317 # invoked by the EXIT trap
cleanup() {
    [ -z "$work" ] || rm -rf "$work"
    [ -z "$stage" ] || rm -rf "$stage"
    [ -z "$partial" ] || rm -f "$partial"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

# fetch URL FILE: download URL into FILE, or exit.
fetch() {
    if [ "$fetcher" = curl ]; then
        curl -fsL --connect-timeout 20 --max-time 300 -o "$2" "$1"
    else
        wget -q -T 30 -O "$2" "$1"
    fi
    fetch_status=$?
    [ "$fetch_status" -eq 0 ] || die "download failed ($fetcher exit $fetch_status): $1"
    [ -s "$2" ] || die "downloaded file is empty: $1"
}

# Run from a file named install.sh in a git checkout: install its configs.
# Piped (curl | sh) or run via `sh -s`, $0 names the shell, not this file.
src=
case $0 in
    install.sh) script_dir=. ;;
    */install.sh) script_dir=${0%/*} ;;
    *) script_dir= ;;
esac
if [ -n "$script_dir" ] && [ -f "$0" ] && [ -e "$script_dir/.git" ] &&
    [ -f "$script_dir/configs/.pymolrc" ] && [ -f "$script_dir/configs/.pymolrc.py" ]; then
    # In a shared directory such as /tmp anyone can create .git and configs/
    # beside a copy of this script, so only the user's own are a checkout.
    if mine "$script_dir/.git" && mine "$script_dir/configs" &&
        mine "$script_dir/configs/.pymolrc" && mine "$script_dir/configs/.pymolrc.py"; then
        src=$script_dir/configs
    else
        printf '%s: note: ignoring %s: it, its files and .git must be yours and no symlinks; downloading instead\n' \
            "$prog" "$script_dir/configs" >&2
    fi
fi
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
        fetch "$base_url/$ref/configs/$name" "$stage/$name"
    done
    src=$stage
fi

# --- classify --------------------------------------------------------------

# classify NAME: decide what to do with DIR/NAME. Sets `action` (install,
# current, update or edited), `found` (the bytes there, as a dump), `link`
# (1 for a symlink) and `stale` (1 when the record must be (re)written).
# The record is read before the file: every run renames a file before it
# writes its record, so a concurrent run can never pair an old file with a
# newer record and make it look edited.
classify() {
    c_dest=$dir/$1
    c_record=$record_dir/$1
    c_has_record=0
    stale=1
    if [ -f "$c_record" ]; then
        [ -r "$c_record" ] || die "cannot read $c_record"
        c_has_record=1
        c_recorded=$(text "$c_record")
        [ "$(dump "$c_record")" != "$(dump "$src/$1")" ] || stale=0
    fi
    found=
    link=0
    [ ! -L "$c_dest" ] || link=1
    if [ ! -e "$c_dest" ]; then
        action=install
        return 0
    fi
    found=$(dump "$c_dest") || die "cannot read $c_dest"
    action=current
    [ "$found" != "$(dump "$src/$1")" ] || return 0
    action=update
    c_text=$(text "$c_dest")
    [ "$c_text" != "$(text "$src/$1")" ] || return 0
    if [ "$c_has_record" = 1 ]; then
        [ "$c_text" != "$c_recorded" ] || return 0
    elif [ -n "$stage" ]; then
        [ -d "$stage/legacy" ] || mkdir "$stage/legacy" || die "cannot write to $stage"
        fetch "$base_url/$legacy_ref/configs/$1" "$stage/legacy/$1"
        [ "$c_text" != "$(text "$stage/legacy/$1")" ] || return 0
    fi
    action=edited
}

# store NAME / load NAME: keep classify's results for NAME, and `kept`, the
# backup of an edited file, and get them back.
store() {
    case $1 in
        .pymolrc) rc_action=$action rc_found=$found rc_link=$link rc_stale=$stale rc_kept=$kept ;;
        *) py_action=$action py_found=$found py_link=$link py_stale=$stale py_kept=$kept ;;
    esac
}
load() {
    case $1 in
        .pymolrc) action=$rc_action found=$rc_found link=$rc_link stale=$rc_stale kept=$rc_kept ;;
        *) action=$py_action found=$py_found link=$py_link stale=$py_stale kept=$py_kept ;;
    esac
}

edited=0
writes=0 # files to write
needed=0 # files or records to write
for name in .pymolrc .pymolrc.py; do
    classify "$name"
    kept=
    store "$name"
    case $action in
        edited) edited=1 writes=1 needed=1 ;;
        install | update) writes=1 needed=1 ;;
    esac
    [ "$stale" = 0 ] || needed=1
done

# --- stop for a local edit ---------------------------------------------------

# The command that reruns this installer with --force. A download takes the
# installer from main, as the README does, and only the configs from
# PYMOLSCRIPTS_REF: the installer at an older ref has no --force, or is none.
override_command() {
    if [ -z "$stage" ]; then
        case $0 in
            */*) o_cmd="sh $(quote "$0") --force" ;;
            *) o_cmd="sh $(quote "./$0") --force" ;;
        esac
    else
        if [ "$fetcher" = curl ]; then
            o_cmd="curl -fsSL $(quote "$base_url/main/install.sh") |"
        else
            o_cmd="wget -qO- $(quote "$base_url/main/install.sh") |"
        fi
        if [ -n "${PYMOLSCRIPTS_REF:-}" ]; then
            o_cmd="$o_cmd PYMOLSCRIPTS_REF=$(quote "$PYMOLSCRIPTS_REF")"
        fi
        if [ -n "${PYMOLSCRIPTS_BASE_URL:-}" ]; then
            o_cmd="$o_cmd PYMOLSCRIPTS_BASE_URL=$(quote "$PYMOLSCRIPTS_BASE_URL")"
        fi
        o_cmd="$o_cmd sh -s -- --force"
    fi
    [ "$prefix_set" = 0 ] || o_cmd="$o_cmd --prefix $(quote "$dir_shown")"
    printf '%s\n' "$o_cmd"
}

# At most 10 lines, so that a caller keeping the last 20 lines keeps it all.
report_edits() {
    printf '%s: stopped: a local edit would be overwritten; nothing was changed.\n' "$prog"
    for name in .pymolrc .pymolrc.py; do
        load "$name"
        [ "$action" = edited ] || continue
        if [ "$link" = 1 ]; then
            printf '  edited: %s (a symlink; --force replaces it, not its target)\n' "$dir/$name"
        else
            printf '  edited: %s\n' "$dir/$name"
        fi
        if [ -f "$record_dir/$name" ] && command -v diff >/dev/null 2>&1; then
            printf '    see the edit: diff -u %s %s\n' \
                "$(quote "$record_dir/$name")" "$(quote "$dir/$name")"
        elif [ -n "$stage" ]; then
            printf '    compare with: %s\n' "$base_url/$ref/configs/$name"
        else
            printf '    compare with: %s\n' "$src/$name"
        fi
    done
    printf 'To keep an edit, commit it to configs/ in PyMOLScripts (the next run then\n'
    printf 'finds it current), or move host-only settings to %s,\n' "$dir/.pymolrc.local.pml"
    printf 'which PyMOL runs after both files and this installer never touches.\n'
    printf 'To overwrite instead, keeping each edited file as <name>.local-<time>:\n'
    printf '  %s\n' "$(override_command)"
}

if [ "$edited" = 1 ] && [ "$force" = 0 ]; then
    report_edits >&2
    exit 3
fi
if [ "$needed" = 0 ]; then
    say ".pymolrc and .pymolrc.py in $dir_shown are already current"
    exit 0
fi

# --- write -------------------------------------------------------------------

# The record directory comes first, so that a broken state directory stops
# the run before anything in DIR changes.
err=$(umask 077 && mkdir -p "$record_dir" 2>&1) || die "cannot create $record_dir: $err"

# backup NAME: keep the edited DIR/NAME as a free DIR/NAME.local-<time>, and
# set b_path to that name. Another user who can write to DIR could plant a
# link at the name once it was found free, which a copy would follow; so the
# copy is made in the private work directory, inside DIR and so on its file
# system as the staged files are, and then hard-linked to the name: link(2)
# neither follows nor replaces an entry that took the name meanwhile.
backup() {
    for tool in cp date ln; do
        command -v "$tool" >/dev/null 2>&1 || die "$tool not found; it is needed for --force"
    done
    b_copy=$work/$1.local
    err=$(cp -p "$dir/$1" "$b_copy" 2>&1) || die "cannot copy $dir/$1 to $b_copy: $err"
    b_base=$dir/$1.local-$(date +%Y%m%d%H%M%S) || die "date failed"
    b_path=$b_base
    b_n=0
    while :; do
        if [ ! -e "$b_path" ] && [ ! -L "$b_path" ]; then
            if err=$(ln "$b_copy" "$b_path" 2>&1); then
                # Unless a directory, or a link to one, took the name and ln
                # linked the copy inside it.
                [ -L "$b_path" ] || [ ! -f "$b_path" ] || return 0
            elif [ ! -e "$b_path" ] && [ ! -L "$b_path" ]; then
                die "cannot keep $dir/$1 as $b_path: $err; --force needs hard links in $dir_shown to keep edited files, so nothing was replaced"
            fi
        fi
        b_n=$((b_n + 1))
        b_path=$b_base-$b_n
    done
}

# unchanged NAME: DIR/NAME still holds what classify found there.
unchanged() {
    if [ "$action" = install ]; then
        [ ! -e "$dir/$1" ]
    else
        [ -e "$dir/$1" ] && [ "$(dump "$dir/$1" 2>/dev/null)" = "$found" ]
    fi
}

wrote=0
if [ "$writes" = 1 ]; then
    if [ ! -d "$dir_shown" ]; then
        err=$(mkdir -p "$dir_shown" 2>&1) || die "cannot create $dir_shown: $err"
    fi
    work=$(mktemp -d "$dir/.pymolscripts-install.XXXXXX" 2>/dev/null) ||
        die "cannot create a temporary directory in $dir_shown"
    for name in .pymolrc .pymolrc.py; do
        load "$name"
        [ "$action" != current ] || continue
        err=$(cat "$src/$name" 2>&1 >"$work/$name") || die "cannot write $work/$name: $err"
    done
    # Every backup before the first rename, so that a backup that fails
    # stops the run with nothing replaced.
    for name in .pymolrc .pymolrc.py; do
        load "$name"
        if [ "$action" = edited ] && [ "$link" = 0 ]; then
            backup "$name"
            kept=$b_path
            store "$name"
        fi
    done
    for name in .pymolrc .pymolrc.py; do
        load "$name"
        [ "$action" != current ] || continue
        dest=$dir/$name
        note=
        if [ "$action" = install ]; then
            verb=installed
        elif [ "$link" = 1 ]; then
            verb=updated note=" (replaced a symlink; its target is untouched)"
        elif [ "$action" = edited ]; then
            verb=updated note=" (kept the edited file as $kept)"
        else
            verb=updated
        fi
        # Narrow the window for an edit made since classify read the file.
        if ! unchanged "$name"; then
            if [ -e "$dest" ] && [ "$(dump "$dest" 2>/dev/null)" = "$(dump "$src/$name")" ]; then
                continue # another run installed it meanwhile
            fi
            die "$dest changed while installing; nothing more was changed, rerun to check it again"
        fi
        [ ! -d "$dest" ] || die "$dest is a directory or a link to one; remove it and rerun"
        err=$(mv -f "$work/$name" "$dest" 2>&1) || die "cannot rename $work/$name to $dest: $err"
        say "$verb $dest$note"
        wrote=1
    done
fi

# Records last: a run that stops before here leaves files the next run finds
# current, never a record newer than the file it describes.
for name in .pymolrc .pymolrc.py; do
    load "$name"
    [ "$stale" = 1 ] || continue
    partial=$(mktemp "$record_dir/.record.XXXXXX" 2>/dev/null) ||
        die "cannot write a record in $record_dir; the next run writes it"
    err=$(cat "$src/$name" 2>&1 >"$partial") || die "cannot write $partial: $err"
    err=$(mv -f "$partial" "$record_dir/$name" 2>&1) ||
        die "cannot rename $partial to $record_dir/$name: $err"
    partial=
done

[ "$wrote" = 1 ] || say ".pymolrc and .pymolrc.py in $dir_shown are already current"
exit 0
