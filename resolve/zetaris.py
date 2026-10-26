"""Zetaris researcher: the same research contract, answered by a federated query.

Each of the four sources lives in its own database and is registered in Zetaris
as its own datasource (see zetaris/register.sql). One SQL statement joins them
in place; nothing is copied into Zetaris.

REST call shape used by the hackathon's local Zetaris platform:

    POST {ZETARIS_REST_URL}/sql-editor/sqls/run
    headers: Authorization: Bearer <key>, X-Org-ID, X-Request-ID
    body:    {"queryId": uuid, "sql": "<one statement, no trailing ;>",
              "source": "SqlEditor", "limit": n}
    returns: {"headers": [...], "data": [[...], ...], "total": n, "timeUsed": ms}
"""

import json
import os
import re
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .config import ROOT
from .transport import ServiceError

_READ_ONLY = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)
_LITERAL = re.compile(r"'(?:[^']|'')*'")


def quote(value) -> str:
    """Render one bound value as a SQL string literal. Values are internal IDs."""
    text = str(value)
    if len(text) > 200 or any(ch in text for ch in "\x00\n\r\\"):
        raise ValueError("Refusing to bind an unexpected query value")
    return "'" + text.replace("'", "''") + "'"


def bind(sql: str, params) -> str:
    """Replace each ? outside string literals with a quoted value, in order."""
    params = list(params)
    parts, last, out = _LITERAL.split(sql), _LITERAL.findall(sql), []
    for index, chunk in enumerate(parts):
        while "?" in chunk:
            if not params:
                raise ValueError("Not enough query parameters")
            head, _, chunk = chunk.partition("?")
            out.append(head + quote(params.pop(0)))
        out.append(chunk)
        if index < len(last):
            out.append(last[index])
    if params:
        raise ValueError("Too many query parameters")
    return "".join(out)


class ZetarisClient:
    def __init__(self, base_url: str, api_key: str, org_id: str = "1", timeout: float = 30):
        parsed = urlparse(base_url)
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        ):
            raise ValueError("ZETARIS_REST_URL must use HTTPS (local platform may use http://localhost)")
        self.url = base_url.rstrip("/") + "/sql-editor/sqls/run"
        self.api_key, self.org_id, self.timeout = api_key, org_id, timeout

    @classmethod
    def from_env(cls) -> "ZetarisClient":
        key = os.getenv("ZETARIS_API_KEY", "")
        if not key:
            raise ValueError("RESOLVE_RESEARCH=zetaris requires ZETARIS_API_KEY")
        return cls(os.getenv("ZETARIS_REST_URL", "http://localhost:8888/api/v1.0"), key,
                   os.getenv("ZETARIS_ORG_ID", "1"), float(os.getenv("ZETARIS_TIMEOUT_SECONDS", "30")))

    def run(self, sql: str, limit: int = 500) -> dict:
        sql = "\n".join(line for line in sql.splitlines()
                        if not line.strip().startswith("--")).strip().rstrip(";").strip()
        code = _LITERAL.sub("''", sql)
        if not _READ_ONLY.match(sql) or ";" in code:
            raise ValueError("The agent sends one read-only SELECT/WITH statement per call")
        body = json.dumps({"queryId": str(uuid.uuid4()), "sql": sql,
                           "source": "SqlEditor", "limit": limit}).encode("utf-8")
        request = Request(self.url, data=body, method="POST", headers={
            "Authorization": f"Bearer {self.api_key}", "X-Org-ID": self.org_id,
            "X-Request-ID": str(uuid.uuid4()), "Content-Type": "application/json",
            "Accept": "application/json",
        })
        try:
            with urlopen(request, timeout=self.timeout) as response:
                envelope = json.loads(response.read(5_000_001))
        except HTTPError as error:
            raise ServiceError(f"Zetaris returned HTTP {error.code}") from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise ServiceError("Zetaris request failed or returned invalid JSON") from None
        if not isinstance(envelope, dict) or not isinstance(envelope.get("data"), list):
            raise ServiceError("Unexpected Zetaris response envelope")
        if len(envelope["data"]) >= limit:
            raise ServiceError("Zetaris result hit the row limit; refusing a possibly truncated answer")
        return envelope


class _Cursor:
    def __init__(self, client: ZetarisClient):
        self.client, self.rows = client, []

    def execute(self, sql: str, params=()):
        self.rows = [tuple(row) for row in self.client.run(bind(sql, params))["data"]]

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class _Connection:
    """Just enough of DB-API for SQLResearcher: cursor() and close()."""
    def __init__(self, client: ZetarisClient):
        self.client = client

    def cursor(self):
        return _Cursor(self.client)

    def close(self):
        pass


def zetaris_researcher(symptom_filter: bool = True):
    from .research import SQLResearcher

    client = ZetarisClient.from_env()
    query = (ROOT / "sql/research_zetaris.sql").read_text()
    identity = (ROOT / "sql/identity_zetaris.sql").read_text()
    return SQLResearcher(lambda: _Connection(client), query, identity, symptom_filter)


def make_researcher(database: Path, symptom_filter: bool = True):
    """RESOLVE_RESEARCH=zetaris uses the federated query; anything else, the local fallback."""
    from .research import SQLResearcher

    mode = os.getenv("RESOLVE_RESEARCH", "local")
    if mode == "zetaris":
        return zetaris_researcher(symptom_filter)
    if mode != "local":
        raise ValueError("RESOLVE_RESEARCH must be local or zetaris")
    return SQLResearcher.local(database, symptom_filter)
