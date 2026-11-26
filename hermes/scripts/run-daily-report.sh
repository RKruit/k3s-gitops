#!/bin/bash
# Wrapper voor de Hermes-cronjob: dagrapport script met monitoring-venv.
exec /home/rkruit/.hermes/venvs/monitoring/bin/python /home/rkruit/whatsapp/daily-report.py