"""Run with python -m resolve from the repository root."""

import argparse
import json
import os
import tempfile
import uuid
from pathlib import Path

from .actions import DryRunActions, JournalActions, LiveActions
from .agent import Agent
from .config import data_dir, load_env, runtime_dir
from .reasoning import CompatibleReasoner, RulesReasoner
from .research import SQLResearcher, seed_snapshot


def build_agent(database: Path, actions) -> Agent:
    mode = os.getenv("RESOLVE_MODEL_MODE", "rules")
    if mode not in {"rules", "compatible"}:
        raise ValueError("RESOLVE_MODEL_MODE must be rules or compatible")
    reasoner = RulesReasoner() if mode == "rules" else CompatibleReasoner.from_env()
    return Agent(SQLResearcher.local(database), reasoner, actions)


def print_state(state, trace: bool) -> None:
    if trace:
        for event in state.events:
            print(json.dumps(event))
    print(json.dumps(state.snapshot(), ensure_ascii=False, indent=2))


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="Resolve evidence-first support starter")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("seed", help="Rebuild the local fallback snapshot")
    demo = commands.add_parser("demo", help="Run all four outcomes without credentials")
    demo.add_argument("--trace", action="store_true")
    commands.add_parser("eval", help="Evaluate the 10 seeded offline questions")
    ask = commands.add_parser("ask", help="Investigate a single support report")
    ask.add_argument("message")
    ask.add_argument("--customer-id", required=True)
    ask.add_argument("--question-id", default=None)
    ask.add_argument("--trace", action="store_true")
    ask.add_argument("--live", action="store_true")
    bot = commands.add_parser("bot", help="Listen in one configured Discord support channel")
    bot.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if args.command in {"demo", "eval"}:
        # Acceptance demos always use isolated local data and dry-run actions,
        # regardless of the user's live/model environment configuration.
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "demo.sqlite"
            seed_snapshot(data_dir(), database)
            agent = Agent(SQLResearcher.local(database), RulesReasoner(), DryRunActions())
            cases = json.loads((data_dir() / "eval_cases.json").read_text())
            if args.command == "demo":
                for index in (0, 2, 4, 5):
                    case = cases[index]
                    print_state(agent.run(case["message"], case["customer_id"], case["id"]), args.trace)
                return 0
            passed = 0
            for case in cases:
                state = agent.run(case["message"], case["customer_id"], case["id"])
                ok = state.action.value == case["expected_action"] and state.action_result["status"] == "dry_run"
                passed += ok
                print(f"{'PASS' if ok else 'FAIL'} {case['id']}: {state.action.value}")
            print(f"{passed}/{len(cases)} passed (offline baseline, not live-model accuracy)")
            return 0 if passed == len(cases) else 1
    database = runtime_dir() / "support.sqlite"
    if args.command == "seed" or not database.exists():
        seed_snapshot(data_dir(), database)
    if args.command == "seed":
        print(f"Local fallback snapshot ready: {database}")
        return 0
    if args.command == "bot":
        from .discord_bot import run_bot
        run_bot(database, live=args.live)
        return 0
    actions = DryRunActions()
    if args.live:
        channel = os.getenv("DISCORD_SUPPORT_CHANNEL_ID", "")
        actions = JournalActions(LiveActions.from_env(channel), runtime_dir() / "actions.sqlite")
    agent = build_agent(database, actions)
    state = agent.run(args.message, args.customer_id, args.question_id or f"cli-{uuid.uuid4().hex}")
    print_state(state, args.trace)
    return 1 if state.action_result.get("status") == "failed" else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as error:
        raise SystemExit(f"Configuration/input error: {error}") from None
