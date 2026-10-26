"""Local SQL fallback and a DB-API seam for the federated researcher.

The SQLite snapshot is intentionally a FALLBACK, not a Zetaris integration.
The research contract returns Evidence objects and never executes model SQL.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Callable, Protocol

from .config import ROOT
from .state import Evidence, TaskState


def version(value: str) -> tuple[int, int, int]:
    """MVP supports stable major.minor.patch releases only; reject ambiguous tags."""
    if not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise ValueError(f"Unsupported version: {value!r}; expected major.minor.patch")
    return tuple(int(part) for part in value.split("."))


def applies(record: dict, client_version: str) -> bool:
    current = version(client_version)
    return version(record["min_version"]) <= current <= version(record["max_version"])


JOINED = {("log", "in"): "login", ("sign", "in"): "signin", ("set", "up"): "setup"}


def stem(word: str) -> str:
    """Tiny suffix stripper so "syncing", "uploads", "crashes" match "sync", "upload", "crash"."""
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)]
            break
    return word


def tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    result = set(words) | {stem(w) for w in words}
    result |= {JOINED[pair] for pair in zip(words, words[1:]) if pair in JOINED}
    return result


def matches(record: dict, message: str, *, expanded: bool = False) -> bool:
    terms = set(record.get("terms", []))
    words = tokens(message)
    if terms & words:
        return True
    if not expanded:
        return False
    # A retry searches article bodies for symptom words. Generic component names
    # cannot turn a blank-export question into an unrelated permissions fix.
    generic = {
        "the", "and", "a", "to", "is", "on", "for", "in", "of", "it", "your",
        "my", "i", "this", "that", "why", "how", "can", "need", "help", "with",
        "after", "before", "version", "client", "update", "issue", "problem", "an",
        "sync", "export", "exports", "csv", "pdf", "upload", "uploads", "login",
        "password", "notifications", "search", "sharing", "workspace", "cloudnest",
    }
    # Exact words only here: stems would let "crashing" match an article that says
    # it does NOT cover crashes. Stems are for the curated terms above.
    raw = set(re.findall(r"[a-z0-9]+", message.lower()))
    return bool((raw - generic) & set(re.findall(r"[a-z0-9]+", record.get("body", "").lower())))


class Researcher(Protocol):
    def research(self, state: TaskState) -> list[Evidence]: ...


def seed_snapshot(data: Path, database: Path) -> None:
    """Build a reproducible local snapshot from separately authored source files."""
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS accounts (
                row_id TEXT PRIMARY KEY, discord_id TEXT UNIQUE,
                client_version TEXT, last_working_version TEXT, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS kb (
                row_id TEXT PRIMARY KEY, component TEXT, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS releases (
                row_id TEXT PRIMARY KEY, component TEXT, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS issues (
                row_id TEXT PRIMARY KEY, component TEXT, state TEXT,
                affects_version TEXT, payload TEXT NOT NULL);
        """)
        for table in ("accounts", "kb", "releases", "issues"):
            connection.execute(f"DELETE FROM {table}")  # constant allowlist above
        # Real Discord user IDs for demo testers live in an ignored local file, so the
        # committed data keeps placeholders: {"c-031": "<discord user id>", ...}
        links_path = data / "discord_links.local.json"
        links = json.loads(links_path.read_text()) if links_path.exists() else {}
        if not all(isinstance(v, str) and v.isdigit() for v in links.values()):
            raise ValueError("discord_links.local.json values must be numeric Discord user IDs")
        for row in json.loads((data / "accounts.json").read_text()):
            version(row["client_version"])
            version(row["last_working_version"])
            if row["id"] in links:
                row = {**row, "discord_id": links[row["id"]]}
            connection.execute("INSERT INTO accounts VALUES (?, ?, ?, ?, ?)", (
                row["id"], row["discord_id"], row["client_version"],
                row["last_working_version"], json.dumps(row)))
        for path in sorted((data / "kb").glob("*.md")):
            text = path.read_text(encoding="utf-8")
            metadata, sep, body = text.partition("-->\n")
            if not sep or not metadata.startswith("<!-- resolve: "):
                raise ValueError(f"Missing Resolve metadata: {path.name}")
            row = json.loads(metadata.removeprefix("<!-- resolve: ").strip())
            version(row["min_version"])
            version(row["max_version"])
            row["body"] = body.strip()
            row["steps"] = re.findall(r"^\d+\. (.+)$", body, re.MULTILINE)
            row["url"] = (
                "https://github.com/resolve-hackathon/resolve-agent/blob/main/"
                f"data/kb/{path.name}"
            )
            connection.execute("INSERT INTO kb VALUES (?, ?, ?)", (
                row["id"], row["component"], json.dumps(row)))
        changelog = (data / "CHANGELOG.md").read_text(encoding="utf-8")
        # Each release section carries one machine-readable component record.
        for chunk in changelog.split("\n## ")[1:]:
            heading, _, body = chunk.partition("\n")
            tag = re.search(r"<!-- resolve: (.+?) -->", body)
            if not tag:
                raise ValueError(f"Missing release metadata: {heading}")
            row = json.loads(tag.group(1))
            version(row["version"])
            row["body"] = re.sub(r"<!--.*?-->", "", body).strip()
            row["url"] = (
                "https://github.com/resolve-hackathon/resolve-agent/blob/main/"
                "data/CHANGELOG.md"
            )
            connection.execute("INSERT INTO releases VALUES (?, ?, ?)", (
                row["id"], row["component"], json.dumps(row)))
        for row in json.loads((data / "issues.json").read_text()):
            connection.execute("INSERT INTO issues VALUES (?, ?, ?, ?, ?)", (
                row["id"], row["component"], row["state"],
                row["affects_version"], json.dumps(row)))


