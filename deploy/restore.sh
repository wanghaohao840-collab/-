#!/bin/sh
set -eu

if [ "$#" -lt 1 ]; then
    echo "Usage: deploy/restore.sh <archive> [--env-file path]" >&2
    exit 2
fi

archive="$1"
shift
env_file="${DEPLOY_ENV_FILE:-deploy/.env}"
while [ "$#" -gt 0 ]; do
    case "$1" in
        --env-file)
            [ "$#" -ge 2 ] || {
                echo "--env-file requires a path" >&2
                exit 2
            }
            env_file="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 2
            ;;
    esac
done

[ -f "$env_file" ] || {
    echo "Environment file not found: $env_file" >&2
    exit 1
}
[ -f "$archive" ] || {
    echo "Archive not found: $archive" >&2
    exit 1
}

read_env_value() {
    key="$1"
    carriage_return="$(printf '\r')"
    while IFS= read -r line; do
        line="${line%"$carriage_return"}"
        case "$line" in
            "$key="*)
                value="${line#*=}"
                case "$value" in
                    \"*\") value="${value#\"}"; value="${value%\"}" ;;
                    \'*\') value="${value#\'}"; value="${value%\'}" ;;
                esac
                printf '%s\n' "$value"
                return
                ;;
        esac
    done < "$env_file"
}

data_root="${DEPLOY_DATA_ROOT:-$(read_env_value DEPLOY_DATA_ROOT)}"
data_root="${data_root:-./deploy-data}"
case "$data_root" in
    ""|/|.)
        echo "Refusing to restore into an unsafe data root: $data_root" >&2
        exit 1
        ;;
esac
[ -d "$data_root" ] || {
    echo "Current data root not found: $data_root" >&2
    exit 1
}

archive_dir="$(cd "$(dirname "$archive")" && pwd -P)"
archive_name="$(basename "$archive")"
archive="$archive_dir/$archive_name"
checksum="$archive.sha256"
[ -f "$checksum" ] || {
    echo "Checksum not found: $checksum" >&2
    exit 1
}
(
    cd "$archive_dir"
    sha256sum -c "$archive_name.sha256"
)

if ! tar -tzf "$archive" | awk '
    $0 ~ /^\// || $0 ~ /(^|\/)\.\.(\/|$)/ { bad=1 }
    $0 != "." && $0 != "./" && $0 !~ /^\.\// { bad=1 }
    END { exit bad }
'; then
    echo "Archive contains an unsafe path" >&2
    exit 1
fi
if tar -tvzf "$archive" | awk '$1 ~ /^[lh]/ { found=1 } END { exit !found }'; then
    echo "Archive contains a symbolic or hard link" >&2
    exit 1
fi

data_abs="$(cd "$data_root" && pwd -P)"
parent="$(dirname "$data_abs")"
staging="$(mktemp -d "$parent/.assistant-restore.XXXXXX")"
rollback="${data_abs}.rollback-$(date -u +%Y%m%dT%H%M%SZ)"
failed_restore="${data_abs}.failed-$(date -u +%Y%m%dT%H%M%SZ)"
running_services="$(
    docker compose --env-file "$env_file" ps --services --status running
)"
services_stopped=0
swap_done=0

start_services() {
    if [ -n "$running_services" ]; then
        # shellcheck disable=SC2086
        docker compose --env-file "$env_file" start $running_services >/dev/null
    fi
}

wait_for_health() {
    deadline="$(( $(date +%s) + 180 ))"
    while [ "$(date +%s)" -lt "$deadline" ]; do
        all_healthy=1
        for service in $running_services; do
            container_id="$(
                docker compose --env-file "$env_file" ps -q "$service"
            )"
            state="$(
                docker inspect \
                    --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' \
                    "$container_id"
            )"
            [ "$state" = "running healthy" ] || all_healthy=0
        done
        [ "$all_healthy" -eq 1 ] && return 0
        sleep 2
    done
    return 1
}

restore_on_failure() {
    status="$?"
    trap - EXIT HUP INT TERM
    if [ "$status" -ne 0 ]; then
        if [ "$swap_done" -eq 1 ]; then
            docker compose --env-file "$env_file" stop >/dev/null 2>&1 || true
            if [ -d "$data_abs" ]; then
                mv "$data_abs" "$failed_restore"
            fi
            if [ -d "$rollback" ]; then
                mv "$rollback" "$data_abs"
            fi
            start_services || true
        elif [ "$services_stopped" -eq 1 ]; then
            start_services || true
        fi
        if [ -n "${staging:-}" ] && [ -d "$staging" ]; then
            echo "Restore staging retained for inspection: $staging" >&2
        fi
    fi
    exit "$status"
}
trap restore_on_failure EXIT HUP INT TERM

tar --no-same-owner -xzf "$archive" -C "$staging"
[ -d "$staging/app" ] || {
    echo "Archive is missing the app data directory" >&2
    exit 1
}
[ -d "$staging/qdrant" ] || {
    echo "Archive is missing the qdrant data directory" >&2
    exit 1
}
[ ! -e "$rollback" ] || {
    echo "Rollback path already exists: $rollback" >&2
    exit 1
}

docker compose --env-file "$env_file" stop >/dev/null
services_stopped=1
mv "$data_abs" "$rollback"
swap_done=1
mv "$staging" "$data_abs"
staging=""

start_services
wait_for_health
swap_done=0
services_stopped=0
printf 'Restore completed. Rollback kept at: %s\n' "$rollback"
