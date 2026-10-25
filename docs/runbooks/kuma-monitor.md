# Runbook: add / update a Kuma monitor via the API

Script: `sv01/scripts/kuma-add-monitors.py` — idempotent, reads creds from `/srv/sv01-secrets/kuma-admin.env` (user `hermes-monitoring`).

## One-time setup (Hermes host)

System pip is PEP 668-locked — make a venv:

```bash
python3 -m venv ~/.hermes/venvs/monitoring
~/.hermes/venvs/monitoring/bin/pip install python-socketio
```

## Running

```bash
~/.hermes/venvs/monitoring/bin/python sv01/scripts/kuma-add-monitors.py
```

## v2 API gotchas (learned the hard way — Kuma 2.5.7)

| Thing | Correct value |
|---|---|
| `setup` event payload | **tuple** `(username, password)` |
| `login` event payload | **dict** `{username, password, token: None}` |
| Monitor-add event | **`add`** (not `addMonitor`), payload is `(monitor,)` |
| Monitor payload keys | must include `conditions`, `kafkaProducerBrokers`, `kafkaProducerSaslOptions`, `rabbitmqNodes`, `accepted_statuscodes` (NOT NULL constraints; the handler JSON-stringifies them) |
| TCP check type | **`port`** — type `tcp` does not exist (it yields *Unknown Monitor Type* and that monitor's scheduler hangs) |
| `getMonitorList` | call with **no** data argument; the list arrives via a separate `monitorList` event |

## Verifying

- Success shows as immediate up/down beats (60 s interval) in the UI or via the socket API.
- Ground truth: the sqlite DB on NFS — copy `kuma.db` + `-wal` + `-shm` first, then inspect the copy.
- HTTP/keyword monitors also exist for containers with a web UI; the script's monitor set: komodo, todo-app, beszel, kuma-self, hindsight, ollama, pg0.