#!/bin/bash
# NextEnergy MORGEN-rapport -> WhatsApp-groep "THUIS, Oostrandpark".
# Cron 16:00, no_agent. Het python-script wacht zelf op publicatie (rond 16:00)
# en drukt niets bij ontbrekende data; deze wrapper is dan stil (watchdog).
# Succes = geen stdout (geen dubbele Telegram-levering); fout = korte melding
# die de cron naar Telegram stuurt.
PY=/home/rkruit/.hermes/venvs/monitoring/bin/python3
REPORT_PY=/home/rkruit/.hermes/scripts/nextenergy_morgen_report.py
WAHA_SEND=/home/rkruit/.hermes/scripts/waha-send.py
ENV=/srv/sv01-secrets/waha.env
LOG=/home/rkruit/.hermes/logs/nextenergy-morgen.log
mkdir -p "$(dirname "$LOG")"

# Extra args worden doorgegeven aan het report (handig voor tests,
# bv. --day today --no-dedupe); NE_MORGEN_CHAT override voor testdoeleinden.
REPORT=$("$PY" "$REPORT_PY" "$@" 2>>"$LOG")
RC=$?
if [ $RC -ne 0 ]; then
  echo "⚠ NextEnergy morgenrapport faalde (exit $RC) — zie $LOG"
  exit 0
fi
if [ -z "$(printf '%s' "$REPORT" | tr -d '[:space:]')" ]; then
  echo "$(date '+%F %T') geen data (niet gepubliceerd) — niets verstuurd" >>"$LOG"
  exit 0
fi

CHAT=${NE_MORGEN_CHAT:-$(grep '^WAHA_GROUP_THUIS=' "$ENV" | cut -d= -f2-)}
KEY=$(grep '^WAHA_API_KEY=' "$ENV" | cut -d= -f2-)
printf '%s' "$REPORT" | "$PY" "$WAHA_SEND" "$CHAT" >>"$LOG" 2>&1
if [ $? -ne 0 ]; then
  # Zelfherstel: FAILED-sessie (container "healthy" zegt niks over de sessie)
  curl -s -o /dev/null -X POST http://127.0.0.1:3000/api/sessions/default/restart \
    -H "X-Api-Key: $KEY" && sleep 20
  printf '%s' "$REPORT" | "$PY" "$WAHA_SEND" "$CHAT" >>"$LOG" 2>&1
  if [ $? -ne 0 ]; then
    echo "⚠ WhatsApp-versturing NextEnergy-morgenrapport mislukt (zie $LOG)"
    exit 0
  fi
fi
echo "$(date '+%F %T') verstuurd naar groep" >>"$LOG"
exit 0
