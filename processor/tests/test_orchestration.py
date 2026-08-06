from __future__ import annotations

import unittest
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch
from uuid import UUID

from routefrom_pipeline.database.imports import ImportResult
from routefrom_pipeline.database.postgres import PersistenceResult
from routefrom_pipeline.model import CsvProfile
from routefrom_pipeline.orchestration import run_linggan_pipeline
from routefrom_pipeline.pipeline import ProcessingConfig


class FakeCursor:
    def __init__(self) -> None:
        self.executions: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, query: str, params: Any = ()) -> None:
        self.executions.append((" ".join(query.split()), tuple(params)))


class FakeConnection:
    def __init__(self) -> None:
        self.fake_cursor = FakeCursor()
        self.transaction_count = 0

    def transaction(self) -> Any:
        self.transaction_count += 1
        return nullcontext()

    def cursor(self) -> FakeCursor:
        return self.fake_cursor


class OrchestrationTests(unittest.TestCase):
    dataset_id = UUID("ab96de99-f2d3-402b-ad2b-c756e05d4d62")
    dataset_import_id = UUID("3a2b810e-ce30-408a-a29b-9e6c9bf026e5")
    processing_run_id = UUID("3384175a-5fe9-4f78-a132-21604d78673c")

    def _import_result(self) -> ImportResult:
        recorded_at = datetime(2026, 7, 1, tzinfo=UTC)
        profile = CsvProfile(
            sha256="A" * 64,
            row_count=2,
            first_recorded_at=recorded_at,
            last_recorded_at=recorded_at,
            timestamps_strictly_increasing=True,
            duplicate_timestamp_count=0,
            sentinel_counts={},
        )
        return ImportResult(self.dataset_import_id, profile, 2, False)

    @patch("routefrom_pipeline.orchestration.persist_processed_trace")
    @patch("routefrom_pipeline.orchestration.process_trace")
    @patch("routefrom_pipeline.orchestration.iter_linggan_csv")
    @patch("routefrom_pipeline.orchestration.import_linggan_csv")
    def test_success_links_job_import_and_processing_run(
        self,
        import_csv: Mock,
        iter_csv: Mock,
        process: Mock,
        persist: Mock,
    ) -> None:
        connection = FakeConnection()
        imported = self._import_result()
        trace = object()
        persistence = PersistenceResult(self.processing_run_id, {}, {})
        import_csv.return_value = imported
        source_points = (object(), object())
        iter_csv.return_value = iter(source_points)
        process.return_value = trace
        persist.return_value = persistence
        config = ProcessingConfig(interval_window=4)

        result = run_linggan_pipeline(
            connection,
            dataset_id=self.dataset_id,
            source_path=Path("linggan.csv"),
            config=config,
            parameters={"profile": "personal"},
            code_revision="test-revision",
            import_batch_size=500,
        )

        self.assertEqual(result.dataset_import, imported)
        self.assertEqual(result.processing, persistence)
        self.assertEqual(connection.transaction_count, 5)
        import_csv.assert_called_once_with(
            connection,
            dataset_id=self.dataset_id,
            source_path=Path("linggan.csv"),
            source_object_key=None,
            timezone="Asia/Shanghai",
            batch_size=500,
        )
        process.assert_called_once_with(
            source_points,
            config=config,
            map_matcher=None,
            map_snapshot=None,
        )
        persist.assert_called_once()
        persistence_call = persist.call_args.kwargs
        self.assertEqual(persistence_call["dataset_import_id"], self.dataset_import_id)
        self.assertEqual(persistence_call["input_sha256"], "A" * 64)
        self.assertEqual(persistence_call["parameters"]["requested"]["profile"], "personal")
        queries = [query for query, _ in connection.fake_cursor.executions]
        self.assertTrue(any("stage = 'run_algorithms'" in query for query in queries))
        self.assertTrue(any("stage = 'persist_results'" in query for query in queries))
        success_params = next(
            params
            for query, params in connection.fake_cursor.executions
            if "status = 'succeeded'" in query
        )
        self.assertEqual(success_params[0], self.processing_run_id)

    @patch("routefrom_pipeline.orchestration.persist_processed_trace")
    @patch("routefrom_pipeline.orchestration.import_linggan_csv")
    def test_failure_is_audited_without_discarding_an_active_version(
        self,
        import_csv: Mock,
        persist: Mock,
    ) -> None:
        connection = FakeConnection()
        import_csv.side_effect = ValueError("source hash mismatch")

        with self.assertRaisesRegex(ValueError, "source hash mismatch"):
            run_linggan_pipeline(
                connection,
                dataset_id=self.dataset_id,
                source_path=Path("linggan.csv"),
            )

        persist.assert_not_called()
        self.assertEqual(connection.transaction_count, 3)
        queries = [query for query, _ in connection.fake_cursor.executions]
        self.assertTrue(any("status = 'failed'" in query for query in queries))
        dataset_failure_query = next(
            query
            for query in queries
            if "UPDATE app.datasets AS dataset" in query
        )
        self.assertIn("publication_status = 'active'", dataset_failure_query)
        failure_params = next(
            params
            for query, params in connection.fake_cursor.executions
            if "status = 'failed'" in query
        )
        self.assertIn("ValueError: source hash mismatch", str(failure_params[0]))


if __name__ == "__main__":
    unittest.main()
