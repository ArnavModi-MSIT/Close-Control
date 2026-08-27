#!/bin/bash
# Runs once, only against a freshly-initialized data volume (Postgres's
# own docker-entrypoint-initdb.d convention). POSTGRES_DB only creates
# ONE database on first init -- this creates the second one this project
# needs (the stream demo's isolated database). The third (ephemeral
# per-test-run databases) is deliberately NOT created here -- see
# test_review_api.py, which creates/drops its own per run.
set -e

for db in review_queue_stream; do
  exists=$(psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT 1 FROM pg_database WHERE datname='${db}'")
  if [ "$exists" != "1" ]; then
    echo "Creating database: ${db}"
    psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "CREATE DATABASE ${db};"
  else
    echo "Database already exists, skipping: ${db}"
  fi
done
