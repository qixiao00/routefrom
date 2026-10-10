BEGIN;

-- Every persisted chunk needs a searchable envelope, including a horizontal
-- line, a vertical line and a single vertex. A tiny padding keeps the existing
-- Polygon column valid when the geometric envelope would be a line or point.
CREATE FUNCTION app.refresh_trajectory_chunk_bounds() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  geometry_for_bounds geometry;
BEGIN
  geometry_for_bounds := NEW.path;
  IF geometry_for_bounds IS NULL THEN
    SELECT ST_Collect(position) INTO geometry_for_bounds
    FROM app.trajectory_vertices
    WHERE trajectory_variant_id = NEW.trajectory_variant_id
      AND trajectory_segment_id = NEW.trajectory_segment_id
      AND sequence_number BETWEEN NEW.first_vertex_sequence AND NEW.last_vertex_sequence;
  END IF;
  NEW.bounds := ST_Expand(ST_Envelope(geometry_for_bounds), 0.000000001);
  RETURN NEW;
END;
$$;

CREATE TRIGGER trajectory_chunks_refresh_bounds
BEFORE INSERT OR UPDATE OF path, trajectory_variant_id, trajectory_segment_id,
  first_vertex_sequence, last_vertex_sequence ON app.trajectory_chunks
FOR EACH ROW EXECUTE FUNCTION app.refresh_trajectory_chunk_bounds();

-- Re-run the trigger for previously published versions; keep their geometry,
-- time ranges, provenance and publication state unchanged.
UPDATE app.trajectory_chunks SET path = path WHERE bounds IS NULL;

COMMIT;
