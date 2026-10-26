"""Check source refresh selection without connecting to Docker or a database."""

import csv
import io
import json
import tempfile
import unittest
from unittest.mock import patch

from zetaris.refresh_kb import main


class KBRefreshTests(unittest.TestCase):
    def test_refresh_selects_only_requested_article_and_target_database(self):
        with tempfile.TemporaryDirectory() as bundle:
            args = ["refresh_kb.py", "--bundle", bundle, "--article-id", "KB-011"]
            with patch("sys.argv", args), patch("zetaris.refresh_kb.subprocess.run") as run, \
                 patch("builtins.print"):
                main()
        command = run.call_args.args[0]
        self.assertIn("resolve_kb", command)
        self.assertNotIn("resolve_accounts", command)
        sql = run.call_args.kwargs["input"]
        self.assertIn("ON_ERROR_STOP=1", command)
        self.assertTrue(run.call_args.kwargs["check"])
        self.assertTrue(sql.startswith("BEGIN;"))
        self.assertTrue(sql.rstrip().endswith("COMMIT;"))
        self.assertNotIn("DROP TABLE", sql)
        self.assertNotIn("DELETE FROM", sql)
        csv_text = sql.split("FROM STDIN WITH (FORMAT csv);\n", 1)[1].split("\\.\n", 1)[0]
        rows = list(csv.reader(io.StringIO(csv_text)))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][:2], ["KB-011", "appearance"])
        self.assertIn("00:00", json.loads(rows[0][2])["body"])

    def test_unknown_article_is_rejected_before_docker(self):
        with tempfile.TemporaryDirectory() as bundle:
            args = ["refresh_kb.py", "--bundle", bundle, "--article-id", "KB-missing"]
            with patch("sys.argv", args), patch("zetaris.refresh_kb.subprocess.run") as run, \
                 patch("sys.stderr", new_callable=io.StringIO):
                with self.assertRaises(SystemExit):
                    main()
                run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
