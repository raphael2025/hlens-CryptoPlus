#!/usr/bin/env bash
#
# migrate.sh — apply packages/hlens-collector/migrations/NNN_*.sql in numeric
# order, once each, recording what was applied.
#
# AGENTS §9 / docs/03-ARCHITECTURE.md §17: no ORM, no Alembic. The migration
# tool is psql plus this file. Each migration runs in ONE transaction together
# with the row that records it, so a database can never be in the state
# "migration applied but not recorded" or the reverse.
#
# Usage:
#   scripts/migrate.sh                 # uses the libpq environment (PGDATABASE, ...)
#   scripts/migrate.sh <dsn>           # or an explicit connection string
#   scripts/migrate.sh --status <dsn>  # list what is applied, apply nothing
#
# The connection string is an argument or the environment; there is no host,
# database name or credential in this repository (AGENTS §3).
#
# TZ: the session runs with TimeZone = UTC, matching the container (§5). The
# migrations do not depend on it — every partition bound is written with an
# explicit `+00` — but a UTC session is what production has, so that is what
# they are applied under.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MIGRATIONS_DIR="$REPO_ROOT/packages/hlens-collector/migrations"

export PGTZ=UTC

STATUS_ONLY=0
if [[ "${1:-}" == "--status" ]]; then
    STATUS_ONLY=1
    shift
fi

DSN="${1:-}"
psql_run() {
    if [[ -n "$DSN" ]]; then
        psql -X -q -v ON_ERROR_STOP=1 "$DSN" "$@"
    else
        psql -X -q -v ON_ERROR_STOP=1 "$@"
    fi
}

applied_sha() {
    # Empty output means "not applied" — including the case where the ledger
    # table does not exist yet, which is exactly the state before 001 runs.
    psql_run -tAc "
        SELECT sha256 FROM hlens_meta.schema_migrations WHERE filename = '$1'
    " 2>/dev/null || true
}

if [[ "$STATUS_ONLY" == "1" ]]; then
    psql_run -c "
        SELECT filename, applied_at, left(sha256, 12) AS sha
          FROM hlens_meta.schema_migrations
         ORDER BY filename
    "
    exit 0
fi

shopt -s nullglob
migrations=("$MIGRATIONS_DIR"/[0-9][0-9][0-9]_*.sql)
if [[ ${#migrations[@]} -eq 0 ]]; then
    echo "migrate.sh: no migrations found in $MIGRATIONS_DIR" >&2
    exit 1
fi

for path in "${migrations[@]}"; do
    name="$(basename "$path")"
    sum="$(sha256sum "$path" | cut -d' ' -f1)"
    recorded="$(applied_sha "$name")"

    if [[ -n "$recorded" ]]; then
        if [[ "$recorded" != "$sum" ]]; then
            echo "migrate.sh: $name was applied with different text (recorded ${recorded:0:12}, on disk ${sum:0:12})." >&2
            echo "            An applied migration is history. Add a new numbered file instead of editing this one." >&2
            exit 1
        fi
        echo "  skip   $name"
        continue
    fi

    echo "  apply  $name"
    psql_run -1 -f "$path" -c "
        INSERT INTO hlens_meta.schema_migrations (filename, sha256, source)
        VALUES ('$name', '$sum', 'migrate.sh')
    "
done

echo "migrate.sh: up to date."
