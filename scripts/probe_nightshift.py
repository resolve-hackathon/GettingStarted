"""Diagnose model calls through Meterless Nightshift.

Runs one question exactly as Resolve does, and prints every model request's size,
HTTP status, time and the start of Nightshift's answer (including its error detail).
When a call fails, it retries the same request with one change at a time to find
what NVIDIA rejects. Prints no tokens.

    python3 scripts/probe_nightshift.py c-003 "I can reproduce an upload crash after 3.8.2"
"""

import copy
import json
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from resolve.__main__ import meterless_engines  # noqa: E402
from resolve.actions import DryRunActions  # noqa: E402
from resolve.agent import Agent  # noqa: E402
from resolve.config import load_env, runtime_dir  # noqa: E402
from resolve.reasoning import CompatibleReasoner  # noqa: E402
from resolve.transport import ServiceError  # noqa: E402
from resolve.zetaris import make_researcher  # noqa: E402


def send(client, body):
    raw = json.dumps(body).encode()
    request = Request(client.base_url + "/chat/completions", data=raw, method="POST", headers={
        "Authorization": client.token, "Content-Type": "application/json", "Accept": "application/json",
        **client.extra_headers})
    started = time.time()
    try:
        with urlopen(request, timeout=client.timeout) as response:
            status, text = response.status, response.read().decode("utf-8", "replace")
    except HTTPError as error:
        status, text = error.code, error.read().decode("utf-8", "replace")
    except Exception as error:  # timeouts, resets
        status, text = None, f"{type(error).__name__}: {error}"
    return status, round(time.time() - started, 1), len(raw), text


def variants(body):
    """Ways to get a direct answer: thinking switches NVIDIA may honour, and other Nemotrons."""
    def changed(**fields):
        copy_ = copy.deepcopy(body)
        copy_.update(fields)
        return copy_
    yield "enable_thinking false", changed(chat_template_kwargs={"enable_thinking": False})
    yield "reasoning_effort low", changed(reasoning_effort="low")
    yield "detailed thinking off", changed(messages=[
        {"role": "system", "content": "detailed thinking off\n" + body["messages"][0]["content"].replace("/no_think\n", "", 1)},
        body["messages"][1]])
    yield "model bolt-e4b", changed(model="nvidia/nemotron-3.5-bolt-e4b")
    yield "model meterless/fast", changed(model="meterless/fast")
    yield "model ultra", changed(model="nvidia/nemotron-3-ultra-550b-a55b", max_tokens=8192)


def verdict(status, text):
    if status != 200:
        return "FAIL"
    try:
        body = json.loads(text)
        content = body["choices"][0]["message"].get("content") or ""
        model = body.get("model")
    except (ValueError, KeyError, IndexError, TypeError):
        return "200 but unreadable"
    start = content.find("{")
    try:
        json.JSONDecoder().raw_decode(content[start:]) if start >= 0 else None
        ok = start >= 0
    except ValueError:
        ok = False
    return f"OK json={ok} model={model} chars={len(content)}"


class ProbeClient:
    def __init__(self, real):
        self.real, self.step = real, 0

    def request(self, method, path, body):
        self.step += 1
        status, seconds, size, text = send(self.real, body)
        print(f"\n[{self.step}] as sent: HTTP {status} in {seconds}s, request {size} bytes")
        print("    " + text[:300].replace("\n", " "))
        if status == 200:
            return json.loads(text)
        print("    Trying alternatives (each up to the timeout):")
        for label, changed in variants(body):
            status2, seconds2, _, text2 = send(self.real, changed)
            print(f"    {label:24s} HTTP {status2} in {seconds2:5.1f}s  {verdict(status2, text2)}")
        sys.exit("\nDone: send this output.")


def main():
    load_env()
    customer, message = sys.argv[1], sys.argv[2]
    reasoner = CompatibleReasoner.from_nightshift()
    reasoner.client = ProbeClient(reasoner.client)
    database = runtime_dir() / "support.sqlite"
    agent = Agent(make_researcher(database, symptom_filter=False), reasoner, DryRunActions(),
                  *meterless_engines())
    state = agent.run(message, customer, "probe")
    print(f"\nRESULT: {state.action.value} verified={state.verified} gaps={state.gaps}")


if __name__ == "__main__":
    os.environ.setdefault("MODEL_RETRY_SECONDS", "1")
    main()
