# Integration handoff

## Zetaris: pending tenant wiring

`SQLResearcher.local()` is an honest local SQLite fallback. It copies authored
demo files into a local snapshot and must not be described as federation.

The integration seam is `SQLResearcher(connect, query, identity_query)`.
`connect` returns a connection with DB-API-style `cursor()`, `execute()`,
`fetchall()`/`fetchone()`, and `close()` methods. Provide a wrapper if your Zetaris
client uses a different shape. Register separate source systems in your tenant
and expose virtual views with these normalized columns:

| View | Columns used by the starter query |
| --- | --- |
| accounts | row_id, discord_id, client_version, last_working_version, payload |
| kb | row_id, component, payload |
| releases | row_id, component, payload |
| issues | row_id, component, state, affects_version, payload |

`payload` is a JSON string containing the source record, with fields matching the
local fixtures. Return `(source, row_id, payload)` rows. Preserve stable original
source IDs and source URLs. Use virtual views over the separate KB, account,
release, and GitHub sources; do not upload the combined fallback snapshot and
call it a federated join.

Adapt the view names and bound-parameter placeholders in `sql/research.sql` for
the connection your tenant actually provides. Parameter order is customer ID,
component, component, component. Also adapt `identity_query`; its only parameter
is Discord user ID. Never interpolate message text or let a model generate SQL.
The current SQL returns component-scoped candidate rows; Python then filters
stable version ranges, the last-working-to-current release window, and symptom
relevance. Move those filters into virtual views if useful after the basic join
works. The retry expands body search with symptom terms and records its prior
gaps in the log; it is not a second arbitrary model SQL query.

Once your tenant connection works, replace the `SQLResearcher.local(database)`
construction in `build_agent` and the Discord identity researcher with your
configured researcher. Keep the default local path for the clean-clone demo.
No undocumented Zetaris HTTP endpoint or unverified driver name is assumed.

Start with Zetaris's official [connection overview](https://kbase.zetaris.com/knowledge/connecting-to-zetaris)
and [API access guide](https://kbase.zetaris.com/knowledge/accessing-zetaris-using-api)
to obtain the correct tenant connection and authentication details.

## NVIDIA Nemotron: optional compatible transport implemented

The adapter calls `/chat/completions` at the configured base URL with bearer
authentication. Both hypothesis and semantic verification use the configured
model; triage and reply rendering remain local for this starter. Copy the exact
available model ID from your provider. Invalid JSON, unsupported actions, false
claims, rejected verification, and service errors cannot bypass the hard gate.
No provider credentials or live inference were available during development.

Official API reference: [NVIDIA chat completion interface](https://docs.api.nvidia.com/nim/reference/nvidia-llama-3_1-nemotron-ultra-253b-v1-infer).
This link documents the interface; it is not a requirement to use that model.

## Meterless: explicit state pattern only

`TaskState` is a serializable shared object. The role prompts live in
`resolve/reasoning.py`; `events` provide inspectable decisions and citations.
There are no Meterless SDK, prompt-library, memory, or service calls yet. Confirm
the event requirement, then map these seams to the actual service. Do not claim
sponsor usage from the local dataclass alone. H-MEM is deferred as a stretch goal.

## GitHub: live action handlers, snapshot research

`LiveActions` implements a comment on an existing issue and opening/re-reading a
new one. The offline issue records are fictional fixtures. Replace them with a
real demo repository's records before live `link_issue`; revalidate versions and
workarounds when importing. Keep the external API repository fixed in
configuration, separate from model output. Seed the component labels yourself.

Next, add a GitHub issue importer with pagination and explicit version metadata
(for example, component and affected-version labels) or register the GitHub API
source in Zetaris. Do not parse arbitrary issue prose into trusted executable
actions. Live POSTs have no automatic HTTP retry; a persistent action journal
blocks duplicate question IDs after success, failure, or process interruption.
It cannot guarantee exactly-once delivery across a remote timeout: reconcile
the receipts in `runtime/actions.sqlite` and the service before manual recovery.

Official references: [issue creation](https://docs.github.com/en/rest/issues/issues#create-an-issue)
and [issue comments](https://docs.github.com/en/rest/issues/comments#create-an-issue-comment).

## Discord: optional one-server bot

The bot is deliberately scoped to one server and support channel. With live
actions disabled it posts preview replies only. With live actions enabled,
fixed REST handlers send source-based replies and evidence packets, collecting
message IDs. Mentions are disabled in generated messages. GitHub writes happen
only in the configured demo repository.

Official references: [discord.py API](https://discordpy.readthedocs.io/en/latest/api.html)
and [Message Content intent](https://discordpy.readthedocs.io/en/stable/intents.html).

## Cursor and submission evidence

Open this checkout in Cursor, configure the actual Zetaris MCP server there,
and capture a working query and component while building. This starter cannot
retroactively claim it was authored in Cursor or that its MCP connection exists.
After tenant integration, rerun the four acceptance scenarios against the real
services, including visible receipts and the escalation channel, before recording.
The ten offline cases measure deterministic fixture behavior, not live Nemotron
accuracy, sponsor compliance, or production readiness.
