# services/calls.py
from __future__ import annotations
import feedparser
import requests
from typing import Dict, List, Tuple, Optional
import ssl, certifi
from urllib.request import urlopen

# NSF RSS directory exists; this is a commonly used NSF funding feed.
NSF_FUNDING_RSS = "https://www.nsf.gov/rss/rss_www_funding.xml"

# DOE Office of Science provides RSS feeds; FOA feed URL may vary by page.
# If this URL ever fails, we can swap to another DOE RSS feed from their RSS directory.
DOE_OSC_FOA_RSS = "https://science.osti.gov/rss/foa.xml"

def _fetch_url_bytes(url: str, timeout: int = 20) -> bytes:
    ctx = ssl.create_default_context(cafile=certifi.where())
    with urlopen(url, timeout=timeout, context=ctx) as r:
        return r.read()

def _normalize_call(c: dict, agency_default: str = "") -> dict:
    # map many possible field names into one schema
    return {
        "agency": c.get("agency") or c.get("source") or agency_default,
        "title": c.get("title") or c.get("opportunityTitle") or c.get("name") or "",
        "deadline": c.get("deadline") or c.get("closeDate") or c.get("dueDate") or "",
        "published": c.get("published") or c.get("postDate") or c.get("publishDate") or "",
        "link": c.get("link") or c.get("url") or c.get("opportunityURL") or c.get("href") or "",
        "summary": c.get("summary") or c.get("synopsis") or c.get("description") or "",
    }


def _fetch_rss(url: str, agency: str, limit: int = 50) -> List[Dict]:
    # IMPORTANT: Don't let feedparser fetch the URL itself (it won't use our SSL context).
    data = _fetch_url_bytes(url, timeout=20)  # uses certifi-based SSL context
    feed = feedparser.parse(data)

    # feedparser puts errors in feed.bozo / feed.bozo_exception
    if getattr(feed, "bozo", 0):
        raise RuntimeError(f"RSS parse error: {getattr(feed, 'bozo_exception', 'unknown')}")

    entries = getattr(feed, "entries", []) or []
    out = []
    for e in entries[:limit]:
        title = getattr(e, "title", "") or ""
        link = getattr(e, "link", "") or ""
        published = getattr(e, "published", "") or getattr(e, "updated", "") or ""
        summary = getattr(e, "summary", "") or getattr(e, "description", "") or ""

        out.append({
            "agency": agency,
            "title": title,
            "link": link,
            "published": published,
            "deadline": "",  # RSS often doesn't provide deadline
            "summary": summary,
            "source": f"{agency.lower()}_rss",
        })
    return out


def _fetch_grants_gov(keywords: List[str] | None = None, limit: int = 50) -> List[Dict]:
    """
    Minimal Grants.gov search. The exact schema can vary; treat as best-effort.
    """
    keywords = keywords or []
    url = "https://api.grants.gov/v1/api/search2"
    payload = {
        "keyword": " ".join(keywords[:10]),
        "rows": limit
    }
    r = requests.post(url, json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()

    # Best-effort normalization
    items = data.get("opportunities") or data.get("data") or []
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        out.append({
            "agency": it.get("agency", "Grants.gov"),
            "title": it.get("title", ""),
            "link": it.get("opportunityNumber", ""),  # you can map to real URL later
            "published": it.get("postDate", "") or "",
            "summary": it.get("description", "") or "",
            "deadline": it.get("closeDate", "") or "",
            "source": "grants.gov",
        })
    return out

def fetch_calls(
    use_nsf: bool = True,
    use_doe: bool = True,
    use_grants: bool = True,
    keywords: Optional[List[str]] = None,
    limit_each: int = 50
) -> Tuple[List[Dict], List[str]]:
    calls: List[Dict] = []
    errors: List[str] = []
    print("NSF RSS:", NSF_FUNDING_RSS)
    print("DOE RSS:", DOE_OSC_FOA_RSS)
    if use_nsf:
        try:
            nsf = _fetch_rss(NSF_FUNDING_RSS, "NSF", limit_each)
            calls.extend(nsf)
        except Exception as e:
            errors.append(f"NSF RSS failed: {e}")

    if use_doe:
        try:
            doe = _fetch_rss(DOE_OSC_FOA_RSS, "DOE", limit_each)
            calls.extend(doe)
        except Exception as e:
            errors.append(f"DOE RSS failed: {e}")

    if use_grants:
        try:
            kw = list(keywords) if keywords else []
            gg = _fetch_grants_gov(kw, limit=min(limit_each, 50))
            calls.extend(gg)
        except Exception as e:
            errors.append(f"Grants.gov failed: {e}")

    # normalize + filter
    normalized: List[Dict] = []
    for item in calls:
        if isinstance(item, dict):
            normalized.append(_normalize_call(item))

    normalized = [x for x in normalized if x.get("title") or x.get("link")]
    if not normalized:
        errors.append("DEBUG: No calls found from sources. Using fallback demo call.")
        normalized = [{
            "agency": "NSF",
            "title": "DEMO: Placeholder call (check RSS/API connectivity)",
            "deadline": "",
            "published": "",
            "link": "https://www.nsf.gov/funding/",
            "summary": "This is a fallback item so the rest of the pipeline can be tested.",
            "source": "fallback"
        }]
    return normalized, errors
