# services/llm_client.py
import json
import time
from typing import Any, Optional

from google import genai

# The SDK will automatically read GEMINI_API_KEY from env if set. :contentReference[oaicite:2]{index=2}
_client = genai.Client()

DEFAULT_TEXT_MODEL = "gemini-2.5-flash"  # fast + good quality for app flows
DEFAULT_JSON_MODEL = "gemini-2.5-flash"

def llm_text(prompt: str, model: str = DEFAULT_TEXT_MODEL) -> str:
    resp = _client.models.generate_content(
        model=model,
        contents=prompt,
    )
    # The SDK returns text in resp.text for simple generations
    return (resp.text or "").strip()

def llm_json(
    prompt: str,
    model: str = DEFAULT_JSON_MODEL,
    max_retries: int = 2,
    sleep_s: float = 0.8,
) -> Any:
    """
    Ask the model to return ONLY valid JSON, then parse it.
    Retries if parsing fails.
    """
    json_prompt = (
        prompt.strip()
        + "\n\nReturn ONLY valid JSON. Do not include markdown fences."
    )

    last_err: Optional[Exception] = None
    for _ in range(max_retries + 1):
        text = llm_text(json_prompt, model=model)
        try:
            return json.loads(text)
        except Exception as e:
            last_err = e
            time.sleep(sleep_s)

    raise RuntimeError(f"Failed to parse JSON from model output: {last_err}")
