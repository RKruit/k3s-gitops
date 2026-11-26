#!/usr/bin/env python3
"""Dagelijkse datumverschuiving voor het panel 'Gasverbruik (...)' in verwarming.

Sinds 12-11-2026: het aparte dashboard 'gas-gisteren' is geschrapt (dubbel);
de offset-lijn (increase p1_gas_m3[1h] offset 1d) in verwarming.json schuift
vanzelf mee, deze cron zet alleen de DATUM in de paneltitel op gisteren en
pusht het via k3s-gitops (webhook-sync -> provisioning). Idempotent: niets te
doen als de datum al klopt. Grafana-parse-les (13.3.0): relatieve 'to'-
expressies degraderen (now-1d/d -> now-1d), epoch-ms is 'Invalid date',
absolute ISO-strings met lokale offset zijn de enige betrouwbare vorm -
maar panel-level time telt nooit mee (dashboards met eigen venster verdienen
de voorkeur; lesson in grafana-dashboards-skill).
"""
import json
import subprocess
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

GIT_DIR = "/opt/sv01-config"
DATA_REL = "sv01/compose/grafana/dashboards/verwarming.json"
DATA = f"{GIT_DIR}/{DATA_REL}"
TZ = ZoneInfo("Europe/Amsterdam")


def main() -> int:
    with open(DATA) as f:
        d = json.load(f)
    gister = (datetime.now(TZ).date() - timedelta(days=1)).strftime("%d-%m")
    want = f"Gasverbruik ({gister})"
    p3 = next((p for p in d.get("panels", []) if p.get("id") == 3
               and str(p.get("title", "")).startswith("Gasverbruik (")), None)
    if p3 is None:
        print("gas-gisteren-titel: panel 3 niet gevonden in verwarming.json, niets te doen")
        return 0
    if p3.get("title") == want:
        print(f"titel staat al op {want}, niets te doen")
        return 0
    p3["title"] = want
    d["version"] = int(d.get("version", 1)) + 1
    with open(DATA, "w") as f:
        json.dump(d, f, indent=1, ensure_ascii=False)
        f.write("\n")
    subprocess.run(["git", "-C", GIT_DIR, "add", DATA_REL], check=True)
    r = subprocess.run(
        ["git", "-C", GIT_DIR, "commit", "-q", "-m", f"gas-gisteren: titel -> {want} (cron)"],
        capture_output=True, text=True,
    )
    if r.returncode != 0 and "nothing to commit" not in r.stdout + r.stderr:
        print("commit mislukt:", r.stdout, r.stderr)
        return 1
    subprocess.run(["git", "-C", GIT_DIR, "push", "-q"], check=True)
    print(f"titel -> {want} (v{d['version']}, gepusht)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