class SQLResearcher:
    """Accept a connection factory so a real federated driver can replace SQLite.

    A Zetaris adapter must supply equivalent virtual views/rows and a query with
    its driver's placeholders. Connection credentials stay outside task state.
    """

    def __init__(self, connect: Callable, query: str | None = None,
                 identity_query: str = "SELECT row_id FROM accounts WHERE discord_id = ?",
                 symptom_filter: bool = True):
        self.connect = connect
        # True: keep only rows whose keywords match the report (deterministic baseline).
        # False: keep every component- and version-scoped row; the model judges relevance.
        self.symptom_filter = symptom_filter
        self.query = query or (ROOT / "sql/research.sql").read_text()
        self.identity_query = identity_query

    @classmethod
    def local(cls, database: Path, symptom_filter: bool = True) -> "SQLResearcher":
        return cls(lambda: sqlite3.connect(database), symptom_filter=symptom_filter)

    def customer_for_discord(self, discord_id: str) -> str | None:
        connection = self.connect()
        try:
            cursor = connection.cursor()
            cursor.execute(self.identity_query, (discord_id,))
            row = cursor.fetchone()
            return row[0] if row else None
        finally:
            connection.close()

    def research(self, state: TaskState) -> list[Evidence]:
        connection = self.connect()
        try:
            cursor = connection.cursor()
            cursor.execute(self.query, (state.customer_id, *([state.component] * 3)))
            rows = [Evidence(source, row_id, json.loads(payload))
                    for source, row_id, payload in cursor.fetchall()]
        finally:
            connection.close()
        account = next((r.record for r in rows if r.source == "accounts"), None)
        if account is None:
            return []
        result = []
        for row in rows:
            record = row.record
            if row.source == "kb" and not applies(record, account["client_version"]):
                continue
            if row.source == "releases" and not (
                version(account["last_working_version"]) < version(record["version"])
                <= version(account["client_version"])
            ):
                continue
            if self.symptom_filter and row.source != "accounts" and not matches(
                record, state.message, expanded=bool(state.gaps)
            ):
                continue
            result.append(row)
        return result
