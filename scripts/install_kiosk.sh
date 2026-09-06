#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -eq 0 ]]; then
    echo "Execute este script como o utilizador da sessão gráfica, sem sudo." >&2
    exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LAUNCHER="$SCRIPT_DIR/start_kiosk.sh"
AUTOSTART_DIR="$HOME/.config/autostart"
DESKTOP_FILE="$AUTOSTART_DIR/tank-control-kiosk.desktop"
URL="${1:-http://191.188.127.31:5000}"

if ! command -v chromium >/dev/null 2>&1 && ! command -v chromium-browser >/dev/null 2>&1; then
    echo "Chromium não está instalado. Instale-o antes de continuar." >&2
    exit 1
fi

chmod +x "$LAUNCHER"
mkdir -p "$AUTOSTART_DIR"

cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Tank Control Kiosk
Comment=Abre o painel Tank Control em modo quiosque
Exec=env TANK_CONTROL_KIOSK_URL=$URL $LAUNCHER
Terminal=false
X-GNOME-Autostart-enabled=true
EOF

echo "Quiosque instalado para $URL"
echo "Ficheiro criado: $DESKTOP_FILE"
echo "Reinicie o Pi para testar: sudo reboot"