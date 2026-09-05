#!/bin/sh
# One-shot schema setup for the Temporal server, run from temporalio/admin-tools
# (POSIX sh: the image has no bash). Mirrors what the deprecated auto-setup image
# did, made idempotent: creating a database that exists is ignored, and
# setup-schema runs only when update-schema finds no version table.
set -eu

: "${POSTGRES_SEEDS:?}" "${POSTGRES_USER:?}" "${POSTGRES_PWD:?}"
: "${DB_PORT:=5432}" "${DBNAME:=temporal}" "${VISIBILITY_DBNAME:=temporal_visibility}"

export SQL_PASSWORD="${POSTGRES_PWD}"

tool() {
  db="$1"
  shift
  temporal-sql-tool --plugin postgres12 --ep "${POSTGRES_SEEDS}" -u "${POSTGRES_USER}" -p "${DB_PORT}" --db "${db}" "$@"
}

setup_db() {
  db="$1"
  schema_dir="$2"
  tool "${db}" create || echo "database ${db} already exists"
  if ! tool "${db}" update-schema -d "${schema_dir}" 2>/dev/null; then
    tool "${db}" setup-schema -v 0.0
    tool "${db}" update-schema -d "${schema_dir}"
  fi
}

setup_db "${DBNAME}" /etc/temporal/schema/postgresql/v12/temporal/versioned
setup_db "${VISIBILITY_DBNAME}" /etc/temporal/schema/postgresql/v12/visibility/versioned
echo "temporal schemas ready"
