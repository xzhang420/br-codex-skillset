#!/usr/bin/env bash

set -Eeuo pipefail

usage() {
    cat <<'EOF'
Usage:
  archive_tpx3_experiments.sh --root PATH [options] -- EXPERIMENT [EXPERIMENT ...]

Default mode is a read-only audit.

Options:
  --root PATH                 Absolute tpx3Files directory (required)
  --execute                   Create archives and remove verified sources
  --threads N                 pigz threads (default: 16)
  --min-age-minutes N         Refuse files newer than N minutes (default: 60)
  --max-source-bytes N        Refuse a source larger than N bytes (default: 1000000000000)
  --log PATH                  Execution log (default: proposal-root/compression_tpx3_experiments.log)
  --replace-partials          Remove pre-existing .tar.gz.partial files during execution
  --help                      Show this help
EOF
}

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

timestamp() {
    date --iso-8601=seconds
}

log() {
    printf '[%s] %s\n' "$(timestamp)" "$*"
}

is_positive_integer() {
    [[ $1 =~ ^[1-9][0-9]*$ ]]
}

metrics() {
    local source_dir=$1
    local entries files bytes
    entries=$(find "$source_dir" -xdev -printf '.' | wc -c)
    files=$(find "$source_dir" -xdev -type f -printf '.' | wc -c)
    bytes=$(find "$source_dir" -xdev -type f -printf '%s\n' |
        awk '{ total += $1 } END { printf "%.0f", total + 0 }')
    printf '%s %s %s\n' "$entries" "$files" "$bytes"
}

root=''
log_path=''
threads=16
min_age_minutes=60
max_source_bytes=1000000000000
execute_mode=0
replace_partials=0
experiments=()

