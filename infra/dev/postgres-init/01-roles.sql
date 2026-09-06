-- Runs once, on first boot of the dev cluster (docker-entrypoint-initdb.d).
-- Roles only; tables, grants, and policies come from drizzle migrations.

-- The web app connects as this role. Never a superuser, never BYPASSRLS, never
-- an owner: all three disable row-level security silently.
CREATE ROLE temnia_app LOGIN PASSWORD 'temnia_app' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;

-- The Python pipeline connects as this role from S1. DML-only, same constraints.
CREATE ROLE temnia_pipeline LOGIN PASSWORD 'temnia_pipeline' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;

-- Temporal's persistence. temporal-sql-tool creates its two databases as this role.
CREATE ROLE temporal LOGIN PASSWORD 'temporal' CREATEDB;

GRANT CONNECT ON DATABASE temnia TO temnia_app, temnia_pipeline;
