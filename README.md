# Resolve: evidence-first support agent starter

A runnable starting point for the approach in **Resolve: Customer Support
Resolution Agent**: investigate a support report across accounts, KB articles,
releases, and issues; verify the evidence; then post a fix, link an issue, open an
issue, or escalate. CloudNest is the fictional demo product.

The original `AI_CustomerSupport_Agent.py`, `CodeAI Agent`, and image guide are
preserved as early references. Use the `resolve/` package for this approach.

## Run it now

Python 3.11 or newer. No API keys, downloads, or third-party packages are needed
for the CLI, demo, evaluation, or tests. Run these from this repository's root:

```bash
git clone https://github.com/resolve-hackathon/GettingStarted.git
cd GettingStarted
python -m resolve demo --trace
python -m resolve eval
python -m unittest discover -s tests -v
```

The demo prints four complete JSON task states, one per outcome. Its `events`
show role decisions, source row IDs, verification gaps, and action status. It does
not print hidden model reasoning. `demo` and `eval` always use the deterministic
offline baseline and dry-run actions, even when live services are configured.

Try individual reports:

```bash
python -m resolve ask --customer-id c-031 "Desktop sync stopped after the update" --trace
python -m resolve ask --customer-id c-002 "Why is my export blank?"
python -m resolve ask --customer-id c-003 "I can reproduce an upload crash after 3.8.2"
python -m resolve ask --customer-id c-004 "My workspace turns purple at midnight"
```

Expected actions: `link_issue`, `post_fix`, `open_issue`, `escalate`.
Dry-run results have `status: dry_run`, contain the intended payload, and return
no invented remote message IDs or issue numbers. They do not contact Discord or
GitHub. The fixture GH-182 uses an intentionally non-working `example.invalid`
URL; it is not a claim that issue 182 exists in your repository.

## What is implemented

| Piece | Starter behavior |
| --- | --- |
| Triage | Deterministic customer/component routing; Discord identity comes from the sender mapping, never message text |
| Researcher | Parameterized SQL over a local fallback snapshot of four logical sources |
| Hypothesis | Offline rule baseline or optional Nemotron/OpenAI-compatible JSON call |
| Verifier | Checks exact claim values, source IDs, account version, remedy applicability, and action eligibility; compatible mode also runs an independent model audit |
| Resolver | Four fixed action handlers, dry run by default; live Discord/GitHub implementations verify returned receipts |
| Retry | At most one additional research pass using verification gaps and symptom text |
| State/log | One inspectable JSON object per question; Discord runs append JSONL locally |
| Duplicate protection | Live actions are journaled by question ID; uncertain/failed POSTs are not automatically replayed |
| Seed data | 50 synthetic accounts, 10 authored KB articles, 6 releases, 10 issue fixtures |
| Checks | Four end-to-end outcome tests, ten offline evaluation cases, and failure/verification tests |

All non-escalation actions must pass the verifier with confidence at least 0.6.
Replies use exact remedies from source rows. A new bug requires an explicit
regression record with matching symptom signals; a recent release alone is not
enough. Unknown customers and unsupported/version-mismatched questions escalate.
Offline confidence scores are illustrative, not measured probabilities.

## Configure optional services

Copy `.env.example` to `.env` (`cp .env.example .env` on macOS/Linux, or
`Copy-Item .env.example .env` in PowerShell). Environment variables override the
file. Do not commit credentials.

For model inference, set:

```dotenv
RESOLVE_MODEL_MODE=compatible
MODEL_BASE_URL=https://integrate.api.nvidia.com/v1
MODEL_API_KEY=your_key
MODEL_NAME=the_model_identifier_from_your_provider
```

Then run an `ask` command. It still uses dry-run actions. To use a different
OpenAI-compatible endpoint, change the base URL, key, and model together. There is
no silent provider switch: unavailable or malformed inference retries once and
then escalates. Local triage and response templates do not make model calls.

For Discord, install the optional dependency in a virtual environment:

```bash
python -m venv .venv
# Activate .venv using your shell's activation command.
python -m pip install -e ".[discord]"
python -m resolve seed
python -m resolve bot
```

Set `DISCORD_TOKEN`, `DISCORD_GUILD_ID`, and `DISCORD_SUPPORT_CHANNEL_ID`; enable
the Message Content intent in the Discord developer portal. The bot handles
`!resolve your question` or an @mention in that one support channel and its
threads. It ignores other channels, bots, webhooks, and DMs. Give it View Channel,
Send Messages, Read Message History, and Send Messages in Threads as needed.

For a demo user, replace that user's synthetic `discord_id` in
`data/accounts.json` with the test sender's ID and run `python -m resolve seed`.
Unmapped users are escalated instead of accessing another account. Dry-run bot
mode posts clearly marked previews in Discord but performs no GitHub actions or
human handoff.

Live mode additionally requires `GITHUB_TOKEN`, `GITHUB_REPOSITORY`,
`DISCORD_ESCALATION_CHANNEL_ID`, and `RESOLVE_ALLOW_LIVE_ACTIONS=true`:

```bash
python -m resolve bot --live
```

Use a demo repository with Issues enabled, the `component:upload` (and other
needed component) labels, and a token with repository Issues write access.
Replace issue fixtures with actual issue numbers, URLs, affected versions, and
workarounds before demonstrating `link_issue` live, then reseed. Fixture URLs are
blocked from live commenting. The handler re-reads the live issue before
commenting. New issues are re-read after creation. Failed or partial actions
report `failed` with the receipts obtained so far; they never claim a confirmed
handoff or fix delivery. The private escalation channel must be writable.

For CLI live actions use `ask --live --question-id a-stable-report-id ...`.
Use the same ID for the same report to avoid duplicate remote actions. After a
timeout, inspect both the action journal and remote service before retrying.

## Files to work on next

```text
resolve/state.py          Shared task state and action allowlist
resolve/agent.py          Five-role sequence and bounded retry
resolve/research.py       Seed importer, SQL fallback, federated connection seam
resolve/reasoning.py      Offline baseline, prompts, hard verifier, model adapter
resolve/actions.py        Four actions, receipts, persistent action journal
resolve/discord_bot.py    One-server Discord entry point
sql/research.sql          Parameterized four-source research query
data/                    Editable synthetic source material and evaluation cases
tests/test_acceptance.py  Outcomes, verification, and API failure checks
docs/INTEGRATIONS.md      Sponsor integration status and remaining work
```

`python -m resolve seed` rebuilds `runtime/support.sqlite` after editing source
files. It does not clear the separate live action journal. All paths assume an
editable checkout; run commands from the repository root. `runtime/` and `.env`
are ignored by Git.

## Boundaries and remaining work

This is a working starter, not a complete hackathon submission. Live sponsor
services were not exercised. Zetaris federation and Meterless SDK/service calls
still need your tenant details and the event's confirmed requirements. The
current explicit shared-state design is inspired by the plan's World Model
pattern; it does not claim to call Meterless. See
[integration notes](docs/INTEGRATIONS.md) for the exact handoff points.

The offline retriever uses simple keyword matching and stable three-part version
ranges. It does not prove semantic correctness for arbitrary real questions.
GitHub research currently uses an authored snapshot, not a continuous API sync;
live action handlers are separate from the research source. Source revalidation,
rate-limit handling, real identity provisioning, operational monitoring, and
live-model evaluation are next steps. Keep all demo data synthetic and avoid
using this starter for account changes, refunds, or destructive actions.
