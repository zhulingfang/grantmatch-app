from services.llm_client import llm_json

def build_prof_profile(pubs_text: str, proposals_text: str) -> dict:
    prompt = f"""
Create a professor research profile as JSON with:
- themes (5-10)
- methods_keywords (20-40)
- key_application_domains (5-10)
- strongest_prior_results (bullets)
- reusable_proposal_phrases (bullets; do NOT copy long verbatim text)
- agencies_fit (list of {{agency, why}})

Publications:
{pubs_text[:12000]}

Prior proposals:
{proposals_text[:12000]}
"""
    return llm_json(prompt)
