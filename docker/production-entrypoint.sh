#!/bin/sh

set -eu

: "${CARDINE_OWNER_PASSWORD_HASH:?CARDINE_OWNER_PASSWORD_HASH is required}"
: "${CARDINE_PUBLIC_ORIGIN:?CARDINE_PUBLIC_ORIGIN is required}"
: "${CARDINE_COURSE_ID:?CARDINE_COURSE_ID is required}"
: "${CARDINE_SESSION_ID:?CARDINE_SESSION_ID is required}"

repository="${CARDINE_REPOSITORY:-/cardine/repository}"
bind_host="${CARDINE_BIND_HOST:-0.0.0.0}"
port="${PORT:-8080}"

if [ ! -d "$repository" ]; then
  echo "Cardine repository does not exist: $repository" >&2
  exit 64
fi

exec study-agent-shell-web \
  --host "$bind_host" \
  --port "$port" \
  --repository "$repository" \
  --course-id "$CARDINE_COURSE_ID" \
  --session-id "$CARDINE_SESSION_ID" \
  --private \
  --production
