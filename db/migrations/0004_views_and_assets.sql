BEGIN;

CREATE TABLE IF NOT EXISTS app.map_views (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  dataset_id uuid,
  title text NOT NULL,
  description text,
  slug text,
  schema_version integer NOT NULL DEFAULT 1 CHECK (schema_version > 0),
  config jsonb NOT NULL DEFAULT '{}'::jsonb,
  visibility text NOT NULL DEFAULT 'private'
    CHECK (visibility IN ('private', 'link', 'public')),
  share_token_hash text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, slug),
  FOREIGN KEY (user_id, dataset_id)
    REFERENCES app.datasets(user_id, id) ON DELETE SET NULL (dataset_id)
);

CREATE TABLE IF NOT EXISTS app.generated_assets (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_id uuid NOT NULL REFERENCES app.datasets(id) ON DELETE CASCADE,
  map_view_id uuid REFERENCES app.map_views(id) ON DELETE CASCADE,
  asset_kind text NOT NULL
    CHECK (asset_kind IN (
      'overview', 'month_track', 'day_track', 'heatmap',
      'vector_tile', 'thumbnail', 'export', 'report'
    )),
  period_key text,
  config_hash text,
  object_key text NOT NULL,
  content_type text NOT NULL,
  content_encoding text,
  size_bytes bigint NOT NULL CHECK (size_bytes >= 0),
  point_count integer CHECK (point_count IS NULL OR point_count >= 0),
  bounds geometry(Polygon, 4326),
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (dataset_id, asset_kind, period_key, config_hash)
);

CREATE INDEX IF NOT EXISTS map_views_user_updated_idx
  ON app.map_views (user_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS map_views_visibility_idx
  ON app.map_views (visibility) WHERE visibility <> 'private';

CREATE INDEX IF NOT EXISTS generated_assets_lookup_idx
  ON app.generated_assets (dataset_id, asset_kind, period_key);

CREATE INDEX IF NOT EXISTS generated_assets_bounds_gix
  ON app.generated_assets USING gist (bounds);

COMMIT;
