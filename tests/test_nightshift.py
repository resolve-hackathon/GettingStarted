"""Meterless Nightshift as the primary AI provider; sample mode needs nothing."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from resolve.actions import DryRunActions
from resolve.reasoning import CompatibleReasoner, RulesReasoner


class NightshiftTests(unittest.TestCase):
    def test_client_uses_nightshift_url_token_and_meterless_headers(self):
        env = {"METERLESS_RUNTIME_TOKEN": "test-token", "METERLESS_BASE_URL": "http://127.0.0.1:8088/v1",
               "METERLESS_MODEL": "nvidia/nemotron-3-super-120b-a12b", "MODEL_NO_THINK": "true",
               "MODEL_MAX_TOKENS": "2048"}
        with mock.patch.dict(os.environ, env, clear=False):
            reasoner = CompatibleReasoner.from_nightshift()
        self.assertEqual(reasoner.provider, "meterless-nightshift")
        self.assertEqual(reasoner.model, "nvidia/nemotron-3-super-120b-a12b")
        self.assertEqual(reasoner.client.base_url, "http://127.0.0.1:8088/v1")
        self.assertEqual(reasoner.client.extra_headers["meterless-app"], "resolve")

        captured = {}

        def fake_urlopen(request, timeout):
            captured["url"], captured["headers"] = request.full_url, dict(request.header_items())
            captured["body"] = json.loads(request.data)
            body = json.dumps({"model": "nvidia/nemotron-3-super-120b-a12b",
                               "choices": [{"finish_reason": "stop", "message": {
                                   "content": json.dumps({"component": "sync", "issue_type": "technical"})}}]})

            class Response:
                def __enter__(self): return self
                def __exit__(self, *a): return False
                def read(self, n): return body.encode()
            return Response()

        from resolve.state import TaskState
        state = TaskState("q", "sync stopped", "c-031")
        with mock.patch("resolve.transport.urlopen", fake_urlopen):
            reasoner.triage(state)
        self.assertEqual(captured["url"], "http://127.0.0.1:8088/v1/chat/completions")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer test-token")
        self.assertEqual(captured["body"]["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual(captured["body"]["max_tokens"], 2048)
        model_event = next(e for e in state.events if e["role"] == "model")
        self.assertEqual((model_event["provider"], model_event["served"]),
                         ("meterless-nightshift", "nvidia/nemotron-3-super-120b-a12b"))

    def test_missing_token_is_a_configuration_error(self):
        with mock.patch.dict(os.environ, {"METERLESS_RUNTIME_TOKEN": ""}, clear=False):
            with self.assertRaises(ValueError):
                CompatibleReasoner.from_nightshift()

    def test_sample_mode_forces_offline_rules_and_local_research(self):
        from resolve.__main__ import build_agent
        from resolve.config import data_dir
        from resolve.research import seed_snapshot
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "t.sqlite"
            seed_snapshot(data_dir(), database)
            env = {"SAMPLE_MODE": "true", "RESOLVE_MODEL_MODE": "nightshift", "RESOLVE_RESEARCH": "zetaris",
                   "RESOLVE_LOG_DIR": str(Path(directory) / "logs"),
                   "RESOLVE_MEMORY_DIR": str(Path(directory) / "memory")}
            with mock.patch.dict(os.environ, env, clear=False):
                agent = build_agent(database, DryRunActions())
                self.assertIsInstance(agent.reasoner, RulesReasoner)
                state = agent.run("Desktop sync stopped after the update", "c-031", "q")
        self.assertEqual(state.action.value, "link_issue")


if __name__ == "__main__":
    unittest.main()


class TransportShapeTests(unittest.TestCase):
    def test_streamed_answer_is_joined(self):
        from resolve.transport import _parse_sse
        raw = (b'data: {"model":"nvidia/nemotron-x","choices":[{"delta":{"content":"{\\"verified\\": "}}]}\n\n'
               b'data: {"model":"nvidia/nemotron-x","choices":[{"delta":{"content":"true, \\"gaps\\": []}"},'
               b'"finish_reason":"stop"}],"meterless":{"receipt_id":"r1"}}\n\ndata: [DONE]\n')
        body = _parse_sse(raw)
        self.assertEqual(json.loads(body["choices"][0]["message"]["content"]), {"verified": True, "gaps": []})
        self.assertEqual((body["model"], body["meterless"]["receipt_id"]), ("nvidia/nemotron-x", "r1"))

    def test_timeout_is_named(self):
        from resolve.transport import JSONClient, ServiceError
        client = JSONClient("http://127.0.0.1:8088/v1", "Bearer t", timeout=7)

        def slow(request, timeout):
            raise TimeoutError("timed out")
        with mock.patch("resolve.transport.urlopen", slow):
            with self.assertRaisesRegex(ServiceError, "within 7s"):
                client.request("POST", "/chat/completions", {})

    def test_nightshift_prompts_ask_for_no_thinking(self):
        with mock.patch.dict(os.environ, {"METERLESS_RUNTIME_TOKEN": "t"}, clear=False):
            reasoner = CompatibleReasoner.from_nightshift()
        self.assertTrue(reasoner.no_think)
        self.assertEqual(reasoner.max_tokens, 2048)
