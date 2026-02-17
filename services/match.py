from services.llm_client import llm_json

def rank_calls(profile: dict, calls: list[dict]) -> list[dict]:
    prompt = f"""
Rank these funding calls for fit to the professor.
Return JSON array with:
- fit_score (0-100)
- title, agency, deadline, link
- why_fit (3 bullets)
- recommended_pitch (2 bullets)
- risks_or_gaps (bullets)

Profile:
{profile}

Calls:
{calls[:25]}
"""
    return llm_json(prompt)
