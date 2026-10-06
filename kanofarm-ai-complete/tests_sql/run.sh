#!/usr/bin/env bash
# Runs the real migrations (0001..latest) on a scratch PostgreSQL database and then the behaviour tests in behaviour.sql.
# Usage:  PGHOST=/path/to/socket-or-host PGPORT=5432 PGUSER=postgres tests_sql/run.sh
# Needs only psql and a reachable PostgreSQL 15+ server where the user may create databases and roles. Creates and drops "kf_test".
set -euo pipefail
cd "$(dirname "$0")/.."
DB=kf_test
psql -v ON_ERROR_STOP=1 -q -d postgres -c "drop database if exists $DB" -c "create database $DB"
# roles are cluster-wide, so create them only if an earlier run has not already
psql -v ON_ERROR_STOP=1 -q -d $DB -c "do \$\$ begin
  if not exists (select 1 from pg_roles where rolname='anon') then create role anon nologin; end if;
  if not exists (select 1 from pg_roles where rolname='authenticated') then create role authenticated nologin; end if;
  if not exists (select 1 from pg_roles where rolname='service_role') then create role service_role nologin bypassrls; end if; end \$\$"
sed -e '/^create role/d' tests_sql/supabase_stub.sql | psql -v ON_ERROR_STOP=1 -q -d $DB
for f in database/migrations/*.sql; do
  echo "applying $f"; psql -v ON_ERROR_STOP=1 -q -d $DB -f "$f"
done
psql -v ON_ERROR_STOP=1 -q -d $DB -f tests_sql/behaviour.sql
echo "SQL BEHAVIOUR TESTS PASSED"
psql -q -d postgres -c "drop database $DB"
