#!/bin/bash
# Wrapper voor cronjob 'Zendure scorecard' — de git-tracked scorecard draaien.
exec /usr/bin/python3 /opt/sv01-config/sv01/scripts/battery_score.py
