from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Callable


Fetch = Callable[[str, dict[str, str] | None, int], bytes]


@dataclass(frozen=True)
class LiveSearchResult:
    title: str
    authors: list[str]
    locator_kind: str
    locator_value: str
    relevance: str
    status: str
    why_relevant: str
    provider: str


@dataclass(frozen=True)
class LiveSearchResponse:
    provider: str
    results: list[LiveSearchResult]
    notes: list[str]


def live_literature_search(
    query: str,
    *,
    providers: list[str],
    max_results: int = 8,
    ads_token_env: str = "ADS_API_TOKEN",
    fetch: Fetch = None,
) -> list[LiveSearchResponse]:
    fetcher = fetch or fetch_url
    responses = []
    for provider in providers:
        if provider == "arxiv":
            responses.append(search_arxiv(query, max_results=max_results, fetch=fetcher))
        elif provider == "ads":
            token = os.environ.get(ads_token_env, "")
            responses.append(search_ads(query, max_results=max_results, token=token, fetch=fetcher))
        elif provider in {"web", "crossref"}:
            responses.append(search_crossref(query, max_results=max_results, fetch=fetcher))
        else:
            responses.append(
                LiveSearchResponse(
                    provider=provider,
                    results=[],
                    notes=[f"Unknown live literature provider: {provider}."],
                )
            )
    return responses


def search_arxiv(
    query: str,
    *,
    max_results: int,
    fetch: Fetch = None,
) -> LiveSearchResponse:
    fetcher = fetch or fetch_url
    params = {
        "search_query": f"all:{query}",
        "start": "0",
        "max_results": str(max_results),
        "sortBy": "relevance",
        "sortOrder": "descending",
    }
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(params)
    try:
        xml_bytes = fetcher(url, {"User-Agent": USER_AGENT}, 20)
    except (OSError, urllib.error.URLError) as exc:
        return LiveSearchResponse("arxiv", [], [f"arXiv search failed: {exc}."])
    return LiveSearchResponse("arxiv", parse_arxiv_atom(xml_bytes, query), [])


def parse_arxiv_atom(xml_bytes: bytes, query: str) -> list[LiveSearchResult]:
    root = ET.fromstring(xml_bytes)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    results = []
    for entry in root.findall("atom:entry", ns):
        title = normalize_space(entry.findtext("atom:title", default="", namespaces=ns))
        summary = normalize_space(entry.findtext("atom:summary", default="", namespaces=ns))
        url = entry.findtext("atom:id", default="", namespaces=ns)
        authors = [
            normalize_space(author.findtext("atom:name", default="", namespaces=ns))
            for author in entry.findall("atom:author", ns)
        ]
        arxiv_id = url.rstrip("/").split("/")[-1]
        results.append(
            LiveSearchResult(
                title=title or arxiv_id,
                authors=[author for author in authors if author],
                locator_kind="arxiv_id" if arxiv_id else "url",
                locator_value=arxiv_id or url,
                relevance=infer_relevance(title, summary),
                status="candidate",
                why_relevant=f"arXiv live search matched query '{query}' in returned metadata.",
                provider="arxiv",
            )
        )
    return results


def search_ads(
    query: str,
    *,
    max_results: int,
    token: str,
    fetch: Fetch = None,
) -> LiveSearchResponse:
    if not token:
        return LiveSearchResponse(
            "ads",
            [],
            ["ADS search skipped: set ADS_API_TOKEN for authenticated ADS queries."],
        )
    fetcher = fetch or fetch_url
    fields = "bibcode,title,author,year,doi,identifier,abstract"
    params = {
        "q": query,
        "fl": fields,
        "rows": str(max_results),
        "start": "0",
    }
    url = "https://api.adsabs.harvard.edu/v1/search/query?" + urllib.parse.urlencode(params)
    try:
        payload = fetcher(
            url,
            {
                "Authorization": f"Bearer {token}",
                "User-Agent": USER_AGENT,
            },
            20,
        )
    except (OSError, urllib.error.URLError) as exc:
        return LiveSearchResponse("ads", [], [f"ADS search failed: {exc}."])
    return LiveSearchResponse("ads", parse_ads_json(payload, query), [])


