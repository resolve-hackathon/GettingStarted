"""Optional Discord transport. No Slack dependency or network call on import."""

import asyncio
import json
import os
import re
from pathlib import Path

from .actions import DryRunActions, JournalActions, LiveActions
from .config import runtime_dir
from .research import SQLResearcher


def run_bot(database: Path, *, live: bool = False) -> None:
    try:
        import discord
    except ImportError:
        raise ValueError('Install the optional bot dependency: python -m pip install -e ".[discord]"') from None
    from .__main__ import build_agent

    required = ["DISCORD_TOKEN", "DISCORD_GUILD_ID", "DISCORD_SUPPORT_CHANNEL_ID"]
    if any(not os.getenv(key) for key in required):
        raise ValueError("Bot requires DISCORD_TOKEN, DISCORD_GUILD_ID, DISCORD_SUPPORT_CHANNEL_ID")
    guild_id, support_id = os.environ["DISCORD_GUILD_ID"], os.environ["DISCORD_SUPPORT_CHANNEL_ID"]
    if not guild_id.isdigit() or not support_id.isdigit():
        raise ValueError("Guild and support channel IDs must be numeric")
    if live:
        LiveActions.from_env(support_id)  # validate all settings before connecting
    else:
        build_agent(database, DryRunActions())  # validate model config before connecting
    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents, allowed_mentions=discord.AllowedMentions.none())
    semaphore = asyncio.Semaphore(1)
    researcher = SQLResearcher.local(database)

    @client.event
    async def on_ready():
        print(f"Resolve connected as {client.user}; live actions={live}")

    @client.event
    async def on_message(message):
        if message.author.bot or message.webhook_id or not message.guild:
            return
        parent = getattr(message.channel, "parent_id", None)
        if str(message.guild.id) != guild_id or support_id not in {str(message.channel.id), str(parent)}:
            return
        mentioned = client.user is not None and client.user in message.mentions
        if not mentioned and not message.content.startswith("!resolve "):
            return
        text = message.content.removeprefix("!resolve ")
        if client.user:
            text = re.sub(rf"<@!?{client.user.id}>", "", text).strip()
        if not text or len(text) > 4000:
            await message.reply("Please send a text support question with 1 to 4000 characters.", mention_author=False)
            return
        async with semaphore:
            customer = await asyncio.to_thread(researcher.customer_for_discord, str(message.author.id))
            # Never accept a customer ID supplied in the chat message as identity.
            customer = customer or "unknown-discord-user"
            actions = DryRunActions()
            if live:
                actions = JournalActions(LiveActions.from_env(str(message.channel.id), str(message.id)),
                                         runtime_dir() / "actions.sqlite")
            agent = build_agent(database, actions)
            async with message.channel.typing():
                state = await asyncio.to_thread(agent.run, text, customer, f"discord-{message.id}")
            if not live or state.action_result.get("status") == "failed":
                for offset in range(0, len(state.reply_text), 1800):
                    receipt = await message.reply(state.reply_text[offset:offset + 1800], mention_author=False)
                    state.action_result.setdefault("bot_reply_ids", []).append(str(receipt.id))
            log = runtime_dir() / "investigations.jsonl"
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(state.snapshot(), ensure_ascii=False) + "\n")

    client.run(os.environ["DISCORD_TOKEN"])
