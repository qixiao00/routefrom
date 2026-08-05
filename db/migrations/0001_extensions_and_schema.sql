BEGIN;

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS app;

COMMENT ON SCHEMA app IS
  'RouteFrom application data. Authentication schemas are kept separate.';

COMMIT;
