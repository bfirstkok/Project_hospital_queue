#!/usr/bin/env bash
set -uo pipefail

readonly APP_DIR="${HOSPITAL_APP_DIR:-/opt/hospital}"
readonly BASE_URL="${HOSPITAL_BASE_URL:-https://hospital.bfirstkok.me}"
readonly DOMAIN="${HOSPITAL_DOMAIN:-hospital.bfirstkok.me}"
readonly DISK_WARN_PERCENT="${HOSPITAL_DISK_WARN_PERCENT:-85}"
readonly CERT_WARN_DAYS="${HOSPITAL_CERT_WARN_DAYS:-21}"
readonly LOG_WARN_MB="${HOSPITAL_LOG_WARN_MB:-500}"
readonly STATE_DIR="$APP_DIR/.tmp/monitor"
readonly ALERT_LOG="$STATE_DIR/alerts.log"
readonly STATE_FILE="$STATE_DIR/state"
readonly LOCK_FILE="$STATE_DIR/lock"

mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR"
exec 9>"$LOCK_FILE"
flock -n 9 || exit 0

now_epoch="$(date +%s)"
since_epoch=$((now_epoch - 360))
if [[ -r "$STATE_FILE" ]]; then
    stored_since="$(awk -F= '$1 == "last_epoch" {print $2}' "$STATE_FILE" 2>/dev/null || true)"
    [[ "$stored_since" =~ ^[0-9]+$ ]] && since_epoch="$stored_since"
fi
since_rfc3339="$(date -u -d "@$since_epoch" '+%Y-%m-%dT%H:%M:%SZ')"

alerts=()

add_alert() {
    alerts+=("$1")
}

cd "$APP_DIR" || exit 1

for container in hospital-db hospital-web hospital-caddy; do
    if ! docker inspect "$container" >/dev/null 2>&1; then
        add_alert "$container is missing"
        continue
    fi

    state="$(docker inspect -f '{{.State.Status}}' "$container")"
    [[ "$state" == running ]] || add_alert "$container state is $state"

    health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container")"
    [[ "$health" == unhealthy ]] && add_alert "$container is unhealthy"

    restart_count="$(docker inspect -f '{{.RestartCount}}' "$container")"
    previous_restart="$(awk -F= -v key="restart_$container" '$1 == key {print $2}' "$STATE_FILE" 2>/dev/null || true)"
    if [[ "$previous_restart" =~ ^[0-9]+$ ]] && (( restart_count > previous_restart )); then
        add_alert "$container restarted $((restart_count - previous_restart)) time(s)"
    fi
    printf -v "restart_${container//-/_}" '%s' "$restart_count"
done

http_code="$(curl -sS --connect-timeout 10 --max-time 20 -o /dev/null -w '%{http_code}' "$BASE_URL/" 2>/dev/null || printf '000')"
[[ "$http_code" =~ ^[23] ]] || add_alert "$BASE_URL health request returned HTTP $http_code"

web_5xx="$(docker compose logs --since "$since_rfc3339" --no-color web 2>/dev/null | grep -Ec '" [5][0-9][0-9] ' || true)"
(( web_5xx > 0 )) && add_alert "$web_5xx HTTP 5xx response(s) since $since_rfc3339"

db_errors="$(docker compose logs --since "$since_rfc3339" --no-color db 2>/dev/null | grep -Ec 'ERROR|FATAL|PANIC' || true)"
(( db_errors > 0 )) && add_alert "$db_errors database error(s) since $since_rfc3339"

cert_seconds=$((CERT_WARN_DAYS * 86400))
if ! timeout 20 openssl s_client -servername "$DOMAIN" -connect "$DOMAIN:443" </dev/null 2>/dev/null \
    | openssl x509 -noout -checkend "$cert_seconds" >/dev/null 2>&1; then
    add_alert "TLS certificate for $DOMAIN expires within $CERT_WARN_DAYS days or could not be checked"
fi

disk_percent="$(df -P / | awk 'NR == 2 {gsub(/%/, "", $5); print $5}')"
if [[ "$disk_percent" =~ ^[0-9]+$ ]] && (( disk_percent >= DISK_WARN_PERCENT )); then
    add_alert "root disk usage is ${disk_percent}% (threshold ${DISK_WARN_PERCENT}%)"
fi

docker_root="$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || true)"
oversized_logs=0
if [[ -n "$docker_root" ]]; then
    oversized_logs="$(docker run --rm --entrypoint sh \
        -v "$docker_root/containers:/docker-logs:ro" caddy:2.10-alpine \
        -c "find /docker-logs -type f -name '*-json.log' -size +${LOG_WARN_MB}M -print 2>/dev/null | wc -l" 2>/dev/null || printf '0')"
fi
if [[ "$oversized_logs" =~ ^[0-9]+$ ]] && (( oversized_logs > 0 )); then
    add_alert "$oversized_logs Docker log file(s) exceed ${LOG_WARN_MB} MB"
fi

{
    printf 'last_epoch=%s\n' "$now_epoch"
    for container in hospital-db hospital-web hospital-caddy; do
        key="restart_${container//-/_}"
        value="${!key:-0}"
        printf 'restart_%s=%s\n' "$container" "$value"
    done
} >"$STATE_FILE"
chmod 600 "$STATE_FILE"

if (( ${#alerts[@]} == 0 )); then
    exit 0
fi

message="hospital monitor: $(IFS='; '; echo "${alerts[*]}")"
timestamp="$(date -Is)"
printf '%s %s\n' "$timestamp" "$message" >>"$ALERT_LOG"
chmod 600 "$ALERT_LOG"
logger -p user.err -t hospital-monitor -- "$message"

# Optional external delivery. Keep the URL outside the repository and never print it.
webhook_file="$HOME/.config/hospital-monitor/webhook-url"
if [[ -s "$webhook_file" ]]; then
    webhook_url="$(head -n 1 "$webhook_file")"
    json_message="$(printf '%s' "$message" | sed 's/\\/\\\\/g; s/"/\\"/g')"
    curl -sS --connect-timeout 10 --max-time 20 -o /dev/null \
        -H 'Content-Type: application/json' \
        --data "{\"text\":\"$json_message\",\"content\":\"$json_message\"}" \
        "$webhook_url" || true
fi

printf '%s\n' "$message" >&2
exit 1
