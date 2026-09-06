#!/usr/bin/env bash
set -u

URL="${TANK_CONTROL_KIOSK_URL:-http://191.188.127.31:5000}"

for _ in $(seq 1 60); do
    if curl --fail --silent --output /dev/null --max-time 2 "$URL"; then
        break
    fi
    sleep 2
done

BROWSER="$(command -v chromium || command -v chromium-browser || true)"
if [[ -z "$BROWSER" ]]; then
    logger -t tank-control-kiosk "Chromium não está instalado"
    exit 1
fi

exec "$BROWSER" \
    --kiosk \
    --noerrdialogs \
    --disable-infobars \
    --disable-session-crashed-bubble \
    --disable-features=Translate \
    "$URL"