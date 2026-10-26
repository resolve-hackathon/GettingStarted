"""The only action execution boundary. Models cannot supply API methods or URLs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path

from .reasoning import verify_structure
from .state import Action, TaskState
from .transport import JSONClient, ServiceError


def action_material(state: TaskState) -> tuple[str, dict]:
    """Public text uses exact source remedies, never model-composed claims."""
    if state.action == Action.ESCALATE:
        packet = {"question_id": state.question_id, "message": state.message,
                  "customer_id": state.customer_id, "component": state.component,
                  "gaps": state.gaps, "retries": state.retries,
                  "evidence": [{"id": e.id, "record": e.record} for e in state.evidence]}
        return "A human support engineer will follow up; I could not verify a fix.", packet
    if not state.verified or state.action != state.hypothesis.action or verify_structure(state):
        raise ValueError("Unverified state cannot execute a non-escalation action")
    selected = next(e for e in state.evidence if e.id == state.hypothesis.selected_id)
    account = next(e for e in state.evidence if e.source == "accounts")
    citation = f"Source: [{selected.id}] {selected.record['url']}"
    if state.action == Action.POST_FIX:
        steps = "\n".join(f"{i}. {step}" for i, step in enumerate(selected.record["steps"], 1))
        return f"{steps}\n\n{citation}", {}
    if state.action == Action.LINK_ISSUE:
        return f"{selected.record['workaround']}\n\n{citation}", {
            "number": selected.record["number"], "url": selected.record["url"],
            "comment": f"Resolve report {state.question_id}\n"
                       f"Client: {account.record['client_version']}\n"
                       f"Reported symptoms (untrusted customer text):\n{state.message}\n"
                       f"Evidence: {', '.join(state.hypothesis.supporting_ids)}",
        }
    body = (f"Resolve report: {state.question_id}\n"
            f"Component: {state.component}\nClient: {account.record['client_version']}\n\n"
            f"## Customer report (untrusted text)\n{state.message}\n\n"
            "Reproduction steps have not been independently reproduced; follow up with reporter.\n\n"
            f"## Corroborating release note\n{selected.record['body']}\n\n{citation}\n"
            f"Evidence rows: {', '.join(state.hypothesis.supporting_ids)}")
    return "A bug report has been opened for the support team to investigate.", {
        "title": f"[Resolve] {state.component} regression on {account.record['client_version']}",
        "body": body, "labels": [f"component:{state.component}"],
    }


class DryRunActions:
    """Record intended operations without pretending external receipts exist."""
    def __init__(self):
        self.operations = []

    def execute(self, state: TaskState) -> tuple[dict, str]:
        text, payload = action_material(state)
        operation = {"action": state.action, "payload": payload, "text": text}
        self.operations.append(operation)
        return {"status": "dry_run", "planned": operation}, f"[DRY RUN: {state.action.value}]\n{text}"


class LiveActions:
    def __init__(self, github, discord, repository: str, channel_id: str,
                 escalation_channel_id: str, message_id: str | None = None):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("GITHUB_REPOSITORY must be owner/repository")
        for value in (channel_id, escalation_channel_id):
            if not value.isdigit():
                raise ValueError("Discord channel IDs must be numeric")
        self.github, self.discord, self.repository = github, discord, repository
        self.channel_id, self.escalation_channel_id = channel_id, escalation_channel_id
        self.message_id = message_id
        self.receipts = []

    @classmethod
    def from_env(cls, channel_id: str, message_id: str | None = None):
        if os.getenv("RESOLVE_ALLOW_LIVE_ACTIONS", "false").lower() != "true":
            raise ValueError("Live actions require RESOLVE_ALLOW_LIVE_ACTIONS=true")
        required = ["GITHUB_TOKEN", "GITHUB_REPOSITORY", "DISCORD_TOKEN",
                    "DISCORD_ESCALATION_CHANNEL_ID"]
        if any(not os.getenv(key) for key in required):
            raise ValueError("Live actions require GitHub, Discord, and escalation configuration")
        return cls(JSONClient("https://api.github.com", "Bearer " + os.environ["GITHUB_TOKEN"]),
                   # Discord rejects REST clients without a DiscordBot (url, version) agent.
                   JSONClient("https://discord.com/api/v10", "Bot " + os.environ["DISCORD_TOKEN"],
                              user_agent="DiscordBot (https://github.com/resolve-hackathon/GettingStarted, 0.1)"),
                   os.environ["GITHUB_REPOSITORY"], channel_id,
                   os.environ["DISCORD_ESCALATION_CHANNEL_ID"], message_id)

    def send(self, channel: str, text: str, *, reply: bool = False) -> None:
        for offset in range(0, len(text), 1800):
            payload = {"content": text[offset:offset + 1800], "allowed_mentions": {"parse": []}}
            if reply and self.message_id:
                payload["message_reference"] = {"message_id": self.message_id}
            receipt = self.discord.request("POST", f"/channels/{channel}/messages", payload)
            if not isinstance(receipt, dict) or not receipt.get("id"):
                raise ServiceError("Discord did not return a message ID")
            self.receipts.append({"service": "discord", "channel_id": channel,
                                  "message_id": str(receipt["id"])})

    def execute(self, state: TaskState) -> tuple[dict, str]:
        self.receipts = []
        text, payload = action_material(state)
        root = f"/repos/{self.repository}"
        try:
            if state.action == Action.LINK_ISSUE:
                number = payload["number"]
                if type(number) is not int or number < 1:
                    raise ValueError("Issue number must be a positive integer")
                expected = f"https://github.com/{self.repository}/issues/{number}"
                if payload["url"] != expected:
                    raise ValueError("Fixture issue URL is not in the configured live repository")
                # Re-read before writing, so stale/closed fixture issues cannot be used.
                issue = self.github.request("GET", f"{root}/issues/{number}")
                if issue.get("state") != "open" or issue.get("html_url") != expected or "pull_request" in issue:
                    raise ValueError("Live issue is no longer an open issue in this repository")
                receipt = self.github.request("POST", f"{root}/issues/{number}/comments",
                                              {"body": payload["comment"]})
                if not receipt.get("id"):
                    raise ServiceError("GitHub did not confirm the comment")
                self.receipts.append({"service": "github", "comment_id": receipt["id"]})
            elif state.action == Action.OPEN_ISSUE:
                issue = self.github.request("POST", f"{root}/issues", payload)
                number = issue.get("number")
                if type(number) is not int or number < 1:
                    raise ServiceError("GitHub did not confirm an issue number")
                self.receipts.append({"service": "github", "issue_number": number})
                confirmed = self.github.request("GET", f"{root}/issues/{number}")
                expected = f"https://github.com/{self.repository}/issues/{number}"
                if (confirmed.get("number") != number or confirmed.get("html_url") != expected
                        or confirmed.get("state") != "open" or "pull_request" in confirmed
                        or confirmed.get("body") != payload["body"]):
                    raise ServiceError("Created issue failed re-read verification")
                text += f"\n{expected}"
            elif state.action == Action.ESCALATE:
                packet = "Resolve escalation\n" + json.dumps(payload, ensure_ascii=False, indent=2)
                self.send(self.escalation_channel_id, packet)
            self.send(self.channel_id, text, reply=True)
            return {"status": "completed", "receipts": list(self.receipts)}, text
        except (ServiceError, ValueError, OSError) as error:
            error.receipts = list(self.receipts)
            raise


class JournalActions:
    """Persist one action attempt per question ID, including interrupted attempts.

    A timeout after a POST may still have written remotely. Block automatic replay
    of incomplete/failed actions rather than risking duplicate public issues.
    """
    def __init__(self, actions, database: Path):
        self.actions, self.database = actions, database
        database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(database) as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS actions (
                question_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                status TEXT NOT NULL, result TEXT)""")

    def execute(self, state: TaskState) -> tuple[dict, str]:
        fingerprint = hashlib.sha256(json.dumps([state.customer_id, state.message]).encode()).hexdigest()
        with sqlite3.connect(self.database) as connection:
            inserted = connection.execute("INSERT OR IGNORE INTO actions VALUES (?, ?, 'started', NULL)",
                                          (state.question_id, fingerprint)).rowcount
            if not inserted:
                prior = connection.execute("SELECT fingerprint, status, result FROM actions WHERE question_id = ?",
                                           (state.question_id,)).fetchone()
                if prior[0] != fingerprint:
                    raise ValueError("Question ID was already used for different input")
                if prior[1] == "completed":
                    result, reply = json.loads(prior[2])
                    return {**result, "replayed": True}, reply
                raise ServiceError("Action already attempted; reconcile service receipts before retrying")
        try:
            result, reply = self.actions.execute(state)
        except (ServiceError, ValueError, OSError) as error:
            with sqlite3.connect(self.database) as connection:
                connection.execute("UPDATE actions SET status='failed', result=? WHERE question_id=?",
                                   (json.dumps({"receipts": getattr(error, "receipts", [])}), state.question_id))
            raise
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE actions SET status='completed', result=? WHERE question_id=?",
                               (json.dumps([result, reply]), state.question_id))
        return result, reply
