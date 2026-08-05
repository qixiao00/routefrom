BEGIN;

CREATE TABLE app.time_selections (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  dataset_id uuid NOT NULL REFERENCES app.datasets(id) ON DELETE CASCADE,
  title text,
  timezone text NOT NULL,
  canonical_ranges tstzmultirange NOT NULL,
  normalized_hash text NOT NULL CHECK (normalized_hash ~ '^[0-9A-Fa-f]{64}$'),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (id, dataset_id),
  FOREIGN KEY (user_id, dataset_id)
    REFERENCES app.datasets(user_id, id) ON DELETE CASCADE,
  CHECK (NOT isempty(canonical_ranges))
);

CREATE TABLE app.selection_ranges (
  time_selection_id uuid NOT NULL REFERENCES app.time_selections(id) ON DELETE CASCADE,
  sequence_number integer NOT NULL CHECK (sequence_number >= 0),
  selected_range tstzrange NOT NULL,
  PRIMARY KEY (time_selection_id, sequence_number),
  UNIQUE (time_selection_id, selected_range),
  EXCLUDE USING gist (
    time_selection_id WITH =,
    selected_range WITH &&
  ),
  CHECK (NOT isempty(selected_range)),
  CHECK (lower_inc(selected_range) AND NOT upper_inc(selected_range))
);

CREATE FUNCTION app.validate_time_selection()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  target_selection_id uuid;
  expected_ranges tstzmultirange;
  expected_count integer;
  minimum_sequence integer;
  maximum_sequence integer;
  stored_ranges tstzmultirange;
  stored_hash text;
BEGIN
  IF TG_TABLE_NAME = 'time_selections' THEN
    target_selection_id := NEW.id;
  ELSIF TG_OP = 'DELETE' THEN
    target_selection_id := OLD.time_selection_id;
  ELSE
    target_selection_id := NEW.time_selection_id;
  END IF;

  SELECT canonical_ranges, normalized_hash
  INTO stored_ranges, stored_hash
  FROM app.time_selections
  WHERE id = target_selection_id;

  IF NOT FOUND THEN
    RETURN NULL;
  END IF;

  SELECT
    range_agg(selected_range ORDER BY sequence_number),
    count(*),
    min(sequence_number),
    max(sequence_number)
  INTO expected_ranges, expected_count, minimum_sequence, maximum_sequence
  FROM app.selection_ranges
  WHERE time_selection_id = target_selection_id;

  IF expected_count = 0 THEN
    RAISE EXCEPTION 'time selection % must contain at least one range', target_selection_id;
  END IF;
  IF minimum_sequence <> 0 OR maximum_sequence <> expected_count - 1 THEN
    RAISE EXCEPTION 'time selection % range sequence must be contiguous', target_selection_id;
  END IF;
  IF expected_ranges IS DISTINCT FROM stored_ranges THEN
    RAISE EXCEPTION 'time selection % canonical multirange does not match rows', target_selection_id;
  END IF;
  IF stored_hash <> encode(digest(stored_ranges::text, 'sha256'), 'hex') THEN
    RAISE EXCEPTION 'time selection % normalized hash is invalid', target_selection_id;
  END IF;
  RETURN NULL;
END
$$;

CREATE CONSTRAINT TRIGGER time_selections_canonical_trigger
AFTER INSERT OR UPDATE ON app.time_selections
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION app.validate_time_selection();

CREATE CONSTRAINT TRIGGER selection_ranges_canonical_trigger
AFTER INSERT OR UPDATE OR DELETE ON app.selection_ranges
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION app.validate_time_selection();

DO $$
BEGIN
  IF to_regclass('app.generated_assets') IS NOT NULL
     AND to_regclass('app.legacy_generated_assets') IS NULL THEN
    ALTER TABLE app.generated_assets RENAME TO legacy_generated_assets;
  END IF;
  IF to_regclass('app.map_views') IS NOT NULL
     AND to_regclass('app.legacy_map_views') IS NULL THEN
    ALTER TABLE app.map_views RENAME TO legacy_map_views;
  END IF;
END
$$;

CREATE TABLE app.map_views (
  id uuid CONSTRAINT map_views_v2_pkey PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  dataset_id uuid NOT NULL REFERENCES app.datasets(id) ON DELETE CASCADE,
  title text NOT NULL,
  description text,
  slug text,
  visibility text NOT NULL DEFAULT 'private'
    CHECK (visibility IN ('private', 'link', 'public')),
  share_token_hash text,
  current_revision_id uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT map_views_v2_user_slug_unique UNIQUE (user_id, slug),
  UNIQUE (id, user_id),
  UNIQUE (id, dataset_id),
  FOREIGN KEY (user_id, dataset_id)
    REFERENCES app.datasets(user_id, id) ON DELETE CASCADE
);

