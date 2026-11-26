"""Mirror Roommate ledger events into pgvector and retrieve older context."""

import logging
import os
import re
import unicodedata
from math import isfinite
from threading import Lock

import httpx
import psycopg
from psycopg.types.json import Jsonb
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from server import conversation_memory, database, solutions
from . import CONSTANT
from . import roommate_memory


SOURCE = conversation_memory.ConversationEvent
VIEW = conversation_memory.ConversationView
LOGGER = logging.getLogger("uvicorn.error")
SCHEMA_LOCK = Lock()
RECALL_LIMIT = 20
RERANK_LIMIT = 12
RESULT_LIMIT = 4
SCHEMA = """
CREATE TABLE IF NOT EXISTS roommate_ledger_vectors (
    event_key text PRIMARY KEY,
    owner_id bigint NOT NULL,
    event jsonb NOT NULL,
    contents text NOT NULL,
    tokens text[] NOT NULL,
    embedding vector(1024),
    embedding_model text
);
CREATE INDEX IF NOT EXISTS roommate_ledger_owner_idx ON roommate_ledger_vectors (owner_id);
CREATE INDEX IF NOT EXISTS roommate_ledger_tokens_idx ON roommate_ledger_vectors USING gin (tokens);
"""


def sparse_tokens(text: str) -> list[str]:
    words = re.findall(r"[\u3400-\u9fff]+|[a-z0-9_]+",
                       unicodedata.normalize("NFKC", text).casefold())
    tokens = []
    for word in words:
        if "\u3400" <= word[0] <= "\u9fff" and len(word) > 1:
            tokens.extend(word[index:index + 2] for index in range(len(word) - 1))
        else:
            tokens.append(word)
    return list(dict.fromkeys(tokens))


def rewrite_query(question: str, reference: dict | None) -> str:
    query = unicodedata.normalize("NFKC", question).strip()
    if reference is None or not roommate_memory.REFERENCE.search(query):
        return query
    details = [reference["requirements"], *roommate_memory.input_names(reference),
               *roommate_memory.output_names(reference)]
    return "\n".join([query, conversation_memory.excerpts(" ".join(details), 400)])


def event_contents(event: dict) -> str:
    payload = event["payload"]
    files = [item.get("name", "") if isinstance(item, dict) else item for item in payload.get("files", [])]
    return " ".join([conversation_memory.excerpts(payload["text"], 6000), *files]).strip()


def ledger_events(owner_id: int):
    conversation_memory.ensure_tables()
    with database.get_session() as session:
        return session.execute(
            select(SOURCE.event_key, SOURCE.event, VIEW.archived)
            .outerjoin(VIEW, (VIEW.conversation_id == SOURCE.conversation_id) & (VIEW.owner_id == SOURCE.owner_id))
            .where(SOURCE.owner_id == owner_id, SOURCE.event["kind"].as_string() == "roommate")
            .order_by(SOURCE.id)
        ).all()


def embedding_model() -> str:
    return os.environ.get("KB_EMBEDDING_MODEL", "bge-m3")


def embed(texts: list[str], model: str) -> list[str]:
    url = os.environ.get("KB_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    response = httpx.post(f"{url}/api/embed", json={"model": model, "input": texts},
                          timeout=90 if len(texts) > 1 else 10, trust_env=False)
    response.raise_for_status()
    vectors = response.json()["embeddings"]
    if len(vectors) != len(texts) or any(len(vector) != 1024 or not all(map(isfinite, vector)) for vector in vectors):
        raise ValueError(CONSTANT.ROOMMATE_MEMORY_VECTOR_INVALID)
    return ["[" + ",".join(str(float(value)) for value in vector) + "]" for vector in vectors]


def ensure_schema(connection) -> None:
    with SCHEMA_LOCK:
        exists = connection.execute("SELECT to_regclass('roommate_ledger_vectors')").fetchone()[0]
        if not exists:
            connection.execute("SELECT pg_advisory_xact_lock(481194294)")
            connection.execute(SCHEMA)
            connection.commit()


def sync_events(connection, owner_id: int, events: list, model: str, *, embedding_limit: int | None = 8) -> int:
    known = dict(connection.execute(
        "SELECT event_key, embedding_model FROM roommate_ledger_vectors WHERE owner_id = %s", (owner_id,)
    ).fetchall())
    additions = [(key, owner_id, Jsonb(event), contents, sparse_tokens(contents))
                 for key, event, _ in events if key not in known for contents in [event_contents(event)]]
    with connection.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO roommate_ledger_vectors (event_key, owner_id, event, contents, tokens) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (event_key) DO NOTHING", additions,
        )
    connection.commit()
    pending = [(key, event_contents(event)) for key, event, _ in reversed(events) if known.get(key) != model]
    pending = [(key, text) for key, text in pending if text][:embedding_limit]
    for offset in range(0, len(pending), 8):
        batch = pending[offset:offset + 8]
        vectors = embed([text for _, text in batch], model)
        with connection.cursor() as cursor:
            cursor.executemany(
                "UPDATE roommate_ledger_vectors SET embedding = %s::vector, embedding_model = %s "
                "WHERE owner_id = %s AND event_key = %s",
                [(vector, model, owner_id, key) for (key, _), vector in zip(batch, vectors, strict=True)],
            )
        connection.commit()
    return len(pending)


