"""Resolve the purple-at-midnight scenario through evidence and Discord routing."""

import asyncio
import json
import os
import sqlite3
import tempfile
import types
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

from resolve.actions import DryRunActions, LiveActions
from resolve.agent import Agent
from resolve.config import ROOT
from resolve.discord_bot import run_bot
from resolve.reasoning import CompatibleReasoner, HYPOTHESIS_PROMPT, RulesReasoner
from resolve.research import SQLResearcher, seed_snapshot
from resolve.state import Action


class WorkspaceAppearanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.database = self.directory / "support.sqlite"
        seed_snapshot(ROOT / "data", self.database)
        self.researcher = SQLResearcher.local(self.database)
        self.actions = DryRunActions()
        self.agent = Agent(self.researcher, RulesReasoner(), self.actions)

    def test_purple_at_midnight_returns_verified_kb_steps(self):
        state = self.agent.run("My workspace turns purple at midnight", "c-004", "purple")
        self.assertEqual(state.component, "appearance")
        self.assertEqual(state.action, Action.POST_FIX)
        self.assertTrue(state.verified)
        self.assertEqual(state.retries, 0)
        self.assertEqual(state.hypothesis.selected_id, "kb:KB-011")
        self.assertIn("Theme schedule", state.reply_text)
        self.assertIn("00:00 (midnight)", state.reply_text)
        self.assertIn("time zone", state.reply_text)
        self.assertIn("https://github.com/resolve-hackathon/resolve-agent/blob/main/"
                      "data/kb/011-purple-workspace-midnight.md", state.reply_text)
        self.assertNotIn("support engineer will follow up", state.reply_text)

    def test_paraphrases_and_supported_client_versions_use_the_kb(self):
        reports = (
            ("c-002", "Why does my workspace become violet every night at 12am?"),
            ("c-031", "The workspace changes to PURPLE at midnight"),
            ("c-003", "My theme switches purple at 00:00"),
        )
        for customer, message in reports:
            with self.subTest(customer=customer, message=message):
                state = self.agent.run(message, customer, customer)
                self.assertEqual(state.action, Action.POST_FIX)
                self.assertTrue(state.verified)
                self.assertEqual(state.hypothesis.selected_id, "kb:KB-011")

    def test_unrelated_appearance_symptoms_still_escalate(self):
        state = self.agent.run("My appearance flickers bright green when resizing", "c-004", "unknown-color")
        self.assertEqual(state.component, "appearance")
        self.assertEqual(state.action, Action.ESCALATE)
        self.assertEqual(state.retries, 1)
        self.assertNotIn("Theme schedule", state.reply_text)

    def test_uncovered_version_and_unknown_customer_still_escalate(self):
        with sqlite3.connect(self.database) as connection:
            record = json.loads(connection.execute(
                "SELECT payload FROM accounts WHERE row_id = ?", ("c-004",)
            ).fetchone()[0])
            record["client_version"] = "3.9.0"
            connection.execute("UPDATE accounts SET client_version = ?, payload = ? WHERE row_id = ?",
                               ("3.9.0", json.dumps(record), "c-004"))
        for customer in ("c-004", "unknown-discord-user"):
            with self.subTest(customer=customer):
                state = self.agent.run("My workspace turns purple at midnight", customer, customer)
                self.assertEqual(state.action, Action.ESCALATE)
                self.assertFalse(state.verified)

    def test_compatible_model_uses_the_same_version_checked_source(self):
        class Model:
            def request(self, method, path, body):
                payload = json.loads(body["messages"][1]["content"])
                if body["messages"][0]["content"] == HYPOTHESIS_PROMPT:
                    self_evidence = {row["id"] for row in payload["evidence"]}
                    if "kb:KB-011" not in self_evidence:
                        raise AssertionError("Model was not given the KB article")
                    result = {
                        "action": "post_fix", "confidence": 0.9, "selected_id": "kb:KB-011",
                        "supporting_ids": ["accounts:c-004", "kb:KB-011"],
                        "claims": [{"evidence_id": "accounts:c-004", "field": "client_version"},
                                   {"evidence_id": "kb:KB-011", "field": "steps"}],
                    }
                else:
                    result = {"verified": True, "gaps": []}
                return {"choices": [{"message": {"content": json.dumps(result)}}]}

        agent = Agent(self.researcher, CompatibleReasoner(Model(), "test-model"), self.actions)
        state = agent.run("My workspace turns purple at midnight", "c-004", "model-purple")
        self.assertTrue(state.verified)
        self.assertEqual(state.action, Action.POST_FIX)
        self.assertIn("kb:KB-011", state.reply_text)

    def test_live_fix_sends_discord_reply_without_github_writes(self):
        class NoGitHub:
            def request(self, *args):
                raise AssertionError("A known KB fix must not call GitHub")

        class Discord:
            calls = []
            def request(self, method, path, body):
                self.calls.append((method, path, body))
                return {"id": "456"}

        discord = Discord()
        live = LiveActions(NoGitHub(), discord, "resolve-hackathon/cloudnest", "123", "999", "789")
        state = Agent(self.researcher, RulesReasoner(), live).run(
            "My workspace turns purple at midnight", "c-004", "live-purple"
        )
        self.assertEqual(state.action, Action.POST_FIX)
        self.assertEqual(state.action_result["status"], "completed")
        self.assertEqual(len(discord.calls), 1)
        self.assertEqual(discord.calls[0][1], "/channels/123/messages")
        self.assertIn("Theme schedule", discord.calls[0][2]["content"])
        self.assertEqual(discord.calls[0][2]["message_reference"], {"message_id": "789"})

    def test_discord_command_and_mention_both_reach_the_verified_fix(self):
        with sqlite3.connect(self.database) as connection:
            sender_id = connection.execute(
                "SELECT discord_id FROM accounts WHERE row_id = ?", ("c-004",)
            ).fetchone()[0]

        @asynccontextmanager
        async def typing():
            yield

        bot_user = types.SimpleNamespace(id=999)

        class Message:
            author = types.SimpleNamespace(id=int(sender_id), bot=False)
            guild = types.SimpleNamespace(id=1)
            channel = types.SimpleNamespace(id=123, parent_id=None, typing=typing)
            webhook_id = None
            id = 789

            def __init__(self, content, mentions):
                self.content, self.mentions, self.replies = content, mentions, []

            async def reply(self, text, **kwargs):
                self.replies.append(text)
                return types.SimpleNamespace(id=len(self.replies))

        class Client:
            def __init__(self, **kwargs):
                self.user = bot_user
                self.handlers = {}

            def event(self, function):
                self.handlers[function.__name__] = function
                return function

            def run(self, token):
                asyncio.run(self.handlers["on_message"](message))

        discord = types.SimpleNamespace(
            Client=Client,
            Intents=types.SimpleNamespace(default=lambda: types.SimpleNamespace()),
            AllowedMentions=types.SimpleNamespace(none=lambda: None),
        )
        env = {"DISCORD_TOKEN": "test-token", "DISCORD_GUILD_ID": "1", "DISCORD_SUPPORT_CHANNEL_ID": "123"}
        for content, mentions in (
            ("!resolve My workspace turns purple at midnight", []),
            ("<@!999> My workspace turns purple at midnight", [bot_user]),
        ):
            with self.subTest(content=content):
                message = Message(content, mentions)
                with patch.dict(os.environ, env, clear=True), \
                     patch.dict("sys.modules", {"discord": discord}), \
                     patch("resolve.discord_bot.make_researcher", return_value=self.researcher), \
                     patch("resolve.discord_bot.runtime_dir", return_value=self.directory), \
                     patch("resolve.__main__.build_agent", side_effect=lambda db, actions: Agent(
                         self.researcher, RulesReasoner(), actions
                     )):
                    run_bot(self.database)
                self.assertEqual(len(message.replies), 1)
                self.assertIn("Theme schedule", message.replies[0])
                self.assertIn("kb:KB-011", message.replies[0])
                state = json.loads((self.directory / "investigations.jsonl").read_text().splitlines()[-1])
                self.assertEqual(state["action"], "post_fix")
                self.assertTrue(state["verified"])


if __name__ == "__main__":
    unittest.main()
