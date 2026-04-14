"""
Reference search backends for the PAT pipeline.

Agents that need to discover or verify literature (`MissingReferencesAgent`,
`ReferenceQualityAgent`, `PaperPositioningAgent`) call out through a common
`ReferenceSearchBackend` interface.  Two concrete backends are supported,
both free and keyless:

* :class:`PubMedBackend` - NCBI E-utilities (search + summary + abstract).
* :class:`BioRxivBackend` - bioRxiv/medRxiv details endpoint with a
  keyword re-rank over the rolling window.

:class:`CombinedBackend` runs both in parallel and deduplicates by title.
:class:`NullBackend` is a no-op used when the CLI sets ``--ref-backend none``.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from urllib.parse import quote
from urllib.request import Request, urlopen

from .constants import (
    ABSTRACT_PREVIEW_CHARS,
    AUTHORS_PREVIEW_CHARS,
    BIORXIV_SEARCH_WINDOW_DAYS,
    BIORXIV_TIMEOUT_SECONDS,
    PUBMED_FETCH_TIMEOUT_SECONDS,
    PUBMED_TIMEOUT_SECONDS,
    REPORT_TITLE_TRUNCATE_CHARS,
)


class ReferenceSearchBackend:
    """Abstract interface for pluggable literature search."""

    def search(self, query: str, n: int = 3) -> list[dict]:
        """Return up to ``n`` hits as dicts.

        Each hit dict has the keys: ``title``, ``authors``, ``year``,
        ``venue``, ``url``, ``abstract``.  Backends that fail gracefully
        return a single pseudo-hit whose title begins with ``"["``; callers
        filter those out.
        """
        raise NotImplementedError


class PubMedBackend(ReferenceSearchBackend):
    """PubMed via NCBI E-utilities (free, no API key required)."""

    BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

    def search(self, query: str, n: int = 3) -> list[dict]:
        try:
            search_url = (
                f"{self.BASE}/esearch.fcgi?db=pubmed&term={quote(query)}"
                f"&retmax={n}&retmode=json&sort=relevance"
            )
            with urlopen(search_url, timeout=PUBMED_TIMEOUT_SECONDS) as resp:
                data = json.loads(resp.read())
            ids = data.get("esearchresult", {}).get("idlist", [])
            if not ids:
                return []

            id_str = ",".join(ids)
            fetch_url = (
                f"{self.BASE}/esummary.fcgi?db=pubmed&id={id_str}&retmode=json"
            )
            with urlopen(fetch_url, timeout=PUBMED_TIMEOUT_SECONDS) as resp:
                summary_data = json.loads(resp.read())

            abstracts = self._fetch_abstracts(ids)

            results: list[dict] = []
            for uid in ids:
                art = summary_data.get("result", {}).get(uid, {})
                if not art or "error" in art:
                    continue
                authors = ", ".join(
                    a.get("name", "") for a in art.get("authors", [])[:3]
                )
                results.append({
                    "title": art.get("title", ""),
                    "authors": authors,
                    "year": art.get("pubdate", "")[:4],
                    "venue": art.get("fulljournalname", art.get("source", "")),
                    "url": f"https://pubmed.ncbi.nlm.nih.gov/{uid}/",
                    "abstract": abstracts.get(uid, "")[:ABSTRACT_PREVIEW_CHARS],
                })
            return results
        except Exception as e:
            # Broad catch: network + XML/JSON + HTTP layers all fail differently.
            return [{
                "title": f"[PubMed search failed: {e}]",
                "authors": "", "year": "", "venue": "", "url": "", "abstract": "",
            }]

    def _fetch_abstracts(self, ids: list[str]) -> dict[str, str]:
        """Fetch abstracts through the efetch XML endpoint."""
        abstracts: dict[str, str] = {}
        try:
            id_str = ",".join(ids)
            url = (
                f"{self.BASE}/efetch.fcgi?db=pubmed&id={id_str}"
                f"&rettype=abstract&retmode=xml"
            )
            with urlopen(url, timeout=PUBMED_FETCH_TIMEOUT_SECONDS) as resp:
                root = ET.fromstring(resp.read())
            for article in root.findall(".//PubmedArticle"):
                pmid_el = article.find(".//PMID")
                abstract_el = article.find(".//AbstractText")
                if pmid_el is not None and abstract_el is not None:
                    abstracts[pmid_el.text] = abstract_el.text or ""
        except Exception:
            # Abstracts are best-effort; missing ones are fine.
            pass
        return abstracts


class BioRxivBackend(ReferenceSearchBackend):
    """bioRxiv / medRxiv via the free REST API (no key required).

    The bioRxiv details endpoint does not expose keyword search directly,
    so we pull the rolling window and score hits by simple keyword overlap
    in the title + abstract.
    """

    BASE = "https://api.biorxiv.org"

    def search(self, query: str, n: int = 3) -> list[dict]:
        try:
            end = datetime.now().strftime("%Y-%m-%d")
            start = (
                datetime.now() - timedelta(days=BIORXIV_SEARCH_WINDOW_DAYS)
            ).strftime("%Y-%m-%d")
            search_url = f"{self.BASE}/details/biorxiv/{start}/{end}/0/json"

            req = Request(search_url, headers={"User-Agent": "pat/1.0"})
            with urlopen(req, timeout=BIORXIV_TIMEOUT_SECONDS) as resp:
                data = json.loads(resp.read())

            query_words = query.lower().split()
            scored: list[tuple[int, dict]] = []
            for article in data.get("collection", []):
                title = article.get("title", "").lower()
                abstract = article.get("abstract", "").lower()
                haystack = f"{title} {abstract}"
                score = sum(1 for w in query_words if w in haystack)
                if score >= max(1, len(query_words) // 2):
                    scored.append((score, article))
            scored.sort(key=lambda x: -x[0])

            results: list[dict] = []
            for _, art in scored[:n]:
                authors = art.get("authors", "")
                if len(authors) > AUTHORS_PREVIEW_CHARS:
                    authors = authors[:AUTHORS_PREVIEW_CHARS] + "..."
                venue = "bioRxiv" if "biorxiv" in art.get("server", "") else "medRxiv"
                results.append({
                    "title": art.get("title", ""),
                    "authors": authors,
                    "year": art.get("date", "")[:4],
                    "venue": venue,
                    "url": f"https://doi.org/{art.get('doi', '')}",
                    "abstract": art.get("abstract", "")[:ABSTRACT_PREVIEW_CHARS],
                })
            return results
        except Exception as e:
            return [{
                "title": f"[bioRxiv search failed: {e}]",
                "authors": "", "year": "", "venue": "", "url": "", "abstract": "",
            }]


class CombinedBackend(ReferenceSearchBackend):
    """Queries PubMed and bioRxiv in parallel, then deduplicates by title."""

    def __init__(self) -> None:
        self._pubmed = PubMedBackend()
        self._biorxiv = BioRxivBackend()

    def search(self, query: str, n: int = 3) -> list[dict]:
        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_pm = pool.submit(self._pubmed.search, query, n)
            fut_bx = pool.submit(self._biorxiv.search, query, max(1, n // 2))
            pm = fut_pm.result()
            bx = fut_bx.result()

        seen: set[str] = set()
        merged: list[dict] = []
        for hit in pm + bx:
            key = hit.get("title", "").lower().strip()[:REPORT_TITLE_TRUNCATE_CHARS]
            if key and key not in seen and not hit["title"].startswith("["):
                seen.add(key)
                merged.append(hit)
        return merged[:n]


class NullBackend(ReferenceSearchBackend):
    """No-op backend used when ``--ref-backend none`` is requested."""

    def search(self, query: str, n: int = 3) -> list[dict]:
        return []


def create_ref_backend(name: str) -> ReferenceSearchBackend:
    """Factory: map a CLI backend name onto a concrete backend instance."""
    if name == "pubmed":
        return PubMedBackend()
    if name == "biorxiv":
        return BioRxivBackend()
    if name == "pubmed+biorxiv":
        return CombinedBackend()
    if name == "none":
        return NullBackend()
    # Default is PubMed, which is the most widely relevant for the target domain.
    return PubMedBackend()
