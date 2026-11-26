#!/bin/bash
# Poll Drive voor nieuwe Boox-exports; meld alleen bij iets nieuws (stil anders).
out=$(python3 /home/rkruit/boox-drawings/tools/fetch_boox_drive.py 2>&1) || true
if echo "$out" | grep -q GEDOWNLOAD; then
  echo "Nieuwe Boox-export(s) opgehaald van Drive:"
  echo "$out"
fi