def fused_candidates(connection, owner_id: int, keys: list[str], query: str, model: str) -> list[tuple]:
    tokens = sparse_tokens(query)
    lexical = connection.execute(
        "SELECT event_key, event, contents FROM roommate_ledger_vectors "
        "WHERE owner_id = %s AND event_key = ANY(%s) AND tokens && %s::text[] "
        "ORDER BY (SELECT count(*) FROM unnest(%s::text[]) AS query(token) "
        "WHERE query.token = ANY(tokens)) DESC, event_key LIMIT %s",
        (owner_id, keys, tokens, tokens, RECALL_LIMIT),
    ).fetchall()
    try:
        vector = embed([query], model)[0]
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        LOGGER.warning(CONSTANT.ROOMMATE_MEMORY_EMBED_FAILED, type(exc).__name__)
        semantic = []
    else:
        semantic = connection.execute(
            "SELECT event_key, event, contents FROM roommate_ledger_vectors "
            "WHERE owner_id = %s AND event_key = ANY(%s) AND embedding_model = %s AND embedding IS NOT NULL "
            "ORDER BY embedding <=> %s::vector, event_key LIMIT %s",
            (owner_id, keys, model, vector, RECALL_LIMIT),
        ).fetchall()
    candidates, scores = {}, {}
    for rows in (lexical, semantic):
        for rank, row in enumerate(rows, 1):
            key = row[0]
            candidates[key] = row
            scores[key] = scores.get(key, 0) + 1 / (60 + rank)
    return [candidates[key] for key in sorted(scores, key=lambda key: (-scores[key], key))[:RERANK_LIMIT]]


def cross_rank(query: str, candidates: list[tuple]) -> list[tuple]:
    url = os.environ.get("ROOMMATE_RERANK_URL", "http://127.0.0.1:9931/rerank")
    model = os.environ.get("ROOMMATE_RERANK_MODEL", "bge-reranker-v2-m3")
    response = httpx.post(url, json={"model": model, "query": query,
                                    "documents": [row[2][:1800] for row in candidates],
                                    "top_n": min(RESULT_LIMIT, len(candidates))}, timeout=12, trust_env=False)
    response.raise_for_status()
    indices = [item["index"] for item in response.json()["results"]]
    if not indices or len(indices) != len(set(indices)) or any(
        type(index) is not int or not 0 <= index < len(candidates) for index in indices
    ):
        raise ValueError(CONSTANT.ROOMMATE_MEMORY_RERANK_INVALID)
    return [candidates[index] for index in indices[:RESULT_LIMIT]]


def backfill() -> tuple[int, int]:
    dsn = solutions.knowledge_database_url()
    if not dsn:
        raise RuntimeError(CONSTANT.ROOMMATE_MEMORY_DB_REQUIRED)
    conversation_memory.ensure_tables()
    with database.get_session() as session:
        owners = session.scalars(select(SOURCE.owner_id)
                                 .where(SOURCE.event["kind"].as_string() == "roommate").distinct()).all()
    count = 0
    for owner_id in owners:
        events = ledger_events(owner_id)
        with psycopg.connect(dsn, connect_timeout=2) as connection:
            ensure_schema(connection)
            sync_events(connection, owner_id, events, embedding_model(), embedding_limit=None)
        count += len(events)
    return len(owners), count


def mirror_owner(owner_id: int) -> None:
    dsn = solutions.knowledge_database_url()
    if not dsn:
        return
    try:
        events = ledger_events(owner_id)
        with psycopg.connect(dsn, connect_timeout=2) as connection:
            ensure_schema(connection)
            sync_events(connection, owner_id, events, embedding_model())
    except (psycopg.Error, SQLAlchemyError, OSError, httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        LOGGER.warning(CONSTANT.ROOMMATE_MEMORY_STORE_FAILED, type(exc).__name__)


def retrieve(owner_id: int, conversation_id: str, excluded: set[str], question: str,
             reference: dict | None) -> list[dict]:
    dsn = solutions.knowledge_database_url()
    if not dsn or not question.strip():
        return []
    try:
        events = ledger_events(owner_id)
        if not events:
            return []
        latest = {(event["conversation_id"], event["message_id"], event["role"]): key
                  for key, event, archived in events if archived is False
                  and (event["conversation_id"] != conversation_id or event["message_id"] not in excluded)}
        model = embedding_model()
        with psycopg.connect(dsn, connect_timeout=2) as connection:
            ensure_schema(connection)
            try:
                sync_events(connection, owner_id, events, model)
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                LOGGER.warning(CONSTANT.ROOMMATE_MEMORY_EMBED_FAILED, type(exc).__name__)
            if not latest:
                return []
            query = rewrite_query(question, reference)
            candidates = fused_candidates(connection, owner_id, list(latest.values()), query, model)
        if candidates:
            try:
                candidates = cross_rank(query, candidates)
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                LOGGER.warning(CONSTANT.ROOMMATE_MEMORY_RERANK_FAILED, type(exc).__name__)
        return [{"conversation_id": event["conversation_id"], "message_id": event["message_id"],
                 "role": event["role"], "text": conversation_memory.excerpts(contents, 480)}
                for _, event, contents in candidates[:RESULT_LIMIT]]
    except (psycopg.Error, SQLAlchemyError, OSError) as exc:
        LOGGER.warning(CONSTANT.ROOMMATE_MEMORY_STORE_FAILED, type(exc).__name__)
        return []


if __name__ == "__main__":
    owners, records = backfill()
    print(CONSTANT.ROOMMATE_MEMORY_BACKFILLED.format(owners=owners, records=records))
