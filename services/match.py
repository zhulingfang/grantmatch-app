# services/match.py
from __future__ import annotations
from typing import Any, Dict, List
import re

from services.llm_client import llm_json, llm_text

def _normalize_tokens(text: str) -> set[str]:
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9\s\-]", " ", text)
    toks = set(t for t in text.split() if len(t) >= 3)
    return toks

def _recency_overlap_bonus(profile: dict, call_item: dict, cap: float = 20.0, scale: float = 6.0) -> float:
    recency = (profile or {}).get("keyword_recency_weights", {}) or {}
    if not isinstance(recency, dict) or not recency:
        return 0.0

    call_text = " ".join([
        str(call_item.get("title", "")),
        str(call_item.get("summary", "")),
        str(call_item.get("agency", "")),
    ]).lower()

    bonus = 0.0
    for kw, w in recency.items():
        if kw and str(kw).lower() in call_text:
            bonus += float(w)

    return min(cap, bonus * scale)

def _fallback_score(profile: Dict[str, Any], call: Dict[str, Any]) -> Dict[str, Any]:
    methods = profile.get("methods_keywords", []) or []
    themes  = profile.get("themes", []) or []
    prof_blob = " ".join(methods + themes)

    call_blob = " ".join([
        str(call.get("agency", "")),
        str(call.get("title", "")),
        str(call.get("summary", "")),
        str(call.get("deadline", "")),
    ])

    A = _normalize_tokens(prof_blob)
    B = _normalize_tokens(call_blob)
    if not A or not B:
        base_score = 5
    else:
        overlap = len(A & B)
        # scale overlap -> 0..100 but not all zeros
        base_score = min(100, 10 + overlap * 6)

    # ✅ recency bonus
    recency_bonus = _recency_overlap_bonus(profile, call, cap=20, scale=6)

    score = int(max(0, min(100, round(base_score + recency_bonus))))

    why = []
    top_hits = list((A & B))[:10]
    if top_hits:
        why.append(f"Keyword overlap: {', '.join(top_hits[:8])}")
    if recency_bonus > 0:
        why.append(f"Recent-publication bonus applied (+{recency_bonus:.1f})")
    why.append("Scored with fallback keyword matcher (LLM ranking unavailable).")

    pitch = (
        "Angle: connect the professor’s core methods/themes to the call’s focus. "
        "Propose 2–3 aims, identify datasets/testbeds, and highlight prior results."
    )

    out = dict(call)
    out["fit_score"] = score
    out["why_fit"] = why
    out["recommended_pitch"] = pitch
    out["rank_mode"] = "fallback"
    return out

def rank_calls(profile: Dict[str, Any], calls: List[Dict[str, Any]], attempt_id: int = 0) -> List[Dict[str, Any]]:
    if not profile or not isinstance(profile, dict):
        return []

    # keep only dict calls
    calls = [c for c in (calls or []) if isinstance(c, dict)]
    if not calls:
        return []

    # Keep prompt small: only send compact profile + top call fields
    compact_profile = {
        "themes": profile.get("themes", [])[:10],
        "methods_keywords": profile.get("methods_keywords", [])[:25],
        "domains": profile.get("domains", [])[:10],
    }

    compact_calls = []
    for i, c in enumerate(calls[:50]):
        compact_calls.append({
            "idx": i,
            "agency": c.get("agency", ""),
            "title": c.get("title", ""),
            "summary": c.get("summary", "")[:800],
            "deadline": c.get("deadline", ""),
            "link": c.get("link", ""),
            "source": c.get("source", ""),
        })

    prompt = f"""
You are ranking funding calls for research fit.

Return ONLY valid JSON (no markdown, no extra text) with this exact schema:
{{
  "ranked": [
    {{
      "idx": 0,
      "fit_score": 0,
      "why_fit": ["...","..."],
      "recommended_pitch": "..."
    }}
  ]
}}

Rules:
- fit_score is an integer 0..100 (higher is better).
- idx must match an input call idx.
- ONLY include calls with fit_score >= 50.
- why_fit must be a short list of 2-5 bullets.
- If none qualify, return: {{"ranked": []}}

Professor profile:
{compact_profile}

Calls:
{compact_calls}
""".strip()

    try:
        data = llm_json(prompt)
        ranked = data.get("ranked") if isinstance(data, dict) else None
        if not isinstance(ranked, list):
            raise RuntimeError("LLM returned invalid ranked list format.")

        # Validate + merge back into original calls
        out = []
        for item in ranked:
            if not isinstance(item, dict): 
                continue
            idx = item.get("idx")
            if not isinstance(idx, int) or idx < 0 or idx >= len(calls):
                continue

            merged = dict(calls[idx])
            base_llm_score = int(item.get("fit_score", 0) or 0)
            recency_bonus = _recency_overlap_bonus(profile, calls[idx], cap=10, scale=6)

            final_score = int(max(0, min(100, round(base_llm_score + recency_bonus))))

            merged["fit_score"] = final_score
            merged["why_fit"] = item.get("why_fit", []) or []
            if recency_bonus > 0:
                merged["why_fit"] = [f"Recent-publication bonus applied (+{recency_bonus:.1f})"] + merged["why_fit"]
            merged["recommended_pitch"] = item.get("recommended_pitch", "")
            merged["rank_mode"] = "llm"
            out.append(merged)

        # If LLM gave nothing usable, fallback
        if not out:
            raise RuntimeError("LLM ranked list could not be merged.")

        # Sort by score desc
        out.sort(key=lambda x: x.get("fit_score", 0), reverse=True)
        out = [x for x in out if int(x.get("fit_score", 0) or 0) >= 50]
        return out

    except Exception as e:
        # Fallback scoring so user still gets something meaningful
        scored = [_fallback_score(profile, c) for c in calls]
        scored.sort(key=lambda x: x.get("fit_score", 0), reverse=True)

        # add error info on the first item (help debugging in UI)
        if scored:
            scored[0]["why_fit"] = [f"LLM ranking failed: {e}"] + (scored[0].get("why_fit") or [])
        return scored
