# services/profile.py
from services.llm_client import llm_json
import re
import math
from collections import defaultdict
from datetime import datetime

def build_prof_profile(pubs_text: str, proposals_text: str) -> dict:
    pubs = (pubs_text or "")[:12000]      # keep prompt manageable
    props = (proposals_text or "")[:8000] # proposals may be empty; that's ok

    prompt = f"""
You are extracting a research profile for a professor for grant matching.

Return ONLY valid JSON with EXACT keys:
{{
  "themes": [string],
  "methods_keywords": [string],
  "application_domains": [string],
  "strongest_prior_results": [string],
  "agencies_fit": [{{"agency": string, "why": string}}]
}}

Rules:
- Output must be valid JSON and nothing else.
- No markdown, no code fences, no commentary.
- Use 5-10 themes, 20-40 methods_keywords, 5-10 application_domains.

Publications (raw text):
{pubs}

Prior proposals (raw text):
{props}
"""
    profile = llm_json(prompt)  # or whatever your current line is

    # Ensure dict
    if not isinstance(profile, dict):
        profile = {}

    methods = profile.get("methods_keywords", [])
    if not isinstance(methods, list):
        methods = []

    profile["keyword_recency_weights"] = _build_keyword_recency_weights(
        pub_text=pubs_text,
        methods_keywords=methods,
        current_year=datetime.now().year,
    )

    return profile


def _split_pub_lines(pub_text: str) -> list[str]:
    # Keep non-empty lines only
    return [ln.strip() for ln in (pub_text or "").splitlines() if ln.strip()]

def _extract_year_from_line(line: str) -> int | None:
    # Match 19xx or 20xx
    m = re.search(r"\b(19\d{2}|20\d{2})\b", line)
    if not m:
        return None
    y = int(m.group(1))
    if 1950 <= y <= 2100:
        return y
    return None

def _year_weight(year: int | None, current_year: int = 2026, half_life_years: float = 5.0) -> float:
    """
    Exponential decay:
      current_year => ~1.0
      5 years old => ~0.5
      10 years old => ~0.25
    """
    if year is None:
        return 0.25  # unknown year gets small but nonzero weight
    age = max(0, current_year - year)
    return 0.5 ** (age / half_life_years)

def _build_keyword_recency_weights(pub_text: str, methods_keywords: list[str], current_year: int = 2026) -> dict:
    """
    Count keyword mentions across publication lines, weighted by publication recency.
    Returns a normalized dict: {keyword: weight in [0,1]}.
    """
    lines = _split_pub_lines(pub_text)
    scores = defaultdict(float)

    # pre-lower keywords
    kws = [k.strip() for k in (methods_keywords or []) if str(k).strip()]
    kws_lower = [(k, k.lower()) for k in kws]

    for line in lines:
        line_l = line.lower()
        y = _extract_year_from_line(line)
        w = _year_weight(y, current_year=current_year)

        for original_kw, kw_l in kws_lower:
            # simple substring match; good enough for v1
            if kw_l in line_l:
                scores[original_kw] += w

    if not scores:
        return {}

    maxv = max(scores.values()) or 1.0
    return {k: round(v / maxv, 4) for k, v in scores.items()}