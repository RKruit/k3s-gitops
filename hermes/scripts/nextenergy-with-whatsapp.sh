#!/bin/bash
# NextEnergy dagrapport → Discord (deliver van de cronjob) + WhatsApp-groep
# "THUIS, Oostrandpark". Cronjob 8eb647e208c6 verwijst naar dit script;
# nextenergy_report.py zelf is ongewijzigd. Altijd exit 0 zodat Discord
# zoals gebruikelijk de output krijgt (zoals het python-script doet).
PY=/home/rkruit/.hermes/venvs/monitoring/bin/python3
REPORT="$($PY /home/rkruit/.hermes/scripts/nextenergy_report.py)"
RC=$?
if [ $RC -ne 0 ]; then
  echo "<script> NextEnergy-rapport faalde (exit $RC) — WhatsApp-overbrugging overgeslagen"
  exit 0
fi
echo "$REPORT"
CHAT=$(grep '^WAHA_GROUP_THUIS=' /srv/sv01-secrets/waha.env | cut -d= -f2-)
KEY=$(grep '^WAHA_API_KEY=' /srv/sv01-secrets/waha.env | cut -d= -f2-)
printf '%s' "$REPORT" | $PY /home/rkruit/.hermes/scripts/waha-send.py "$CHAT"
if [ $? -ne 0 ]; then
  # Zelfherstel: FAILED-sessie (container "healthy" zegt niks over de sessie)
  # → herstart en één retry; geen QR-scan nodig zolang de creds geldig zijn.
  curl -s -o /dev/null -X POST http://127.0.0.1:3000/api/sessions/default/restart \
    -H "X-Api-Key: $KEY" && sleep 20
  printf '%s' "$REPORT" | $PY /home/rkruit/.hermes/scripts/waha-send.py "$CHAT" \
    || echo "(WhatsApp-versturing naar de groep mislukt — hierboven staat het Discord-rapport)"
fi
exit 0