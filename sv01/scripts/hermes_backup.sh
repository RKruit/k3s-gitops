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

# rooktest: kan het archief geopend worden met de passphrase?
if gpg --batch --quiet --decrypt --passphrase-file "$PASSFILE" "$OUT" 2>/dev/null | tar -tzf - >/dev/null 2>&1; then
  echo "controle: archief leesbaar en compleet"
else
  echo "FOUT: archief niet leesbaar — back-up is NIET betrouwbaar" >&2
  exit 2
fi
