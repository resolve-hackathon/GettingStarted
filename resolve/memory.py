"""Meterless H-MEM Agent Engine, minimum services for Resolve.

- MemoryStoreService:     tiered records (short_term in-process, working and
                          long_term persisted as JSONL), warm start on load,
                          de-duplicated by id. Audit trail in logs/memory_audit.jsonl.
- MemoryRetrievalService: hybrid ranking (keyword + tag + entity + layer +
                          recency), threshold 0.35, top 5, returned as a
                          structured payload with trace metadata.

Mining uses the guide's no-model fallback: each resolved run is stored as a
concise direct summary, so capture never depends on an extraction model.
Verified, completed outcomes go to long_term; escalations stay in working.
Dreaming, sleep and conflict resolution are not implemented.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .state import Action, TaskState

ENGINE = "meterless/h-mem"
LAYER_WEIGHT = {"long_term": 0.3, "working": 0.2, "short_term": 0.1}
THRESHOLD, TOP_N = 0.35, 5


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9.\-]+", text.lower())) - {"the", "a", "my", "is", "after", "and", "i", "to"}


class MemoryStore:
    """MemoryStoreService."""

    def __init__(self, memory_dir: Path | None, log_dir: Path | None):
        self.memory_dir, self.log_dir = memory_dir, log_dir
        self.records: dict[str, dict] = {}
        for directory in (memory_dir, log_dir):
            if directory:
                directory.mkdir(parents=True, exist_ok=True)
        self._hydrate()

    def _hydrate(self) -> None:
        if not self.memory_dir:
            return
        for layer in ("long_term", "working"):  # warm start: durable tiers only
            path = self.memory_dir / f"{layer}.jsonl"
            if path.exists():
                for line in path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        record = json.loads(line)
                        self.records[record["id"]] = record  # later lines win: de-dup by id

    def _audit(self, op: str, record: dict, **extra) -> None:
        if self.log_dir:
            with (self.log_dir / "memory_audit.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"engine": ENGINE, "op": op, "id": record["id"],
                                         "layer": record["layer"], "runId": record.get("runId"),
                                         "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                         **extra}) + "\n")

    def add(self, record: dict) -> None:
        self.records[record["id"]] = record
        if self.memory_dir and record["layer"] != "short_term":
            with (self.memory_dir / f"{record['layer']}.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._audit("add", record)

    def touch(self, record: dict) -> None:
        record["lastAccessed"] = time.time()
        record["accessCount"] = record.get("accessCount", 0) + 1


class MemoryRetrieval:
    """MemoryRetrievalService: hybrid ranking with an explicit trace."""

    def __init__(self, store: MemoryStore):
        self.store = store

    def retrieve(self, state: TaskState) -> dict:
        query_words = _words(state.message)
        query_entities = {f"customer:{state.customer_id}", f"component:{state.component}"}
        now = time.time()
        scored = []
        for record in self.store.records.values():
            words = _words(record["content"])
            keyword = len(query_words & words) / max(len(query_words), 1)
            tags = len(set(record.get("tags", [])) & {state.component}) * 0.1
            entities = len(set(record.get("entities", [])) & query_entities) * 0.2
            recency = 0.05 if now - record.get("timestamp", 0) < 86400 else 0.0
            score = round(0.4 * keyword + tags + entities + LAYER_WEIGHT[record["layer"]] + recency, 3)
            if score >= THRESHOLD:
                scored.append((score, record))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        chosen = scored[:TOP_N]
        for _, record in chosen:
            self.store.touch(record)
        return {
            "engine": ENGINE, "strategy": "task_focused",
            "retrievalReason": "same customer, same component, and keyword overlap with the report",
            "memories": [{"id": r["id"], "content": r["content"], "type": r["type"], "layer": r["layer"],
                          "tags": r.get("tags", []), "entities": r.get("entities", []),
                          "relevance": s} for s, r in chosen],
        }


class Memory:
    """Mining (no-model fallback) plus the two services."""

    def __init__(self, memory_dir: Path | None, log_dir: Path | None):
        self.store = MemoryStore(memory_dir, log_dir)
        self.retrieval = MemoryRetrieval(self.store)

    def mine(self, state: TaskState) -> None:
        status = state.action_result.get("status")
        if status not in {"completed", "dry_run"}:
            return
        account = next((e.record for e in state.evidence if e.source == "accounts"), {})
        selected = state.hypothesis.selected_id or "none"
        summary = {
            Action.LINK_ISSUE: f"linked open issue {selected} and posted its workaround",
            Action.POST_FIX: f"posted the fix from {selected}",
            Action.OPEN_ISSUE: f"opened a new bug report based on {selected}",
            Action.ESCALATE: "escalated to a human; gaps: " + "; ".join(state.gaps[:2]),
        }[state.action]
        tags = [state.component, state.action.value]
        if state.action == Action.LINK_ISSUE and status == "completed":
            # Only a confirmed live comment counts as "already reported"; a dry run never does.
            tags.append(f"linked:{selected}")
        record = {
            "id": f"mem-{state.question_id}", "type": "factual", "domain": "support",
            "namespace": f"support/{state.component}",
            "layer": "long_term" if state.verified and state.action != Action.ESCALATE else "working",
            "content": f"Customer {state.customer_id} on client {account.get('client_version', '?')} "
                       f"reported a {state.component} problem (\"{state.message[:120]}\"); Resolve {summary}.",
            "tags": tags,
            "entities": [f"customer:{state.customer_id}", f"component:{state.component}",
                         *state.hypothesis.supporting_ids],
            "confidence": state.hypothesis.confidence, "source": f"resolver:{status}",
            "timestamp": time.time(), "lastAccessed": time.time(), "accessCount": 0,
            "relatedTo": [], "supersedes": None, "derivedFrom": [e.id for e in state.evidence],
            "agentId": "resolver", "runId": state.question_id, "taskId": f"attempt-{state.retries}",
        }
        self.store.add(record)
