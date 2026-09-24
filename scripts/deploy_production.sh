#!/usr/bin/env bash
set -Eeuo pipefail

readonly APP_DIR="${HOSPITAL_APP_DIR:-/opt/hospital}"
readonly BRANCH="${HOSPITAL_BRANCH:-main}"
readonly BASE_URL="${HOSPITAL_BASE_URL:-https://hospital.bfirstkok.me}"
readonly PATIENT_DIST_DIR="${HOSPITAL_PATIENT_DIST_DIR:-/opt/hospital-patient/dist}"

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

log "Building a temporary image, collecting static assets, and running application tests"
docker compose run --rm --build web /bin/sh -c \
    "python manage.py collectstatic --noinput && python manage.py check && python manage.py test accounts queues patients opd --verbosity 1"

log "Checking patient portal static export"
[[ -f "$PATIENT_DIST_DIR/index.html" ]] || fail "patient portal build missing: $PATIENT_DIST_DIR/index.html (run npm ci && npm run build in /opt/hospital-patient)"

log "Building and starting the production services"
docker compose up -d --build

# The patient portal build performs a clean export by deleting and recreating
# /opt/hospital-patient/dist. A long-running Caddy bind mount can remain pinned
# to the old directory inode and return 404 even though the new dist/index.html
# exists on the host. Recreate Caddy so the bind mount resolves the current
# export directory before running public smoke tests.
log "Refreshing Caddy patient portal bind mount"
docker compose up -d --force-recreate --no-deps caddy

docker exec hospital-caddy test -f /srv/patient/index.html \
    || fail "patient portal index is not visible inside Caddy at /srv/patient/index.html"

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
patient_portal_code="$(curl -sS --connect-timeout 10 --max-time 20 -o /dev/null -w '%{http_code}' \
    "$BASE_URL/patient/")"

[[ "$login_code" == 400 ]] || fail "empty login returned HTTP $login_code (expected 400)"
[[ "$me_code" == 401 ]] || fail "unauthenticated me returned HTTP $me_code (expected 401)"
[[ "$queue_code" == 401 ]] || fail "unauthenticated queue returned HTTP $queue_code (expected 401)"
[[ "$patient_portal_code" == 200 ]] || fail "patient portal returned HTTP $patient_portal_code (expected 200)"

docker compose ps
log "Deployment and smoke tests completed successfully"
