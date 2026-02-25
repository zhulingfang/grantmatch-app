# services/llm_client.py
import os, json, time
from typing import Any, Optional
import streamlit as st
from google import genai

def _get_api_key() -> str:
    # Prefer Streamlit secrets
    if "GEMINI_API_KEY" in st.secrets:
        return str(st.secrets["GEMINI_API_KEY"])
    if "GOOGLE_API_KEY" in st.secrets:
        return str(st.secrets["GOOGLE_API_KEY"])
    # Fallback to env
    return os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY") or ""

key = _get_api_key()

# Set ONLY ONE env var to avoid the warning
if key:
    os.environ["GOOGLE_API_KEY"] = key
    os.environ.pop("GEMINI_API_KEY", None)  # optional: remove if it exists

_client = genai.Client(api_key=key)

DEFAULT_MODEL = "gemini-2.5-flash"

def llm_text(prompt: str, model: str = DEFAULT_MODEL, temperature: float = 0.4) -> str:
    resp = _client.models.generate_content(
        model=model,
        contents=prompt,
        # Best-effort generation config; supported by Gemini API
        config={"temperature": temperature},
    )
    return (resp.text or "").strip()

def llm_json(prompt: str, model: str = DEFAULT_MODEL, temperature: float = 0.3,
             max_retries: int = 2, sleep_s: float = 0.8) -> Any:
    json_prompt = prompt.strip() + "\n\nReturn ONLY valid JSON. No markdown fences."
    last_err: Optional[Exception] = None
    last_text: str = ""

    for i in range(max_retries + 1):
        text = llm_text(json_prompt, model=model, temperature=temperature)
        last_text = (text or "").strip()

        # Quick guard: empty output
        if not last_text:
            last_err = ValueError("Empty model output")
            time.sleep(sleep_s)
            continue

        # If model wrapped JSON in ``` ... ```, strip it (common)
        if last_text.startswith("```"):
            last_text = last_text.strip("`")
            last_text = last_text.replace("json", "", 1).strip()

        try:
            return json.loads(last_text)
        except Exception as e:
            last_err = e
            # One more attempt: ask model to repair to JSON
            if i < max_retries:
                repair_prompt = (
                    "Fix the following to be valid JSON ONLY. "
                    "Do not add extra keys. Return only JSON.\n\n"
                    f"{last_text[:4000]}"
                )
                repaired = llm_text(repair_prompt, model=model, temperature=0.0).strip()
                try:
                    return json.loads(repaired)
                except Exception:
                    pass
            time.sleep(sleep_s)

    # Include a snippet so you can see what the model produced
    snippet = last_text[:800].replace("\n", "\\n")
    raise RuntimeError(f"Failed to parse JSON. Last error: {last_err}. Model output snippet: {snippet}")

