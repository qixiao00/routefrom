from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from pglast import parse_sql


class DatabaseMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = Path(__file__).resolve().parents[2]
        cls.migration_directory = cls.repo_root / "db" / "migrations"
        cls.migrations = tuple(sorted(cls.migration_directory.glob("*.sql")))

    def test_migration_numbers_are_contiguous(self) -> None:
        numbers = [int(path.name.split("_", 1)[0]) for path in self.migrations]

        self.assertEqual(numbers, list(range(1, 12)))

    def test_every_migration_parses_as_postgresql(self) -> None:
        for path in self.migrations:
            with self.subTest(path=path.name):
                statements = parse_sql(path.read_text(encoding="utf-8"))
                self.assertGreater(len(statements), 0)

    def test_algorithm_tables_are_versioned_by_processing_run(self) -> None:
        expected_tables = {
            "point_assessments",
            "observation_edges",
            "observation_gaps",
            "motion_episodes",
            "stationary_events",
            "visits",
            "trips",
            "mobility_legs",
            "trajectory_variants",
        }
        combined = "\n".join(
            path.read_text(encoding="utf-8") for path in self.migrations[6:10]
        )

        for table in expected_tables:
            with self.subTest(table=table):
                match = re.search(
                    rf"CREATE TABLE app\.{table}\s*\((.*?)\n\);",
                    combined,
                    flags=re.DOTALL,
                )
                self.assertIsNotNone(match)
                self.assertIn("processing_run_id", match.group(1))

    def test_inferred_connections_cannot_masquerade_as_confirmed_distance(self) -> None:
        sql = (self.migration_directory / "0009_trips_modes_and_inference.sql").read_text(
            encoding="utf-8"
        )
        table = re.search(
            r"CREATE TABLE app\.inferred_connections\s*\((.*?)\n\);",
            sql,
            flags=re.DOTALL,
        )

        self.assertIsNotNone(table)
        self.assertNotIn("distance_meters", table.group(1))
        self.assertIn("displayable boolean", table.group(1))

    def test_new_assets_and_time_queries_have_no_fixed_calendar_period(self) -> None:
        sql = (
            self.migration_directory / "0011_time_selections_map_revisions_and_assets.sql"
        ).read_text(encoding="utf-8")

        self.assertIn("tstzmultirange", sql)
        self.assertIn("selection_ranges", sql)
        self.assertNotIn("month_track", sql)
        self.assertNotIn("day_track", sql)
        self.assertNotIn("period_key", sql)

    def test_activation_requires_successful_import_and_stages(self) -> None:
        sql = (
            self.migration_directory / "0005_imports_and_processing_versions.sql"
        ).read_text(encoding="utf-8")

        self.assertIn("CREATE FUNCTION app.activate_processing_run", sql)
        self.assertIn("status IN ('succeeded', 'skipped')", sql)
        self.assertIn("publication_status = 'active'", sql)
        self.assertIn("processing_runs_one_active_per_dataset_idx", sql)

    def test_workspace_contract_exposes_multi_range_lod_query(self) -> None:
        contract = json.loads(
            (self.repo_root / "contracts" / "workspace.schema.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertIn("ranges", contract["$defs"]["timeSelection"]["properties"])
        detail = contract["$defs"]["trajectoryDetail"]["properties"]
        self.assertIn("pixelToleranceMeters", detail)
        self.assertIn("vertexBudget", detail)
        self.assertEqual(
            contract["$defs"]["trajectoryPath"]["properties"]["isInferred"]["const"],
            False,
        )


if __name__ == "__main__":
    unittest.main()
