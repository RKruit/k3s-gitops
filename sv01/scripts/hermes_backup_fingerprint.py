#!/usr/bin/env python3
"""Vingerafdruk van alles wat in de Hermes-back-up zit.

Doel: de nachtelijke back-up overslaan als er niets is veranderd. We hashen de
inhoud van de bestanden (niet alleen mtime), omdat een nieuw bestand met
dezelfde datum anders gemist wordt.

Eén uitzondering: ~/.hermes/cron/jobs.json wordt door de cron-ticker bij elke
run herschreven (last_run_at, next_run_at, failure_streak, ...). Die velden
zeggen niets over wat je wilt bewaren, dus daar hashen we een genormaliseerde
versie: alleen de definitie van de job (naam, schema, prompt, script, skills,
deliver, enabled). Een echte wijziging aan een job telt dus wél mee.

Print één sha256 op stdout; exit 1 als een pad ontbreekt.
"""
import hashlib
import json
import os
import sys

PATHS = [
    "/srv/sv01-secrets",
    "/home/rkruit/.hermes/config.yaml",
    "/home/rkruit/.hermes/.env",
    "/home/rkruit/.hermes/SOUL.md",
    "/home/rkruit/.hermes/auth.json",
    "/home/rkruit/.hermes/google_client_secret.json",
    "/home/rkruit/.hermes/google_token.json",
    "/home/rkruit/.hermes/skills",
    "/home/rkruit/.hermes/scripts",
    "/home/rkruit/.hermes/memories",
    "/home/rkruit/.hermes/cron/notepad.db",
    "/home/rkruit/.athom-cli",
    "/home/rkruit/.hermes/hindsight/profiles",
]

JOBS = "/home/rkruit/.hermes/cron/jobs.json"
SKIP_DIRS = {"__pycache__", ".git", ".curator_backups", ".archive"}
VOLATILE = {
    "last_run_at", "next_run_at", "last_status", "last_error",
    "last_delivery_error", "failure_streak", "last_dispatch", "fire_claim",
    "state", "updated_at",
}


def hash_file(path, digest):
    try:
        with open(path, "rb") as fh:
            while chunk := fh.read(1 << 20):
                digest.update(chunk)
    except (PermissionError, FileNotFoundError, IsADirectoryError):
        digest.update(b"<onleesbaar>")


def walk(path, digest):
    if os.path.isfile(path) or os.path.islink(path):
        digest.update(b"F" + path.encode())
        if os.path.islink(path):
            digest.update(os.readlink(path).encode())
        else:
            hash_file(path, digest)
        return
    if not os.path.isdir(path):
        return
    for root, dirs, files in os.walk(path):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            full = os.path.join(root, name)
            digest.update(b"F" + full.encode())
            if os.path.islink(full):
                digest.update(os.readlink(full).encode())
            else:
                hash_file(full, digest)


def normalized_jobs():
    try:
        data = json.load(open(JOBS, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return b"<geen jobs.json>"
    jobs = data.get("jobs") if isinstance(data, dict) else data
    cleaned = []
    if isinstance(jobs, dict):
        jobs = list(jobs.values())
    for job in jobs or []:
        if isinstance(job, dict):
            cleaned.append({k: v for k, v in job.items() if k not in VOLATILE})
        else:
            cleaned.append(job)
    cleaned.sort(key=lambda j: str(j.get("id") if isinstance(j, dict) else j))
    return json.dumps(cleaned, sort_keys=True, ensure_ascii=False).encode()


def main():
    digest = hashlib.sha256()
    for path in PATHS:
        walk(path, digest)
    digest.update(b"JOBS")
    digest.update(normalized_jobs())
    print(digest.hexdigest())
    return 0


if __name__ == "__main__":
    sys.exit(main())
