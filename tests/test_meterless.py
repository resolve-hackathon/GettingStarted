"""Meterless World Model and H-MEM: evidence ingested with provenance, memory across runs."""

import json
import tempfile
import unittest
from pathlib import Path

from resolve.actions import action_material
from resolve.agent import Agent
from resolve.config import data_dir
from resolve.memory import Memory
from resolve.reasoning import RulesReasoner
from resolve.research import SQLResearcher, seed_snapshot
from resolve.world_model import WorldModel


class CompletedActions:
    """Stands in for confirmed live actions: records the payload, reports completed."""
    def __init__(self):
        self.payloads = []

    def execute(self, state):
        text, payload = action_material(state)
        self.payloads.append(payload)
        return {"status": "completed", "receipts": []}, text


class MeterlessEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db, self.logs, self.mem = root / "t.sqlite", root / "logs", root / "memory"
        seed_snapshot(data_dir(), self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def agent(self, actions):
        # New engine instances each time: proves memory survives a fresh process (warm start).
        return Agent(SQLResearcher.local(self.db), RulesReasoner(), actions,
                     WorldModel(self.logs), Memory(self.mem, self.logs))

    def test_world_model_records_provenance_and_feeds_context(self):
        state = self.agent(CompletedActions()).run("Desktop sync stopped after the update", "c-031", "q1")
        lines = [json.loads(l) for l in (self.logs / "world.jsonl").read_text().splitlines()]
        entity = next(l for l in lines if l["kind"] == "entity" and l["id"] == "issues:GH-1")
        self.assertEqual(entity["provenance"]["rowId"], "GH-1")
        self.assertEqual((entity["agentId"], entity["runId"]), ("researcher", "q1"))
        self.assertTrue(any(l["kind"] == "relationship" and l["type"] == "affects" for l in lines))
        self.assertIn("issues:GH-1", [e["id"] for e in state.world_context["entities"]])
        self.assertTrue((self.logs / "run_manifest.jsonl").exists())

    def test_memory_prevents_duplicate_issue_comment_for_same_customer(self):
        first = CompletedActions()
        self.agent(first).run("Desktop sync stopped after the update", "c-031", "q1")
        self.assertIsNotNone(first.payloads[0]["comment"])

        second = CompletedActions()
        state = self.agent(second).run("Sync is still stuck since the update", "c-031", "q2")
        self.assertEqual(state.action.value, "link_issue")
        self.assertTrue(state.already_reported)
        self.assertIsNone(second.payloads[0]["comment"])
        self.assertIn("You reported this before", state.reply_text)

    def test_other_customer_still_gets_a_comment(self):
        self.agent(CompletedActions()).run("Desktop sync stopped after the update", "c-031", "q1")
        accounts = json.loads((data_dir() / "accounts.json").read_text())
        other = next(a["id"] for a in accounts
                     if a["id"] != "c-031" and a["client_version"] == "3.8.1")
        actions = CompletedActions()
        state = self.agent(actions).run("Desktop sync stopped after the update", other, "q3")
        self.assertFalse(state.already_reported)

    def test_dry_run_never_counts_as_already_reported(self):
        from resolve.actions import DryRunActions
        self.agent(DryRunActions()).run("Desktop sync stopped after the update", "c-031", "q1")
        state = self.agent(DryRunActions()).run("Sync is still stuck", "c-031", "q2")
        self.assertFalse(state.already_reported)
        self.assertTrue(state.memories["memories"])  # still retrieved for context


if __name__ == "__main__":
    unittest.main()
