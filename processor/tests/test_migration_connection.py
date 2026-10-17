from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from routefrom_pipeline.database.migrate import migrate


class MigrationConnectionTests(unittest.TestCase):
    def connection(self):
        connection = MagicMock()
        connection.info.host = "direct.example.invalid"
        connection.closed = False
        factory = MagicMock()
        factory.return_value.__enter__.return_value = connection
        return connection, factory

    def test_migration_connection_is_identifiable_and_lock_wait_is_bounded(self):
        connection, factory = self.connection()
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(
                sys.modules, {"psycopg": SimpleNamespace(connect=factory, sql=SimpleNamespace())}
            ),
        ):
            self.assertEqual(migrate("unused-test-connection", Path(directory)), [])
        options = factory.call_args.kwargs
        self.assertEqual(options["connect_timeout"], 15)
        self.assertTrue(options["application_name"].startswith("routefrom-migrate-"))
        statements = [call.args[0] for call in connection.execute.call_args_list]
        self.assertEqual(statements[0], "SET lock_timeout = '30s'")
        self.assertEqual(statements[-1], "SELECT pg_advisory_unlock(726688004)")

    def test_closed_connection_does_not_hide_the_original_migration_error(self):
        connection, factory = self.connection()

        def execute(statement):
            if statement.startswith("CREATE SCHEMA"):
                connection.closed = True
                raise RuntimeError("original disconnect")

        connection.execute.side_effect = execute
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(
                sys.modules, {"psycopg": SimpleNamespace(connect=factory, sql=SimpleNamespace())}
            ),
            self.assertRaisesRegex(RuntimeError, "original disconnect"),
        ):
            migrate("unused-test-connection", Path(directory))
        self.assertNotIn(
            "SELECT pg_advisory_unlock(726688004)",
            [call.args[0] for call in connection.execute.call_args_list],
        )
