import json, tempfile
from pathlib import Path
from resolve.agent import Agent
from resolve.actions import DryRunActions
from resolve.reasoning import CompatibleReasoner, HYPOTHESIS_PROMPT
from resolve.research import SQLResearcher, seed_snapshot
from resolve.config import data_dir

# Mimic what Nemotron did: bare ids, retyped/paraphrased values, account left out of supporting_ids.
ANSWERS = {
    "c-031": {"action": "link_issue", "confidence": 0.95, "selected_id": "GH-1", "supporting_ids": ["GH-1"],
              "claims": [{"evidence_id": "accounts:c-031", "field": "client_version"},
                         {"evidence_id": "GH-1", "field": "workaround", "value": "turn off background sync"}]},
    "c-002": {"action": "post_fix", "confidence": 0.9, "selected_id": "kb:KB-002", "supporting_ids": ["kb:KB-002"],
              "claims": [{"evidence_id": "c-002", "field": "client_version", "value": "3.7.4"},
                         {"evidence_id": "kb:KB-002", "field": "steps", "value": "Open Reports > Exports ..."}]},
    "c-003": {"action": "open_issue", "confidence": 0.9, "selected_id": "releases:3.8.2-upload",
              "supporting_ids": ["releases:3.8.2-upload"],
              "claims": [{"evidence_id": "accounts:c-003", "field": "client_version"},
                         {"evidence_id": "releases:3.8.2-upload", "field": "body", "value": "upload worker changed"},
                         {"evidence_id": "releases:3.8.2-upload", "field": "regression_signals"}]},
    "c-004": {"action": "escalate", "confidence": 0, "selected_id": None, "supporting_ids": [], "claims": []},
    # Pointer to a field that does not exist must still be rejected.
    "c-002-bad": {"action": "post_fix", "confidence": 0.9, "selected_id": "kb:KB-002", "supporting_ids": [],
              "claims": [{"evidence_id": "accounts:c-002", "field": "client_version"},
                         {"evidence_id": "kb:KB-002", "field": "made_up_field"}]},
}
class Fake:
    def __init__(self): self.case = None
    def request(self, method, path, body):
        system = body["messages"][0]["content"]; user = json.loads(body["messages"][1]["content"])
        assert all("id" in e for e in user["evidence"])
        out = ANSWERS[self.case] if system == HYPOTHESIS_PROMPT else {"verified": True, "gaps": []}
        return {"choices": [{"message": {"content": "<think>hmm</think>```json\n" + json.dumps(out) + "\n```"}}]}


import unittest


class ModelClaimPointerTests(unittest.TestCase):
    """Model claims point at source fields; values come from the rows, not the model."""

    def run_case(self, customer, case, message):
        fake = Fake()
        fake.case = case
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "t.sqlite"
            seed_snapshot(data_dir(), database)
            agent = Agent(SQLResearcher.local(database), CompatibleReasoner(fake, "test-model"), DryRunActions())
            return agent.run(message, customer, "q-" + case)

    def test_paraphrased_values_and_bare_ids_still_verify(self):
        cases = [("c-031", "c-031", "Desktop sync stopped after the update", "link_issue"),
                 ("c-002", "c-002", "Why is my export blank?", "post_fix"),
                 ("c-003", "c-003", "I can reproduce an upload crash after 3.8.2", "open_issue"),
                 ("c-004", "c-004", "My workspace plays a trumpet at noon", "escalate")]
        for customer, case, message, expected in cases:
            with self.subTest(case=case):
                self.assertEqual(self.run_case(customer, case, message).action.value, expected)

    def test_pointer_to_missing_field_is_rejected(self):
        state = self.run_case("c-002", "c-002-bad", "Why is my export blank?")
        self.assertEqual(state.action.value, "escalate")
        self.assertIn("Unsupported claim: kb:KB-002.made_up_field", state.gaps)


if __name__ == "__main__":
    unittest.main()