def parse_ads_json(payload: bytes, query: str) -> list[LiveSearchResult]:
    data = json.loads(payload.decode("utf-8"))
    docs = data.get("response", {}).get("docs", [])
    results = []
    for doc in docs:
        title = first(doc.get("title")) or doc.get("bibcode", "")
        bibcode = doc.get("bibcode", "")
        abstract = doc.get("abstract", "")
        doi = first(doc.get("doi"))
        locator = f"https://ui.adsabs.harvard.edu/abs/{urllib.parse.quote(bibcode)}/abstract" if bibcode else ""
        if doi:
            locator = f"https://doi.org/{doi}"
        results.append(
            LiveSearchResult(
                title=normalize_space(title),
                authors=[normalize_space(author) for author in doc.get("author", [])],
                locator_kind="url" if locator else "unknown",
                locator_value=locator or "to_be_filled",
                relevance=infer_relevance(title, abstract),
                status="candidate" if locator else "unverified",
                why_relevant=f"ADS live search matched query '{query}' in returned metadata.",
                provider="ads",
            )
        )
    return results


def search_crossref(
    query: str,
    *,
    max_results: int,
    fetch: Fetch = None,
) -> LiveSearchResponse:
    fetcher = fetch or fetch_url
    params = {
        "query.bibliographic": query,
        "rows": str(max_results),
    }
    url = "https://api.crossref.org/works?" + urllib.parse.urlencode(params)
    try:
        payload = fetcher(url, {"User-Agent": USER_AGENT}, 20)
    except (OSError, urllib.error.URLError) as exc:
        return LiveSearchResponse("web", [], [f"Crossref web metadata search failed: {exc}."])
    return LiveSearchResponse("web", parse_crossref_json(payload, query), [])


def parse_crossref_json(payload: bytes, query: str) -> list[LiveSearchResult]:
    data = json.loads(payload.decode("utf-8"))
    items = data.get("message", {}).get("items", [])
    results = []
    for item in items:
        title = first(item.get("title")) or item.get("DOI", "")
        doi = item.get("DOI", "")
        abstract = normalize_space(strip_tags(item.get("abstract", "")))
        authors = [
            normalize_space(" ".join(part for part in [author.get("given", ""), author.get("family", "")] if part))
            for author in item.get("author", [])
        ]
        results.append(
            LiveSearchResult(
                title=normalize_space(title),
                authors=[author for author in authors if author],
                locator_kind="url" if doi else "unknown",
                locator_value=f"https://doi.org/{doi}" if doi else "to_be_filled",
                relevance=infer_relevance(title, abstract),
                status="candidate" if doi else "unverified",
                why_relevant=f"Crossref live metadata search matched query '{query}'.",
                provider="web",
            )
        )
    return results


def fetch_url(url: str, headers: dict[str, str] | None = None, timeout: int = 20) -> bytes:
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def infer_relevance(title: str, abstract: str) -> str:
    text = f"{title} {abstract}".lower()
    if "counterexample" in text or "patholog" in text:
        return "counterexample_source"
    if "example" in text or "cubic" in text or "base change" in text:
        return "example_source"
    if "theorem" in text or "lemma" in text or "proof" in text:
        return "possible_theorem_source"
    return "background"


def normalize_space(text: str | None) -> str:
    return " ".join(str(text or "").split())


def strip_tags(text: str) -> str:
    output = []
    in_tag = False
    for char in text:
        if char == "<":
            in_tag = True
        elif char == ">":
            in_tag = False
        elif not in_tag:
            output.append(char)
    return "".join(output)


def first(value):
    if isinstance(value, list):
        return value[0] if value else None
    return value


USER_AGENT = "espresso-proof/0.1 (local research workflow)"
