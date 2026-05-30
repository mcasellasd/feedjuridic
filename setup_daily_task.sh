#!/bin/bash
# ════════════════════════════════════════════════════════════════════════════
# setup_daily_task.sh
# Configuració de tasca programada diària per executar scraper.py
# Actualitza els resums de cowork cada dia a les 09:00 UTC+2 (07:00 UTC)
# ════════════════════════════════════════════════════════════════════════════

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${PROJECT_DIR}/.venv"
PYTHON="${VENV_DIR}/bin/python"

# Verificar que existeix l'entorn virtual
if [ ! -f "$PYTHON" ]; then
    echo "❌ Entorn virtual no trobat a: $VENV_DIR"
    echo "   Crea'l amb: python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

# ────────────────────────────────────────────────────────────────────────────
# 1. OPCIÓ: cron (Linux/macOS)
# ────────────────────────────────────────────────────────────────────────────
setup_cron() {
    echo "📋 Configurant cron job..."
    
    # Cron entry: cada dia a les 09:00 (hora local)
    # Sintaxi cron: min hora dia mes dia_setmana
    CRON_ENTRY="0 9 * * * cd ${PROJECT_DIR} && ${PYTHON} scraper.py >> ${PROJECT_DIR}/logs/cron_scraper.log 2>&1"
    
    # Crear carpeta de logs
    mkdir -p "${PROJECT_DIR}/logs"
    touch "${PROJECT_DIR}/logs/cron_scraper.log"
    
    # Afegir/actualitzar cron entry
    (crontab -l 2>/dev/null | grep -v "scraper.py"; echo "$CRON_ENTRY") | crontab -
    
    if [ $? -eq 0 ]; then
        echo "✅ Cron job afegit correctament."
        echo "   Executarà scraper.py cada dia a les 09:00"
        echo "   Logs: ${PROJECT_DIR}/logs/cron_scraper.log"
    else
        echo "❌ Error al configurar cron. Prova manualment:"
        echo "   crontab -e"
        echo "   Afegeix: $CRON_ENTRY"
    fi
}

# ────────────────────────────────────────────────────────────────────────────
# 2. OPCIÓ: launchd (macOS nàtiu — preferit)
# ────────────────────────────────────────────────────────────────────────────
setup_launchd() {
    echo "🍎 Configurant launchd (macOS)..."
    
    PLIST_PATH="${HOME}/Library/LaunchAgents/com.feedjuridic.scraper.plist"
    PLIST_CONTENT="<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">
<plist version=\"1.0\">
<dict>
    <key>Label</key>
    <string>com.feedjuridic.scraper</string>
    
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>${PROJECT_DIR}/scraper.py</string>
    </array>
    
    <key>WorkingDirectory</key>
    <string>${PROJECT_DIR}</string>
    
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>9</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    
    <key>StandardOutPath</key>
    <string>${PROJECT_DIR}/logs/launchd_scraper.log</string>
    
    <key>StandardErrorPath</key>
    <string>${PROJECT_DIR}/logs/launchd_scraper_error.log</string>
    
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
</dict>
</plist>"
    
    # Crear carpeta de logs
    mkdir -p "${PROJECT_DIR}/logs"
    
    # Escriure plist
    echo "$PLIST_CONTENT" > "$PLIST_PATH"
    chmod 644 "$PLIST_PATH"
    
    # Cargar el plist
    launchctl load "$PLIST_PATH" 2>/dev/null
    
    if [ $? -eq 0 ]; then
        echo "✅ launchd agent instal·lat correctament."
        echo "   Camí: $PLIST_PATH"
        echo "   S'executarà cada dia a les 09:00"
        echo "   Per desactivar: launchctl unload \"$PLIST_PATH\""
        echo "   Per activar de nou: launchctl load \"$PLIST_PATH\""
    else
        echo "❌ Error al cargar launchd agent."
        echo "   Prova manualment:"
        echo "   launchctl load \"$PLIST_PATH\""
    fi
}

# ────────────────────────────────────────────────────────────────────────────
# 3. OPCIÓ: systemd (Linux)
# ────────────────────────────────────────────────────────────────────────────
setup_systemd() {
    echo "🐧 Configurant systemd (Linux)..."
    
    SERVICE_NAME="feedjuridic-scraper"
    SYSTEMD_PATH="${HOME}/.config/systemd/user/${SERVICE_NAME}.service"
    TIMER_PATH="${HOME}/.config/systemd/user/${SERVICE_NAME}.timer"
    
    mkdir -p "${HOME}/.config/systemd/user"
    mkdir -p "${PROJECT_DIR}/logs"
    
    # Crear service unit
    cat > "$SYSTEMD_PATH" << EOF
[Unit]
Description=FeedJurídic Scraper — Actualització diària
After=network.target

[Service]
Type=oneshot
ExecStart=${PYTHON} ${PROJECT_DIR}/scraper.py
WorkingDirectory=${PROJECT_DIR}
StandardOutput=append:${PROJECT_DIR}/logs/systemd_scraper.log
StandardError=append:${PROJECT_DIR}/logs/systemd_scraper_error.log
EOF
    
    # Crear timer unit (cada dia a les 09:00)
    cat > "$TIMER_PATH" << EOF
[Unit]
Description=FeedJurídic Scraper — Temporitzador
Requires=${SERVICE_NAME}.service

[Timer]
OnCalendar=daily
OnCalendar=*-*-* 09:00:00
Persistent=true

[Install]
WantedBy=timers.target
EOF
    
    # Habilitar i iniciar
    systemctl --user daemon-reload
    systemctl --user enable "${SERVICE_NAME}.timer"
    systemctl --user start "${SERVICE_NAME}.timer"
    
    if [ $? -eq 0 ]; then
        echo "✅ systemd timer instal·lat correctament."
        echo "   Service: $SYSTEMD_PATH"
        echo "   Timer: $TIMER_PATH"
        echo "   Estat: $(systemctl --user status ${SERVICE_NAME}.timer)"
        echo "   Per parar: systemctl --user stop \"${SERVICE_NAME}.timer\""
    else
        echo "❌ Error al configurar systemd."
    fi
}

# ────────────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────────────

echo "════════════════════════════════════════════════════════════════════════"
echo "  FeedJurídic · Configuració de tasca programada diària"
echo "════════════════════════════════════════════════════════════════════════"
echo ""
echo "Trià una opció d'escalonament:"
echo "1) cron (Linux/macOS universal)"
echo "2) launchd (macOS preferit — funcionament més fiable)"
echo "3) systemd (Linux moderna)"
echo "4) Sortir"
echo ""

read -r -p "Opció [1-4]: " opcio

case $opcio in
    1) setup_cron ;;
    2) setup_launchd ;;
    3) setup_systemd ;;
    4) echo "Sortint..."; exit 0 ;;
    *) echo "❌ Opció no vàlida."; exit 1 ;;
esac

echo ""
echo "ℹ️  Comprova els logs a: ${PROJECT_DIR}/logs/"
echo "   Per provar manualment: cd ${PROJECT_DIR} && ${PYTHON} scraper.py"
echo ""
