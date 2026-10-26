"""Plain-language questions: model triage plus unfiltered, scoped research."""

import json
import tempfile
import unittest
from pathlib import Path

from resolve.actions import DryRunActions
from resolve.agent import Agent
from resolve.config import data_dir
from resolve.reasoning import HYPOTHESIS_PROMPT, TRIAGE_PROMPT, CompatibleReasoner
from resolve.research import SQLResearcher, seed_snapshot, tokens


class FakeModel:
    """Triage and decide like a model would; verifier approves."""
    def __init__(self, triage=None, propose=None):
        self.triage, self.propose, self.seen = triage, propose, []

    def request(self, method, path, body):
        system = body["messages"][0]["content"]
        user = json.loads(body["messages"][1]["content"])
        if system == TRIAGE_PROMPT:
            out = self.triage
        elif system == HYPOTHESIS_PROMPT:
            self.seen = [e["id"] for e in user["evidence"]]
            out = self.propose
        else:
            out = {"verified": True, "gaps": []}
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(out)}}]}


LINK_GH1 = {"action": "link_issue", "confidence": 0.85, "selected_id": "issues:GH-1", "supporting_ids": [],
            "claims": [{"evidence_id": "accounts:c-031", "field": "client_version"},
                       {"evidence_id": "issues:GH-1", "field": "workaround"}]}


class PlainLanguageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.sqlite"
        seed_snapshot(data_dir(), self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def run_agent(self, model, message, customer="c-031"):
        agent = Agent(SQLResearcher.local(self.db, symptom_filter=False),
                      CompatibleReasoner(model, "test-model"), DryRunActions())
        return agent.run(message, customer, "q")

    def test_stems_and_two_word_forms(self):
        self.assertIn("sync", tokens("quit syncing"))
        self.assertIn("crash", tokens("uploads keep crashing"))
        self.assertIn("login", tokens("I can't log in"))

    def test_no_keyword_question_is_triaged_by_model_and_resolved(self):
        model = FakeModel({"component": "sync", "issue_type": "technical"}, LINK_GH1)
        state = self.run_agent(model, "files aren't showing up on my other computer")
        self.assertEqual(state.events[0]["method"], "model")
        self.assertIn("issues:GH-1", model.seen)
        self.assertEqual(state.action.value, "link_issue")

    def test_all_scoped_rows_reach_the_model(self):
        model = FakeModel({"component": "login", "issue_type": "technical"},
                          {"action": "escalate", "confidence": 0, "selected_id": None,
                           "supporting_ids": [], "claims": []})
        self.run_agent(model, "I can't log in anymore")
        self.assertTrue({"kb:KB-004", "kb:KB-010"} <= set(model.seen))

    def test_bad_model_triage_falls_back_to_keywords(self):
        model = FakeModel({"component": "teleportation", "issue_type": "technical"}, LINK_GH1)
        state = self.run_agent(model, "my desktop app quit syncing")
        self.assertEqual(state.component, "sync")
        self.assertEqual(state.events[0]["method"], "keywords (model unavailable)")

    def test_human_request_skips_model_triage(self):
        model = FakeModel({"component": "sync", "issue_type": "technical"}, LINK_GH1)
        state = self.run_agent(model, "I want a refund for my plan")
        self.assertEqual(state.events[0]["method"], "keywords")
        self.assertEqual(state.action.value, "escalate")


if __name__ == "__main__":
    unittest.main()
