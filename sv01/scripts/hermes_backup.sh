#!/usr/bin/env bash
# Back-up van alles wat NIET in git staat en wat je nodig hebt om sv01 +
# Hermes op een nieuwe server terug te zetten.
#
# In git (/opt/sv01-config, GitHub): compose-stacks, dashboards, host-scripts,
# systemd-units, docs. Dat haal je terug met een git clone.
# In deze back-up (versleuteld, op de NAS 192.168.1.2, dus los van sv01):
#   - /srv/sv01-secrets        alle wachtwoorden en tokens
#   - ~/.hermes                config, .env, skills, scripts, memories, cron-jobs
#   - ~/.athom-cli             Homey-sessie (opnieuw in te loggen, maar handig)
#
# Versleuteling: symmetrisch AES256 met een passphrase uit
# /srv/sv01-secrets/backup-passphrase. ZONDER DIE PASSPHRASE IS DE BACK-UP
# NIET TERUG TE ZETTEN — bewaar hem buiten sv01 (wachtwoordmanager).
#
# Terugzetten: zie de skill 'sv01-restore' (of docs/restore.md in de repo).
set -euo pipefail

DEST="/mnt/nas/hermes_shared/backups/hermes"
PASSFILE="/srv/sv01-secrets/backup-passphrase"
KEEP=14
STAMP="$(date +%Y%m%d-%H%M)"
OUT="${DEST}/hermes-${STAMP}.tar.gz.gpg"

[ -r "$PASSFILE" ] || { echo "FOUT: passphrase-bestand $PASSFILE ontbreekt of is onleesbaar" >&2; exit 1; }
mkdir -p "$DEST"

# --- alleen back-uppen als er iets is veranderd -------------------------
# Vingerafdruk over de inhoud (niet over mtimes); jobs.json wordt genormaliseerd
# omdat de cron-ticker daar elke run last_run_at/next_run_at in bijwerkt.
STATE_DIR="/var/lib/sv01-backup"
FINGERPRINT_FILE="${STATE_DIR}/hermes-fingerprint"
MAX_AGE_DAYS=7   # vangnet: hoe dan ook minstens één keer per week een archief
mkdir -p "$STATE_DIR"

FINDER="$(dirname "$0")/hermes_backup_fingerprint.py"
NOW_FP="$(python3 "$FINDER")" || { echo "FOUT: vingerafdruk mislukt" >&2; exit 1; }
LAST_FP="$(cat "$FINGERPRINT_FILE" 2>/dev/null || true)"

NEWEST="$(ls -1t "${DEST}"/hermes-*.tar.gz.gpg 2>/dev/null | head -1 || true)"
FORCE=0
if [ -z "$NEWEST" ]; then
  FORCE=1
elif [ -n "$(find "$NEWEST" -mtime "+${MAX_AGE_DAYS}" 2>/dev/null)" ]; then
  FORCE=1
fi

if [ "${NOW_FP}" = "${LAST_FP}" ] && [ "$FORCE" -eq 0 ]; then
  echo "$(date -Is) geen wijzigingen sinds $(basename "$NEWEST") — back-up overgeslagen"
  exit 0
fi

# lijst met paden die mee moeten (bestaande paden alleen)
TMPLIST="$(mktemp)"
trap 'rm -f "$TMPLIST"' EXIT
while read -r p; do
  [ -e "$p" ] && echo "$p" >> "$TMPLIST"
done <<'PATHS'
/srv/sv01-secrets
/home/rkruit/.hermes/config.yaml
/home/rkruit/.hermes/.env
/home/rkruit/.hermes/SOUL.md
/home/rkruit/.hermes/auth.json
/home/rkruit/.hermes/google_client_secret.json
/home/rkruit/.hermes/google_token.json
/home/rkruit/.hermes/skills
/home/rkruit/.hermes/scripts
/home/rkruit/.hermes/memories
/home/rkruit/.hermes/cron/jobs.json
/home/rkruit/.hermes/cron/notepad.db
/home/rkruit/.athom-cli
/home/rkruit/.hermes/hindsight/profiles
PATHS

sudo tar --create --gzip --warning=no-file-changed \
  --file - --files-from "$TMPLIST" 2>/dev/null \
| gpg --batch --yes --quiet --symmetric --cipher-algo AES256 \
      --passphrase-file "$PASSFILE" --output "$OUT"

SIZE=$(du -h "$OUT" | cut -f1)
echo "$(date -Is) back-up gemaakt: $OUT ($SIZE)"

# oude back-ups opruimen
ls -1t "${DEST}"/hermes-*.tar.gz.gpg 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
  rm -f "$old" && echo "opgeruimd: $old"
done

# onthoud de vingerafdruk pas als het archief goed is (hieronder bij succes)
# rooktest: kan het archief geopend worden met de passphrase?
if gpg --batch --quiet --decrypt --passphrase-file "$PASSFILE" "$OUT" 2>/dev/null | tar -tzf - >/dev/null 2>&1; then
  echo "controle: archief leesbaar en compleet"
  echo "${NOW_FP}" > "$FINGERPRINT_FILE"
else
  echo "FOUT: archief niet leesbaar — back-up is NIET betrouwbaar" >&2
  exit 2
fi
