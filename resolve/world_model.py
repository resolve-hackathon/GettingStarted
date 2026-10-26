"""Meterless World Model Agent Engine, minimum services for Resolve.

Implements the three services the Meterless guide lists as the minimum:

- WorldStoreService:      append-only JSONL world log (logs/world.jsonl) plus an
                          in-memory index; every record carries provenance and
                          agent attribution (agentId = role, runId = question_id).
- IngestPipelineService:  idempotent ingest of research evidence and role
                          decisions into entities, relationships and events.
- ContextBuilderService:  a structured, budgeted world payload for the model,
                          with trace metadata (selectionReason, relevance).

Every role reads and writes this shared state, and the model receives the
context builder's payload. The run manifest (logs/run_manifest.jsonl) records
which helpers are wired, per the guide's wired-vs-helper rule.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from .state import TaskState

ENGINE = "meterless/world-model"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:16]


class WorldStore:
    """WorldStoreService: write-through, append-only, de-duplicated by content."""

    def __init__(self, log_dir: Path | None):
        self.log_dir = log_dir
        self.entities: dict[str, dict] = {}
        self.relationships: dict[str, dict] = {}
        self.events: list[dict] = []
        self._seen: set[str] = set()
        if log_dir:
            log_dir.mkdir(parents=True, exist_ok=True)

    def _append(self, kind: str, record: dict) -> bool:
        key = f"{kind}:{record['id']}:{record.get('runId')}:{_digest(record.get('content'))}"
        if key in self._seen:  # idempotent: re-ingesting the same observation is a no-op
            return False
        self._seen.add(key)
        if self.log_dir:
            with (self.log_dir / "world.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"kind": kind, **record}, ensure_ascii=False, default=str) + "\n")
        return True

    def put_entity(self, record: dict) -> None:
        if self._append("entity", record):
            self.entities[record["id"]] = record

    def put_relationship(self, record: dict) -> None:
        if self._append("relationship", record):
            self.relationships[record["id"]] = record

    def put_event(self, record: dict) -> None:
        if self._append("event", record):
            self.events.append(record)


class IngestPipeline:
    """IngestPipelineService: evidence and decisions become provenance-stamped records."""

    TYPES = {"accounts": "customer", "kb": "kb_article", "releases": "release", "issues": "issue"}

    def __init__(self, store: WorldStore, via: str):
        self.store, self.via = store, via

    def ingest_evidence(self, state: TaskState, agent_id: str = "researcher") -> None:
        for evidence in state.evidence:
            record = evidence.record
            entity_id = evidence.id
            self.store.put_entity({
                "id": entity_id, "type": self.TYPES.get(evidence.source, evidence.source),
                "name": record.get("id", evidence.row_id),
                "content": record,
                "semantics": {k: record[k] for k in ("component", "version", "client_version",
                                                       "state", "affects_version") if k in record},
                "provenance": {"source": evidence.source, "rowId": evidence.row_id,
                               "via": self.via, "url": record.get("url"), "observedAt": _now()},
                "lifecycle": {"state": record.get("state", "active")},
                "agentId": agent_id, "runId": state.question_id, "taskId": f"attempt-{state.retries}",
            })
            for edge_type, target in self._edges(evidence.source, record, state):
                self.store.put_relationship({
                    "id": f"{entity_id}->{edge_type}->{target}", "type": edge_type,
                    "from": entity_id, "to": target, "content": None,
                    "provenance": {"source": evidence.source, "rowId": evidence.row_id, "via": self.via},
                    "agentId": agent_id, "runId": state.question_id,
                })

    @staticmethod
    def _edges(source: str, record: dict, state: TaskState):
        if source == "accounts":
            yield "runs_version", f"version:{record.get('client_version')}"
        elif source == "issues":
            yield "affects", f"version:{record.get('affects_version')}"
            yield "in_component", f"component:{record.get('component')}"
        elif source == "releases":
            yield "released_as", f"version:{record.get('version')}"
            yield "in_component", f"component:{record.get('component')}"
        elif source == "kb":
            yield "in_component", f"component:{record.get('component')}"

    def ingest_decision(self, state: TaskState, role: str, decision: dict) -> None:
        self.store.put_event({
            "id": f"{state.question_id}:{role}:{state.retries}", "type": f"{role}.decision",
            "content": decision, "cites": decision.get("supporting_ids", []),
            "agentId": role, "runId": state.question_id, "taskId": f"attempt-{state.retries}",
            "at": _now(),
        })


class ContextBuilder:
    """ContextBuilderService: structured payload, not a pre-rendered string."""

    def __init__(self, store: WorldStore, budget: int = 12):
        self.store, self.budget = store, budget

    def build(self, state: TaskState, intent: str) -> dict:
        run = [e for e in self.store.entities.values() if e["runId"] == state.question_id]
        cited = set(state.hypothesis.supporting_ids)

        def relevance(entity: dict) -> float:
            score = 0.5
            if entity["id"] in cited:
                score += 0.4  # mention-prioritized first
            if entity["semantics"].get("component") == state.component:
                score += 0.1
            return round(score, 2)

        ranked = sorted(run, key=relevance, reverse=True)[: self.budget]
        ids = {e["id"] for e in ranked}
        edges = [r for r in self.store.relationships.values()
                 if r["from"] in ids and r["runId"] == state.question_id]
        return {
            "engine": ENGINE, "intent": intent,
            "entities": [{"id": e["id"], "type": e["type"], "semantics": e["semantics"],
                          "provenance": e["provenance"]} for e in ranked],
            "relationships": [{"from": r["from"], "type": r["type"], "to": r["to"]} for r in edges],
            "trace": {"strategy": "task_focused",
                      "selectionReason": "entities observed in this run; cited ones first, then same component",
                      "items": [{"id": e["id"], "relevance": relevance(e)} for e in ranked]},
        }


class WorldModel:
    """The three services mounted together for one agent process."""

    def __init__(self, log_dir: Path | None, via: str = "local"):
        self.store = WorldStore(log_dir)
        self.ingest = IngestPipeline(self.store, via)
        self.context = ContextBuilder(self.store)
        self.log_dir = log_dir

    def write_manifest(self, state: TaskState, wired: dict) -> None:
        if not self.log_dir:
            return
        with (self.log_dir / "run_manifest.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"runId": state.question_id, "at": _now(), "engines": wired}) + "\n")
