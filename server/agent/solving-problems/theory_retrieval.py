"""检索数字信号处理理论条目，并依据教材证据重新排序。"""

import os
import re

import httpx
import psycopg

from . import CONSTANT


CONCEPTS = ("单位冲激", "单位脉冲", "单位阶跃", "线性卷积", "循环卷积", "卷积", "采样定理", "混叠",
            "直接型", "级联型", "信号流图", "网络结构", "差分方程",
            "离散时间傅里叶变换", "傅里叶变换", "DFT", "FFT", "Z变换", "频率响应", "系统函数", "差分方程",
            "冲激响应", "极零点", "收敛域", "滤波器", "线性相位", "窗函数", "周期序列", "时不变", "因果", "稳定")
RECALL_K = 40
RESULT_K = 6
ROW_COLUMNS = ("id", "kind", "concept", "formula", "text", "page", "title", "edition",
               "needs_review", "figure_path", "extraction_method")
ALIASES = {"单位冲激": "单位脉冲", "单位脉冲": "单位冲激", "单位阶跃": "阶跃", "线性卷积": "卷积"}


def requirement_terms(question: str) -> list[str]:
    lowered = question.lower()
    found = [term for term in CONCEPTS if term.lower() in lowered]
    if "δ" in question or "\\delta" in question:
        found.append("单位冲激")
    if "u(n)" in lowered or "u（n）" in lowered:
        found.append("单位阶跃")
    if not found:
        found = re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z]{3,10}", question)[:6]
    primary = [term for term in dict.fromkeys(found) if not any(
        term != other and term in other for other in found)][:8]
    return list(dict.fromkeys([*primary, *(ALIASES[term] for term in primary if term in ALIASES)]))[:8]


def _as_passage(row) -> dict:
    return dict(zip(ROW_COLUMNS, row))


def rerank_theory(lexical: list, semantic: list, terms: list[str], limit: int = RESULT_K) -> list[dict]:
    candidates = {}
    for rows in (lexical, semantic):
        for rank, row in enumerate(rows, 1):
            passage = _as_passage(row)
            entry = candidates.setdefault(passage["id"], {"passage": passage, "score": 0.0})
            entry["score"] += 1 / (20 + rank)
    for entry in candidates.values():
        passage = entry["passage"]
        title = passage["concept"].lower()
        evidence = (passage["text"] + " " + passage["formula"]).lower()
        entry["score"] += sum(0.07 for term in terms if term.lower() in title)
        entry["score"] += sum(0.012 for term in terms if term.lower() in evidence)
        if passage["kind"] in {"formula", "theorem", "definition"}:
            entry["score"] += 0.05
        elif passage["kind"] == "figure":
            entry["score"] -= 0.025
        if re.search(r"习题|\d{1,2}\*?[.．、，。](?=\S)", passage["text"][:130]):
            entry["score"] -= 0.08
        if passage["extraction_method"] == "vision":
            entry["score"] += 0.1
        if not passage["needs_review"]:
            entry["score"] += 0.015
    ranked = sorted(candidates.values(), key=lambda item: (-item["score"], item["passage"]["page"]))
    reviewed = [item["passage"] for item in ranked if not item["passage"]["needs_review"]]
    selected = []
    concept_counts = {}
    for entry in ranked:
        passage = entry["passage"]
        concept = passage["concept"]
        if passage["needs_review"] and any(
            checked["page"] == passage["page"] and checked["kind"] == passage["kind"]
            and (concept in checked["concept"] or checked["concept"] in concept)
            for checked in reviewed
        ):
            continue
        if concept_counts.get(concept, 0) >= 2:
            continue
        selected.append(passage)
        concept_counts[concept] = concept_counts.get(concept, 0) + 1
        if len(selected) == limit:
            break
    return selected


def theory_passages(query: str, subject: str, database_url: str, model: str) -> list[dict] | None:
    """结构化理论索引尚未建立时返回 None。"""
    terms = requirement_terms(query)
    search_query = subject + " " + " ".join(terms) if terms else query[:250]
    embedding_error = None
    vector = None
    try:
        response = httpx.post(f"{os.environ.get('KB_OLLAMA_URL', 'http://127.0.0.1:11434').rstrip('/')}/api/embed",
                              json={"model": model, "input": search_query}, timeout=120, trust_env=False)
        response.raise_for_status()
        vectors = response.json().get("embeddings", [])
        if len(vectors) != 1 or len(vectors[0]) != 1024:
            raise ValueError(CONSTANT.TEXTBOOK_EMBEDDING_DIMENSIONS_INVALID)
        vector = "[" + ",".join(str(float(value)) for value in vectors[0]) + "]"
    except (httpx.HTTPError, ValueError) as exc:
        embedding_error = exc
    columns = ("SELECT e.id, e.kind, e.title, e.formula, e.evidence, e.page_number, "
               "d.title, d.edition, e.needs_review, e.figure_path, e.extraction_method "
               "FROM kb_theory_entries e JOIN kb_documents d ON d.id = e.document_id ")
    with psycopg.connect(database_url) as connection:
        indexed = connection.execute(
            "SELECT EXISTS (SELECT 1 FROM kb_theory_pages p JOIN kb_documents d ON d.id = p.document_id "
            "WHERE d.subject = %s)", (subject,),
        ).fetchone()[0]
        if not indexed:
            return None
        patterns = [f"%{term}%" for term in terms] or [f"%{query[:100]}%"]
        lexical = connection.execute(
            columns + "WHERE d.subject = %s AND (e.title ILIKE ANY(%s) OR e.evidence ILIKE ANY(%s)) "
            "ORDER BY CASE WHEN e.title ILIKE ANY(%s) THEN 0 ELSE 1 END, "
            "CASE WHEN e.kind IN ('formula','theorem','definition') THEN 0 ELSE 1 END, "
            "CASE WHEN e.extraction_method = 'vision' THEN 0 ELSE 1 END, e.page_number LIMIT %s",
            (subject, patterns, patterns, patterns, RECALL_K),
        ).fetchall()
        semantic = connection.execute(
            columns + "WHERE d.subject = %s AND e.embedding IS NOT NULL AND e.embedding_model = %s "
            "ORDER BY e.embedding <=> %s::vector LIMIT %s",
            (subject, model, vector, RECALL_K),
        ).fetchall() if vector else []
    if embedding_error and not lexical:
        raise RuntimeError(CONSTANT.THEORY_SEARCH_UNAVAILABLE) from embedding_error
    return rerank_theory(lexical, semantic, terms)
