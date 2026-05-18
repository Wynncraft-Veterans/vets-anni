#!/bin/sh
# Apply schema, then run the app. Aerich is the source of truth; the
# generate_schemas fallback only triggers if migrations/ is somehow absent
# (e.g. a fresh clone before the first migration was generated).
set -e

if aerich upgrade; then
  echo "entrypoint: aerich upgrade applied"
else
  echo "entrypoint: aerich upgrade unavailable -> generate_schemas fallback"
  python -m app.db.bootstrap
fi

exec python main.py