CREATE TABLE app.map_view_revisions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  map_view_id uuid NOT NULL,
  dataset_id uuid NOT NULL,
  revision_number integer NOT NULL CHECK (revision_number > 0),
  schema_version integer NOT NULL CHECK (schema_version > 0),
  processing_run_id uuid,
  time_selection_id uuid,
  config jsonb NOT NULL,
  config_hash text NOT NULL CHECK (config_hash ~ '^[0-9A-Fa-f]{64}$'),
  created_by_user_id uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (map_view_id, revision_number),
  UNIQUE (id, map_view_id),
  UNIQUE (id, dataset_id),
  FOREIGN KEY (map_view_id, dataset_id)
    REFERENCES app.map_views(id, dataset_id) ON DELETE CASCADE,
  FOREIGN KEY (map_view_id, created_by_user_id)
    REFERENCES app.map_views(id, user_id) ON DELETE RESTRICT,
  FOREIGN KEY (processing_run_id, dataset_id)
    REFERENCES app.processing_runs(id, dataset_id) ON DELETE RESTRICT,
  FOREIGN KEY (time_selection_id, dataset_id)
    REFERENCES app.time_selections(id, dataset_id) ON DELETE RESTRICT
);

ALTER TABLE app.map_views
  ADD CONSTRAINT map_views_current_revision_fk
  FOREIGN KEY (current_revision_id, id)
  REFERENCES app.map_view_revisions(id, map_view_id)
  ON DELETE SET NULL (current_revision_id);

CREATE TABLE app.generated_assets (
  id uuid CONSTRAINT generated_assets_v2_pkey PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_id uuid NOT NULL REFERENCES app.datasets(id) ON DELETE CASCADE,
  processing_run_id uuid,
  map_view_revision_id uuid,
  time_selection_id uuid,
  asset_kind text NOT NULL CHECK (asset_kind IN (
    'trajectory_chunks', 'heatmap', 'point_tiles', 'cluster_tiles',
    'thumbnail', 'export', 'report'
  )),
  selection_hash text CHECK (selection_hash IS NULL OR selection_hash ~ '^[0-9A-Fa-f]{64}$'),
  config_hash text NOT NULL CHECK (config_hash ~ '^[0-9A-Fa-f]{64}$'),
  object_key text NOT NULL,
  content_type text NOT NULL,
  content_encoding text,
  size_bytes bigint NOT NULL CHECK (size_bytes >= 0),
  feature_count integer CHECK (feature_count IS NULL OR feature_count >= 0),
  bounds geometry(Polygon, 4326),
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (processing_run_id, dataset_id)
    REFERENCES app.processing_runs(id, dataset_id) ON DELETE CASCADE,
  FOREIGN KEY (map_view_revision_id, dataset_id)
    REFERENCES app.map_view_revisions(id, dataset_id) ON DELETE CASCADE,
  FOREIGN KEY (time_selection_id, dataset_id)
    REFERENCES app.time_selections(id, dataset_id) ON DELETE RESTRICT,
  UNIQUE (dataset_id, processing_run_id, asset_kind, selection_hash, config_hash)
);

CREATE INDEX time_selections_user_dataset_idx
  ON app.time_selections (user_id, dataset_id, updated_at DESC);

CREATE INDEX time_selections_ranges_gix
  ON app.time_selections USING gist (canonical_ranges);

CREATE INDEX map_views_user_updated_v2_idx
  ON app.map_views (user_id, updated_at DESC);

CREATE INDEX map_view_revisions_view_revision_idx
  ON app.map_view_revisions (map_view_id, revision_number DESC);

CREATE INDEX generated_assets_lookup_v2_idx
  ON app.generated_assets (
    dataset_id,
    processing_run_id,
    asset_kind,
    selection_hash,
    config_hash
  );

CREATE INDEX generated_assets_bounds_v2_gix
  ON app.generated_assets USING gist (bounds);

COMMENT ON TABLE app.selection_ranges IS
  'Normalized arbitrary UTC [start,end) ranges. Natural days and months are UI presets only.';

COMMENT ON TABLE app.map_view_revisions IS
  'Immutable full map configuration snapshots for exact save and restore.';

COMMIT;
