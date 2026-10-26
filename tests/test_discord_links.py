"""Local Discord links overlay the committed placeholder IDs without editing data files."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from resolve.config import data_dir
from resolve.research import SQLResearcher, seed_snapshot


class DiscordLinkTests(unittest.TestCase):
    def test_local_links_override_placeholders(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "data"
            shutil.copytree(data_dir(), data)
            (data / "discord_links.local.json").write_text(json.dumps({"c-031": "123456789012345678"}))
            database = Path(directory) / "t.sqlite"
            seed_snapshot(data, database)
            researcher = SQLResearcher.local(database)
            self.assertEqual(researcher.customer_for_discord("123456789012345678"), "c-031")
            self.assertIsNone(researcher.customer_for_discord("900000000000000031"))
            self.assertEqual(researcher.customer_for_discord("900000000000000002"), "c-002")

    def test_non_numeric_link_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "data"
            shutil.copytree(data_dir(), data)
            (data / "discord_links.local.json").write_text(json.dumps({"c-031": "me"}))
            with self.assertRaises(ValueError):
                seed_snapshot(data, Path(directory) / "t.sqlite")


if __name__ == "__main__":
    unittest.main()
