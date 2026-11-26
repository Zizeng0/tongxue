"""Shared Crossref search for the thesis workflow and the MCP transport."""

import re
from dataclasses import dataclass
from html import unescape
from urllib.parse import quote

import httpx

DOI = re.compile(r"10\.\d{4,9}/[^\s<>]+", re.I)
TAG = re.compile(r"<[^>]*>")


def clean(value: str, limit: int) -> str:
    return " ".join(unescape(TAG.sub(" ", value)).split())[:limit]


@dataclass(frozen=True)
class Publication:
    doi: str
    title: str
    authors: str
    year: int | None
    journal: str
    abstract: str

    @property
    def url(self) -> str:
        return f"https://doi.org/{quote(self.doi, safe='/')}"

    @property
    def reference(self) -> str:
        return "。".join(filter(None, (self.authors, self.title, self.journal, str(self.year or ""), self.url)))


def parse_publication(item: dict) -> Publication | None:
    doi = item.get("DOI", "").lower()
    title = clean(next(iter(item.get("title") or []), ""), 330)
    if not title or not DOI.fullmatch(doi):
        return None
    authors = [author.get("name") or " ".join(filter(None, (author.get("given"), author.get("family"))))
               for author in (item.get("author") or [])[:8]]
    journal = clean(next(iter(item.get("container-title") or []), ""), 160)
    date_parts = (item.get("issued") or {}).get("date-parts") or [[]]
    year = next(iter(date_parts[0]), None)
    return Publication(doi, title, clean(", ".join(filter(None, authors)), 350), year, journal,
                       clean(item.get("abstract") or "", 2000))


def search_literature(queries: list[str], limit: int) -> tuple[list[Publication], int]:
    found = {}
    failed = 0
    with httpx.Client(timeout=6, headers={"User-Agent": "TongxueThesis/1.0"}) as client:
        for query in queries:
            try:
                response = client.get("https://api.crossref.org/works", params={
                    "query.bibliographic": query, "rows": 5,
                    "select": "DOI,title,author,container-title,issued,abstract",
                })
                response.raise_for_status()
                for item in response.json()["message"]["items"]:
                    if publication := parse_publication(item):
                        found.setdefault(publication.doi, publication)
            except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
                failed += 1
    ranked = sorted(found.values(), key=lambda item: bool(item.abstract), reverse=True)
    return ranked[:limit], failed
