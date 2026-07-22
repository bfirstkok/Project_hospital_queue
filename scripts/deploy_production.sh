#!/usr/bin/env bash
set -Eeuo pipefail

readonly APP_DIR="${HOSPITAL_APP_DIR:-/opt/hospital}"
readonly BRANCH="${HOSPITAL_BRANCH:-main}"
readonly BASE_URL="${HOSPITAL_BASE_URL:-https://hospital.bfirstkok.me}"

log() {
    printf '[deploy] %s\n' "$*"
}

fail() {
    printf '[deploy] ERROR: %s\n' "$*" >&2
    exit 1
}

cd "$APP_DIR"

log "Checking repository state"
[[ "$(git branch --show-current)" == "$BRANCH" ]] || fail "expected branch $BRANCH"
[[ -z "$(git status --porcelain)" ]] || fail "working tree is not clean; commit or resolve local files first"

log "Pulling origin/$BRANCH with fast-forward only"
git pull --ff-only origin "$BRANCH"

log "Building a temporary image and running patient tests against the pulled source"
docker compose run --rm --build web python manage.py test patients --verbosity 1

log "Building and starting the production services"
docker compose up -d --build

log "Waiting for database and web health checks"
deadline=$((SECONDS + 180))
while (( SECONDS < deadline )); do
    db_health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' hospital-db 2>/dev/null || true)"
    web_health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' hospital-web 2>/dev/null || true)"
    caddy_state="$(docker inspect -f '{{.State.Status}}' hospital-caddy 2>/dev/null || true)"
    if [[ "$db_health" == healthy && "$web_health" == healthy && "$caddy_state" == running ]]; then
        break
    fi
    sleep 5
done

[[ "$db_health" == healthy ]] || fail "database is not healthy (state: ${db_health:-missing})"
[[ "$web_health" == healthy ]] || fail "web is not healthy (state: ${web_health:-missing})"
[[ "$caddy_state" == running ]] || fail "caddy is not running (state: ${caddy_state:-missing})"

log "Running API smoke tests without patient data"
login_code="$(curl -sS --connect-timeout 10 --max-time 20 -o /dev/null -w '%{http_code}' \
    -X POST -H 'Content-Type: application/json' --data '{}' "$BASE_URL/api/patient/login/")"
me_code="$(curl -sS --connect-timeout 10 --max-time 20 -o /dev/null -w '%{http_code}' \
    "$BASE_URL/api/patient/me/")"
queue_code="$(curl -sS --connect-timeout 10 --max-time 20 -o /dev/null -w '%{http_code}' \
    "$BASE_URL/api/patient/queue/")"

[[ "$login_code" == 400 ]] || fail "empty login returned HTTP $login_code (expected 400)"
[[ "$me_code" == 401 ]] || fail "unauthenticated me returned HTTP $me_code (expected 401)"
[[ "$queue_code" == 401 ]] || fail "unauthenticated queue returned HTTP $queue_code (expected 401)"

docker compose ps
log "Deployment and smoke tests completed successfully"
