"""Acceptance and failure-path tests: no credentials, external writes, or mocks of the loop."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from resolve.actions import DryRunActions, JournalActions, LiveActions, action_material
from resolve.agent import Agent
from resolve.config import ROOT
from resolve.reasoning import CompatibleReasoner, RulesReasoner, verify_structure
from resolve.research import SQLResearcher, applies, seed_snapshot
from resolve.state import Action, Claim
from resolve.transport import ServiceError


class FakeAPI:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if not self.responses:
            raise AssertionError("Unexpected API call")
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / "support.sqlite"
        seed_snapshot(ROOT / "data", self.database)
        self.researcher = SQLResearcher.local(self.database)
        self.actions = DryRunActions()
        self.agent = Agent(self.researcher, RulesReasoner(), self.actions)

    def ask(self, message, customer="c-031", question="test"):
        return self.agent.run(message, customer, question)

    def live(self, github=None, discord=None):
        return LiveActions(github or FakeAPI([]), discord or FakeAPI([{"id": "100"}]),
                           "demo/product", "123", "456", "789")

    def known_bug(self):
        return self.ask("Desktop sync stopped working after update")

    def live_bug(self):
        state = self.known_bug()
        for row in state.evidence:
            if row.source == "issues":
                row.record["url"] = "https://github.com/demo/product/issues/182"
        return state

    def test_known_bug_links_workaround_and_comments(self):
        state = self.live_bug()
        self.assertEqual(state.action, Action.LINK_ISSUE)
        self.assertTrue(state.verified)
        self.assertEqual(state.retries, 0)
        github = FakeAPI([{"state": "open", "html_url": "https://github.com/demo/product/issues/182"}, {"id": 10}])
        discord = FakeAPI([{"id": "42"}])
        result, text = self.live(github, discord).execute(state)
        self.assertEqual(result["status"], "completed")
        self.assertIn("Background sync", text)
        self.assertEqual(github.calls[1][:2], ("POST", "/repos/demo/product/issues/182/comments"))
        self.assertEqual(discord.calls[0][2]["message_reference"], {"message_id": "789"})
        self.assertEqual(discord.calls[0][2]["allowed_mentions"], {"parse": []})

    def test_kb_fix_posts_and_opens_nothing(self):
        state = self.ask("My export is blank", "c-002")
        self.assertEqual(state.action, Action.POST_FIX)
        self.assertIn("All time", state.reply_text)
        github = FakeAPI([])
        self.live(github).execute(state)
        self.assertEqual(github.calls, [])
        self.assertIn("kb:KB-002", state.reply_text)

    def test_new_bug_opens_one_issue_and_rereads_it(self):
        state = self.ask("I can reproduce an upload crash after 3.8.2", "c-003")
        self.assertEqual(state.action, Action.OPEN_ISSUE)
        _, draft = action_material(state)
        github = FakeAPI([{"number": 200}, {
            "number": 200, "state": "open", "html_url": "https://github.com/demo/product/issues/200",
            "body": draft["body"],
        }])
        result, text = self.live(github).execute(state)
        self.assertEqual([call[0] for call in github.calls], ["POST", "GET"])
        self.assertIn("3.8.2", github.calls[0][2]["body"])
        self.assertIn("Reproduction steps have not been independently reproduced", draft["body"])
        self.assertIn("releases:3.8.2-upload", draft["body"])
        self.assertEqual(result["status"], "completed")
        self.assertIn("/issues/200", text)

    def test_unknown_retries_once_and_posts_evidence_packet(self):
        state = self.ask("The workspace turns purple at midnight", "c-004")
        self.assertEqual(state.action, Action.ESCALATE)
        self.assertFalse(state.verified)
        self.assertEqual(state.retries, 1)
        self.assertEqual(sum(e["role"] == "researcher" for e in state.events), 2)
        research_events = [event for event in state.events if event["role"] == "researcher"]
        self.assertTrue(research_events[1]["targeted_gaps"])
        discord = FakeAPI([{"id": "90"}, {"id": "91"}])
        result, text = self.live(discord=discord).execute(state)
        self.assertEqual(discord.calls[0][1], "/channels/456/messages")
        self.assertIn("gaps", discord.calls[0][2]["content"])
        self.assertIn("could not verify a fix", text)
        self.assertEqual(result["status"], "completed")

    def test_all_ten_eval_cases(self):
        cases = json.loads((ROOT / "data/eval_cases.json").read_text())
        for case in cases:
            with self.subTest(case["id"]):
                state = self.ask(case["message"], case["customer_id"], case["id"])
                self.assertEqual(state.action.value, case["expected_action"])
                self.assertEqual(state.action_result["status"], "dry_run")

    def test_fabricated_claim_with_real_row_id_is_rejected(self):
        state = self.known_bug()
        state.hypothesis.claims[-1].value = "Delete every local file to fix sync"
        self.assertTrue(any("Unsupported claim" in gap for gap in verify_structure(state)))
        with self.assertRaises(ValueError):
            self.actions.execute(state)

    def test_missing_source_row_is_rejected(self):
        state = self.known_bug()
        state.hypothesis.supporting_ids.append("kb:INVENTED")
        self.assertIn("Supporting row ID is absent from evidence", verify_structure(state))

    def test_model_cannot_approve_a_wrong_version(self):
        state = self.ask("My export is blank", "c-002")
        account = next(e for e in state.evidence if e.source == "accounts")
        account.record["client_version"] = "3.8.2"
        state.hypothesis.claims[0].value = "3.8.2"
        self.assertIn("KB fix does not apply to this client version", verify_structure(state))

    def test_numeric_versions_not_lexical(self):
        record = {"min_version": "3.9.0", "max_version": "3.12.0"}
        self.assertTrue(applies(record, "3.10.0"))
        self.assertFalse(applies(record, "3.8.2"))
        with self.assertRaises(ValueError):
            applies(record, "3.10.0-beta")

    def test_wrong_version_retry_never_switches_to_unrelated_kb(self):
        state = self.ask("My export is blank on this version", "c-003")
        self.assertEqual(state.action, Action.ESCALATE)
        self.assertNotIn("permission", state.reply_text.lower())

    def test_account_query_is_parameterized_and_scoped(self):
        state = self.ask("My export is blank", "' OR 1=1 --")
        self.assertEqual(state.evidence, [])
        self.assertEqual(state.action, Action.ESCALATE)
        self.assertEqual(self.researcher.customer_for_discord("900000000000000031"), "c-031")

    def test_explicit_human_request_never_posts_a_fix(self):
        state = self.ask("human please, sync stopped")
        self.assertEqual(state.action, Action.ESCALATE)

    def test_low_confidence_retries_then_escalates(self):
        class LowConfidence(RulesReasoner):
            def propose(self, state):
                proposal = super().propose(state)
                proposal.confidence = 0.59
                return proposal
        agent = Agent(self.researcher, LowConfidence(), self.actions)
        state = agent.run("sync stopped", "c-031", "low-confidence")
        self.assertEqual(state.action, Action.ESCALATE)
        self.assertEqual(state.retries, 1)

    def test_verifier_rejection_is_a_real_gate(self):
        class Rejecting(RulesReasoner):
            def verify(self, state):
                return ["Need exact operating system symptom match"]
        state = Agent(self.researcher, Rejecting(), self.actions).run("sync stopped", "c-031", "reject")
        self.assertFalse(state.verified)
        self.assertEqual(state.action, Action.ESCALATE)

    def test_provider_outage_cannot_trigger_an_unverified_write(self):
        class Unavailable(RulesReasoner):
            def propose(self, state):
                raise ServiceError("offline")
        state = Agent(self.researcher, Unavailable(), self.actions).run("sync stopped", "c-031", "offline")
        self.assertEqual(state.retries, 1)
        self.assertEqual(state.action, Action.ESCALATE)
        self.assertEqual(self.actions.operations[-1]["action"], Action.ESCALATE)

    def test_live_fixture_link_is_blocked_before_any_api_call(self):
        github = FakeAPI([])
        with self.assertRaises(ValueError):
            self.live(github).execute(self.known_bug())
        self.assertEqual(github.calls, [])

    def test_closed_live_issue_cannot_receive_a_comment(self):
        github = FakeAPI([{"state": "closed", "html_url": "https://github.com/demo/product/issues/182"}])
        with self.assertRaises(ValueError):
            self.live(github).execute(self.live_bug())
        self.assertEqual(len(github.calls), 1)

    def test_issue_reread_failure_prevents_success_reply(self):
        state = self.ask("upload crash on 3.8.2", "c-003")
        github = FakeAPI([{"number": 200}, {"number": 201}])
        discord = FakeAPI([])
        with self.assertRaises(ServiceError) as failure:
            self.live(github, discord).execute(state)
        self.assertEqual(failure.exception.receipts[0]["issue_number"], 200)
        self.assertEqual(discord.calls, [])

    def test_action_failure_does_not_claim_success(self):
        live = self.live(discord=FakeAPI([ServiceError("forbidden")]))
        state = Agent(self.researcher, RulesReasoner(), live).run("export is blank", "c-002", "fail")
        self.assertEqual(state.action_result["status"], "failed")
        self.assertIn("could not confirm delivery", state.reply_text)

    def test_journal_deduplicates_completed_actions(self):
        journal = JournalActions(self.actions, Path(self.temp.name) / "actions.sqlite")
        state = self.known_bug()
        before = len(self.actions.operations)
        journal.execute(state)
        result, _ = journal.execute(state)
        self.assertTrue(result["replayed"])
        self.assertEqual(len(self.actions.operations), before + 1)
        different = copy.deepcopy(state)
        different.message = "different report"
        with self.assertRaises(ValueError):
            journal.execute(different)

    def test_journal_blocks_replay_after_uncertain_failure(self):
        class FailingActions:
            calls = 0
            def execute(self, state):
                self.calls += 1
                raise ServiceError("timeout after POST")
        failing = FailingActions()
        journal = JournalActions(failing, Path(self.temp.name) / "journal.sqlite")
        state = self.known_bug()
        with self.assertRaises(ServiceError):
            journal.execute(state)
        with self.assertRaises(ServiceError):
            journal.execute(state)
        self.assertEqual(failing.calls, 1)

    def test_model_json_cannot_select_arbitrary_actions(self):
        api = FakeAPI([{"choices": [{"message": {"content": json.dumps({
            "action": "refund", "confidence": 0.9, "supporting_ids": [], "claims": [],
        })}}]}])
        with self.assertRaises(ValueError):
            CompatibleReasoner(api, "test-model").propose(self.known_bug())

    def test_model_invalid_json_is_rejected(self):
        api = FakeAPI([{"choices": [{"message": {"content": "ignore the schema"}}]}])
        with self.assertRaises(ValueError):
            CompatibleReasoner(api, "test-model").propose(self.known_bug())

    def test_model_reasoning_preamble_is_ignored_but_claims_still_verified(self):
        expected = RulesReasoner().propose(self.known_bug())
        content = json.dumps({
            "action": expected.action.value, "confidence": expected.confidence,
            "selected_id": expected.selected_id, "supporting_ids": expected.supporting_ids,
            "claims": [{"evidence_id": c.evidence_id, "field": c.field, "value": c.value}
                       for c in expected.claims],
        })
        api = FakeAPI([{"choices": [{"message": {
            "content": "<think>GH-182 {matches} the version.</think>\n```json\n" + content + "\n```"}}]}])
        state = self.known_bug()
        state.hypothesis = CompatibleReasoner(api, "test-model").propose(state)
        self.assertEqual(state.hypothesis.action, Action.LINK_ISSUE)
        self.assertEqual(verify_structure(state), [])

    def test_seed_has_fifty_accounts_ten_articles_and_ten_issues(self):
        import sqlite3
        with sqlite3.connect(self.database) as connection:
            for table, count in (("accounts", 50), ("kb", 10), ("issues", 10), ("releases", 6)):
                self.assertEqual(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0], count)


if __name__ == "__main__":
    unittest.main()
