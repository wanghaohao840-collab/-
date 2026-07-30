#!/bin/sh
set -eu

env_file="${DEPLOY_ENV_FILE:-deploy/.env}"
backup_root="${DEPLOY_BACKUP_ROOT:-./backups}"

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
        --backup-root)
            [ "$#" -ge 2 ] || {
                echo "--backup-root requires a path" >&2
                exit 2
            }
            backup_root="$2"
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

[ -d "$data_root" ] || {
    echo "DEPLOY_DATA_ROOT must exist before backup: $data_root" >&2
    exit 1
}

mkdir -p "$backup_root"
data_abs="$(cd "$data_root" && pwd -P)"
backup_abs="$(cd "$backup_root" && pwd -P)"
case "$backup_abs/" in
    "$data_abs/"*)
        echo "Backup root must be outside DEPLOY_DATA_ROOT" >&2
        exit 1
        ;;
esac

running_services="$(
    docker compose --env-file "$env_file" ps --services --status running
)"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive_name="assistant-$timestamp.tar.gz"
archive="$backup_abs/$archive_name"
metadata="$archive.meta"

restart_services() {
    status="$?"
    trap - EXIT HUP INT TERM
    if [ -n "$running_services" ]; then
        # Compose service names cannot contain shell whitespace.
        # shellcheck disable=SC2086
        docker compose --env-file "$env_file" start $running_services >/dev/null
    fi
    exit "$status"
}
trap restart_services EXIT HUP INT TERM

docker compose --env-file "$env_file" stop >/dev/null
tar -C "$data_abs" -czf "$archive" .
(
    cd "$backup_abs"
    sha256sum "$archive_name" > "$archive_name.sha256"
)
{
    printf 'created_at=%s\n' "$timestamp"
    printf 'data_root=%s\n' "$data_abs"
    printf 'running_services=%s\n' "$(printf '%s' "$running_services" | tr '\n' ' ')"
    printf 'git_revision=%s\n' "$(git rev-parse HEAD 2>/dev/null || printf unknown)"
    printf 'images='
    docker compose --env-file "$env_file" images --format json 2>/dev/null \
        | tr '\n' ' '
    printf '\n'
} > "$metadata"
printf 'Backup created: %s\n' "$archive"
