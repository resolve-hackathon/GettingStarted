"""Five sequential roles with a strict maximum of one research retry."""

from .reasoning import triage, verify_structure
from .state import Action, Proposal, TaskState
from .transport import ServiceError


class Agent:
    def __init__(self, researcher, reasoner, actions):
        self.researcher, self.reasoner, self.actions = researcher, reasoner, actions

    def run(self, message: str, customer_id: str, question_id: str) -> TaskState:
        if not message.strip() or len(message) > 4000:
            raise ValueError("Message must contain 1 to 4000 characters")
        state = TaskState(question_id, message, customer_id)
        triage(state)
        state.record_event("triage", customer_id=customer_id, component=state.component)
        for attempt in range(2):
            state.retries, state.verified = attempt, False
            state.hypothesis = Proposal()
            state.evidence = []
            try:
                state.evidence = self.researcher.research(state)
                state.record_event("researcher", evidence_ids=[e.id for e in state.evidence],
                                   targeted_gaps=list(state.gaps))
                state.hypothesis = self.reasoner.propose(state)
                state.record_event("hypothesis", action=state.hypothesis.action,
                                   confidence=state.hypothesis.confidence,
                                   supporting_ids=state.hypothesis.supporting_ids)
                gaps = verify_structure(state)
                if not gaps:
                    gaps = self.reasoner.verify(state)
                state.gaps, state.verified = gaps, not gaps
            except (ServiceError, ValueError, OSError) as error:
                state.gaps = [f"Investigation unavailable ({type(error).__name__}: {str(error)[:160]})"]
            state.record_event("verifier", verified=state.verified, gaps=list(state.gaps))
            if state.verified:
                break
        state.action = state.hypothesis.action if state.verified else Action.ESCALATE
        try:
            state.action_result, state.reply_text = self.actions.execute(state)
        except (ServiceError, ValueError, OSError) as error:
            # Do not claim an issue, comment, or escalation exists if its API failed.
            state.action_result = {"status": "failed", "error": type(error).__name__,
                                   "receipts": getattr(error, "receipts", []),
                                   "detail": "Action was not confirmed; inspect service logs before retrying"}
            state.reply_text = "I could not confirm delivery. Please contact a support team member directly."
        state.record_event("resolver", action=state.action,
                           status=state.action_result.get("status", "unknown"))
        return state