while (($#)); do
    case $1 in
        --root)
            (($# >= 2)) || die '--root requires a value'
            root=$2
            shift 2
            ;;
        --execute)
            execute_mode=1
            shift
            ;;
        --threads)
            (($# >= 2)) || die '--threads requires a value'
            threads=$2
            shift 2
            ;;
        --min-age-minutes)
            (($# >= 2)) || die '--min-age-minutes requires a value'
            min_age_minutes=$2
            shift 2
            ;;
        --max-source-bytes)
            (($# >= 2)) || die '--max-source-bytes requires a value'
            max_source_bytes=$2
            shift 2
            ;;
        --log)
            (($# >= 2)) || die '--log requires a value'
            log_path=$2
            shift 2
            ;;
        --replace-partials)
            replace_partials=1
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        --)
            shift
            experiments=("$@")
            break
            ;;
        *)
            die "unknown argument: $1"
            ;;
    esac
done

[[ -n $root ]] || die '--root is required'
((${#experiments[@]} > 0)) || die 'at least one exact experiment name is required after --'
is_positive_integer "$threads" || die '--threads must be a positive integer'
is_positive_integer "$min_age_minutes" || die '--min-age-minutes must be a positive integer'
is_positive_integer "$max_source_bytes" || die '--max-source-bytes must be a positive integer'

[[ $root = /* ]] || die '--root must be an absolute path'
[[ -d $root ]] || die "root does not exist: $root"
[[ ! -L $root ]] || die "refusing symlinked root: $root"
root=$(realpath -e -- "$root")
[[ ${root##*/} == 'tpx3Files' ]] || die "root basename must be tpx3Files: $root"

command -v find >/dev/null || die 'find is required'
command -v tar >/dev/null || die 'tar is required'
command -v pigz >/dev/null || die 'pigz is required'
command -v flock >/dev/null || die 'flock is required'

declare -A seen=()
planned_sources=()
for experiment_name in "${experiments[@]}"; do
    [[ -n $experiment_name && $experiment_name != '.' && $experiment_name != '..' ]] ||
        die 'experiment names must be non-empty exact directory names'
    [[ $experiment_name != */* ]] || die "experiment name must not contain '/': $experiment_name"
    case $experiment_name in
        *'*'*|*'?'*|*'['*) die "globs are not allowed: $experiment_name" ;;
    esac
    [[ -z ${seen[$experiment_name]+x} ]] || die "duplicate experiment: $experiment_name"
    seen[$experiment_name]=1

    source_dir="$root/$experiment_name"
    [[ -d $source_dir ]] || die "source directory is missing: $source_dir"
    [[ ! -L $source_dir ]] || die "refusing symlinked source: $source_dir"
    symlinks=$(find "$source_dir" -xdev -type l -printf '.' | wc -c)
    [[ $symlinks -eq 0 ]] || die "refusing source containing symlinks: $source_dir"
    planned_sources+=("$source_dir")
done

audit_failed=0
largest_source=0
total_source=0
printf 'MODE=%s\n' "$([[ $execute_mode -eq 1 ]] && printf execute || printf audit)"
printf 'ROOT=%s\n' "$root"
printf 'experiment\tentries\tfiles\ttpx3_files\tbytes\trecent_files\tarchive_state\n'
for experiment_name in "${experiments[@]}"; do
    source_dir="$root/$experiment_name"
    read -r entries files bytes < <(metrics "$source_dir")
    tpx3_files=$(find "$source_dir" -xdev -type f -name '*.tpx3' -printf '.' | wc -c)
    recent_files=$(find "$source_dir" -xdev -type f -mmin "-$min_age_minutes" -printf '.' | wc -c)
    final_archive="$root/$experiment_name.tar.gz"
    partial_archive="$final_archive.partial"
    archive_state='clear'
    [[ ! -e $final_archive ]] || archive_state='final_exists'
    [[ ! -e $partial_archive ]] || archive_state='partial_exists'
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$experiment_name" "$entries" "$files" "$tpx3_files" "$bytes" "$recent_files" "$archive_state"

    ((bytes > largest_source)) && largest_source=$bytes
    total_source=$((total_source + bytes))
    ((files > 0)) || { printf 'REFUSE: empty source: %s\n' "$experiment_name" >&2; audit_failed=1; }
    ((bytes <= max_source_bytes)) || { printf 'REFUSE: source exceeds max bytes: %s bytes=%s max=%s\n' "$experiment_name" "$bytes" "$max_source_bytes" >&2; audit_failed=1; }
    ((recent_files == 0)) || { printf 'REFUSE: recently modified source: %s files=%s threshold_minutes=%s\n' "$experiment_name" "$recent_files" "$min_age_minutes" >&2; audit_failed=1; }
    [[ ! -e $final_archive ]] || { printf 'REFUSE: final archive exists: %s\n' "$final_archive" >&2; audit_failed=1; }
    if [[ -e $partial_archive && $replace_partials -eq 0 ]]; then
        printf 'REFUSE: partial archive exists; inspect it or use --replace-partials: %s\n' "$partial_archive" >&2
        audit_failed=1
    fi
done

available_bytes=$(df -B1 --output=avail "$root" | tail -1 | tr -d ' ')
required_peak=$((largest_source + largest_source / 100 + 1073741824))
printf 'TOTAL_SOURCE_BYTES=%s\n' "$total_source"
printf 'LARGEST_SOURCE_BYTES=%s\n' "$largest_source"
printf 'AVAILABLE_BYTES=%s\n' "$available_bytes"
printf 'REQUIRED_PEAK_BYTES=%s\n' "$required_peak"
if ((available_bytes < required_peak)); then
    printf 'REFUSE: insufficient free space for the largest sequential archive\n' >&2
    audit_failed=1
fi

if ((execute_mode == 0)); then
    ((audit_failed == 0)) || exit 2
    printf 'AUDIT_OK\n'
    exit 0
fi
((audit_failed == 0)) || die 'preflight audit failed; no source was changed'

if [[ -z $log_path ]]; then
    proposal_root=$(realpath -m -- "$root/../../..")
    log_path="$proposal_root/compression_tpx3_experiments.log"
fi
[[ $log_path = /* ]] || die '--log must be an absolute path'
mkdir -p -- "${log_path%/*}"

lock_id=$(printf '%s' "$root" | sha256sum | awk '{print substr($1,1,16)}')
lock_path="/tmp/godzilla_archive_tpx3_${lock_id}.lock"
exec 9>"$lock_path"
flock -n 9 || die "another archive process holds the root lock: $lock_path"

exec >>"$log_path" 2>&1
log "START: root=$root experiments=${#experiments[@]} source_bytes=$total_source threads=$threads"

completed=0
for experiment_name in "${experiments[@]}"; do
    source_dir="$root/$experiment_name"
    final_archive="$root/$experiment_name.tar.gz"
    partial_archive="$final_archive.partial"

    if [[ -e $partial_archive ]]; then
        log "REMOVE INCOMPLETE: $partial_archive"
        rm -f -- "$partial_archive"
    fi

    read -r source_entries source_files source_bytes < <(metrics "$source_dir")
    started_epoch=$(date +%s)
    log "ARCHIVE START: $experiment_name entries=$source_entries files=$source_files bytes=$source_bytes"

    tar --one-file-system -C "$root" -cf - -- "$experiment_name" |
        pigz -6 -p "$threads" >"$partial_archive"
    sync -f "$partial_archive"
    archive_bytes=$(stat -c '%s' "$partial_archive")
    log "VERIFY START: $experiment_name archive_bytes=$archive_bytes"

    archive_entries=$(tar -tzf "$partial_archive" | wc -l)
    [[ $archive_entries -eq $source_entries ]] ||
        die "entry-count mismatch for $experiment_name source=$source_entries archive=$archive_entries"

    read -r current_entries current_files current_bytes < <(metrics "$source_dir")
    [[ $current_entries -eq $source_entries &&
       $current_files -eq $source_files &&
       $current_bytes -eq $source_bytes ]] ||
        die "source changed during archiving for $experiment_name"

    log "VERIFY COMPLETE: $experiment_name entries=$archive_entries"
    mv -T -- "$partial_archive" "$final_archive"
    sync -f "$final_archive"

    rm -rf -- "$source_dir"
    [[ ! -e $source_dir ]] || die "source remains after removal: $source_dir"

    finished_epoch=$(date +%s)
    elapsed=$((finished_epoch - started_epoch))
    completed=$((completed + 1))
    log "ARCHIVE COMPLETE: $experiment_name archive_bytes=$archive_bytes elapsed_seconds=$elapsed completed=$completed"
done

log "ALL COMPLETE: archived experiment folders=$completed"
