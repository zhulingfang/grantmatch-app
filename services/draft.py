from services.llm_client import llm_text

def draft_concept(profile: dict, call: dict) -> str:
    prompt = f"""
Draft a 2-page concept (outline + key paragraphs).
Sections:
1) Project Summary (250–350 words)
2) Intellectual Merit
3) Broader Impacts
4) Approach (3 aims with methods + risks)
5) Work Plan (12–24 months milestones)
6) Relevant Prior Work (cite 5–8 publications by title based on the profile)

Call:
{call}

Profile:
{profile}
"""
    return llm_text(prompt)
