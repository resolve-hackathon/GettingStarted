"""Five sequential roles with a strict maximum of one research retry."""

from .reasoning import triage, verify_structure
from .state import Action, Proposal, TaskState
from .transport import ServiceError


class Agent:
    def __init__(self, researcher, reasoner, actions, world=None, memory=None):
        self.researcher, self.reasoner, self.actions = researcher, reasoner, actions
        self.world, self.memory = world, memory  # Meterless World Model and H-MEM (optional)

    def run(self, message: str, customer_id: str, question_id: str) -> TaskState:
        if not message.strip() or len(message) > 4000:
            raise ValueError("Message must contain 1 to 4000 characters")
        state = TaskState(question_id, message, customer_id)
        triage(state)  # keyword baseline, also the fallback
        method = "keywords"
        if hasattr(self.reasoner, "triage") and state.issue_type != "human_required":
            try:
                self.reasoner.triage(state)
                method = "model"
            except (ServiceError, ValueError, OSError):
                triage(state)  # model unavailable: keep the keyword result
                method = "keywords (model unavailable)"
        state.record_event("triage", customer_id=customer_id, component=state.component, method=method)
        if self.memory:
            state.memories = self.memory.retrieval.retrieve(state)
            state.record_event("memory", engine="meterless/h-mem",
                               memory_ids=[m["id"] for m in state.memories["memories"]])
        for attempt in range(2):
            state.retries, state.verified = attempt, False
            state.hypothesis = Proposal()
            state.evidence = []
            try:
                state.evidence = self.researcher.research(state)
                state.record_event("researcher", evidence_ids=[e.id for e in state.evidence],
                                   targeted_gaps=list(state.gaps))
                if self.world:
                    self.world.ingest.ingest_evidence(state)
                    state.world_context = self.world.context.build(state, "select a support outcome")
                state.hypothesis = self.reasoner.propose(state)
                state.record_event("hypothesis", action=state.hypothesis.action,
                                   confidence=state.hypothesis.confidence,
                                   supporting_ids=state.hypothesis.supporting_ids)
                if self.world:
                    self.world.ingest.ingest_decision(state, "hypothesis", {
                        "action": state.hypothesis.action.value, "confidence": state.hypothesis.confidence,
                        "supporting_ids": state.hypothesis.supporting_ids})
                    state.world_context = self.world.context.build(state, "verify the proposed outcome")
                gaps = verify_structure(state)
                if not gaps:
                    gaps = self.reasoner.verify(state)
                state.gaps, state.verified = gaps, not gaps
            except (ServiceError, ValueError, OSError) as error:
                state.gaps = [f"Investigation unavailable ({type(error).__name__}: {str(error)[:160]})"]
            state.record_event("verifier", verified=state.verified, gaps=list(state.gaps))
            if state.verified:
                break
            if self.world:
                self.world.ingest.ingest_decision(state, "verifier", {
                    "verified": state.verified, "gaps": list(state.gaps)})
        state.action = state.hypothesis.action if state.verified else Action.ESCALATE
        if state.action == Action.LINK_ISSUE and state.memories:
            # H-MEM: this customer's report is already on this issue; do not comment twice.
            marker = f"linked:{state.hypothesis.selected_id}"
            prior = [m for m in state.memories.get("memories", [])
                     if marker in m["tags"] and f"customer:{customer_id}" in m["entities"]]
            if prior:
                state.already_reported = True
                state.record_event("memory", engine="meterless/h-mem", reused=prior[0]["id"],
                                   effect="skip duplicate GitHub comment")
        try:
            state.action_result, state.reply_text = self.actions.execute(state)
        except (ServiceError, ValueError, OSError) as error:
            # Do not claim an issue, comment, or escalation exists if its API failed.
            state.action_result = {"status": "failed", "error": type(error).__name__, "message": str(error)[:200],
                                   "receipts": getattr(error, "receipts", []),
                                   "detail": "Action was not confirmed; inspect service logs before retrying"}
            state.reply_text = "I could not confirm delivery. Please contact a support team member directly."
        state.record_event("resolver", action=state.action,
                           status=state.action_result.get("status", "unknown"))
        if self.world:
            self.world.ingest.ingest_decision(state, "resolver", {
                "action": state.action.value, "status": state.action_result.get("status"),
                "already_reported": state.already_reported})
            self.world.write_manifest(state, {
                "meterless/world-model": {"wired": ["WorldStoreService", "IngestPipelineService",
                                                    "ContextBuilderService"],
                                          "dormant": ["EnrichmentService", "RelationshipService",
                                                      "ControlPlaneService"]},
                "meterless/h-mem": {"wired": ["MemoryStoreService", "MemoryRetrievalService"] if self.memory else [],
                                    "dormant": ["dreaming", "sleep", "conflict resolution"]},
            })
        if self.memory:
            self.memory.mine(state)
        return state
