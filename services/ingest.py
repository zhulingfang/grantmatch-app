# services/ingest.py
from __future__ import annotations

import re
from typing import Iterable, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader
from docx import Document

# -----------------------------
# Config
# -----------------------------

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0 Safari/537.36"
)
REQUEST_TIMEOUT_S = 25


# -----------------------------
# Internal helpers
# -----------------------------

def _http_get(url: str) -> str:
    r = requests.get(url, timeout=REQUEST_TIMEOUT_S, headers={"User-Agent": DEFAULT_UA})
    r.raise_for_status()
    return r.text


def _soup_text(html: str, max_chars: int = 200_000) -> str:
    soup = BeautifulSoup(html, "lxml")

    # Remove noisy stuff
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    for sel in ["header", "footer", "nav"]:
        for tag in soup.select(sel):
            tag.decompose()

    text = soup.get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def _normalize_whitespace(text: str) -> str:
    text = re.sub(r"\r\n", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# -----------------------------
# 1) Extract proposals text (PDF/DOCX)
# -----------------------------

def extract_proposal_texts(uploaded_files: Optional[Iterable]) -> str:
    """
    Read uploaded proposal files (Streamlit file_uploader objects), return combined text.
    Supports PDF and DOCX.
    """
    if not uploaded_files:
        return ""

    chunks: List[str] = []
    for f in uploaded_files:
        name = (getattr(f, "name", "") or "").lower()

        try:
            if name.endswith(".pdf"):
                chunks.append(_extract_pdf_text(f))
            elif name.endswith(".docx"):
                chunks.append(_extract_docx_text(f))
            else:
                chunks.append(f"[WARN] Unsupported file type: {name}")
        except Exception as e:
            chunks.append(f"[WARN] Failed to extract {name}: {e}")

    return _normalize_whitespace("\n\n".join(chunks))


def _extract_pdf_text(file_obj) -> str:
    reader = PdfReader(file_obj)
    pages = []
    for p in reader.pages:
        pages.append(p.extract_text() or "")
    return "\n".join(pages).strip()


def _extract_docx_text(file_obj) -> str:
    doc = Document(file_obj)
    paras = [p.text for p in doc.paragraphs if p.text is not None]
    return "\n".join(paras).strip()


# -----------------------------
# 2) Publications ingestion (URL or paste)
# -----------------------------

def fetch_publications_text(
    url: str = "",
    pasted: str = "",
    source_type: str = "Auto",
    max_chars: int = 200_000,
) -> Tuple[str, List[str]]:
    """
    Returns (pub_text, warnings).

    source_type: Auto | Google Scholar | DBLP | Webpage | BibTeX/Paste
    Behavior:
      - If pasted is provided, use it (most reliable).
      - Else try URL based on source_type.
    """
    warnings: List[str] = []
    pasted = (pasted or "").strip()
    url = (url or "").strip()

    if source_type == "BibTeX/Paste" or pasted:
        return _normalize_whitespace(pasted)[:max_chars], warnings

    if not url:
        warnings.append("No publications URL or pasted publication list provided.")
        return "", warnings

    st = (source_type or "Auto").lower()

    # Auto-detect
    if st == "auto":
        if "dblp.org" in url:
            st = "dblp"
        elif "scholar.google" in url:
            st = "google scholar"
        else:
            st = "webpage"

    try:
        if st == "dblp":
            text = _fetch_dblp(url, max_chars=max_chars)
        elif st in ["google scholar", "scholar", "googlescholar"]:
            text, w = _fetch_google_scholar_best_effort(url, max_chars=max_chars)
            warnings.extend(w)
        else:
            html = _http_get(url)
            text = _soup_text(html, max_chars=max_chars)
    except Exception as e:
        warnings.append(f"Failed to fetch publications from URL: {e}")
        return "", warnings

    if not text or len(text) < 200:
        warnings.append("Publications page returned little/no usable text. Consider pasting BibTeX/titles.")
        return "", warnings

    return _normalize_whitespace(text)[:max_chars], warnings


def _fetch_dblp(url: str, max_chars: int = 200_000) -> str:
    html = _http_get(url)
    soup = BeautifulSoup(html, "lxml")

    items = []
    for entry in soup.select("li.entry"):
        title = entry.select_one("span.title")
        year = entry.select_one("span.year")
        venue = entry.select_one("span.venue")

        t = title.get_text(" ", strip=True) if title else ""
        y = year.get_text(" ", strip=True) if year else ""
        v = venue.get_text(" ", strip=True) if venue else ""
        line = " | ".join([x for x in [t, v, y] if x])
        if line:
            items.append(line)

    if not items:
        return _soup_text(html, max_chars=max_chars)

    text = "DBLP Publications:\n" + "\n".join(items)
    return text[:max_chars]


def _fetch_google_scholar_best_effort(url: str, max_chars: int = 200_000) -> Tuple[str, List[str]]:
    """
    Scholar often blocks automation. Try once; if blocked, return empty text + warning.
    """
    warnings: List[str] = []
    try:
        html = _http_get(url)

        # Common block signal
        if "unusual traffic" in html.lower() or "sorry" in html.lower():
            warnings.append(
                "Google Scholar blocked automated access. Please paste a publications list, or upload your CV LaTeX."
            )
            return "", warnings

        soup = BeautifulSoup(html, "lxml")
        items = []
        for row in soup.select("tr.gsc_a_tr"):
            title_el = row.select_one("a.gsc_a_at")
            year_el = row.select_one("span.gsc_a_h")
            title = title_el.get_text(" ", strip=True) if title_el else ""
            year = year_el.get_text(" ", strip=True) if year_el else ""
            if title:
                items.append(f"{title} | {year}".strip(" |"))

        if not items:
            warnings.append("Could not reliably parse Scholar entries; please paste publications or upload CV LaTeX.")
            return "", warnings

        text = "Google Scholar Publications:\n" + "\n".join(items)
        return text[:max_chars], warnings

    except Exception as e:
        warnings.append(f"Failed to fetch Google Scholar (often blocked). Error: {e}")
        return "", warnings


# -----------------------------
# 3) Determine whether fallback needed
# -----------------------------

def needs_publications_fallback(pub_text: str, warnings: List[str]) -> bool:
    """
    Returns True if we should ask user to provide publications via CV/LaTeX/paste.
    """
    if pub_text and len(pub_text) > 500:
        return False

    for w in warnings or []:
        wl = w.lower()
        if "blocked" in wl or "little/no usable text" in wl or "failed" in wl:
            return True

    return True


# -----------------------------
# 4) Extract publications from LaTeX CV (best effort)
# -----------------------------

def extract_publications_from_latex(latex_text: str, max_chars: int = 200_000) -> str:
    """
    Light LaTeX -> text cleaning for publications section.
    Not perfect, but typically sufficient for LLM profiling/matching.
    """
    if not latex_text:
        return ""

    t = latex_text

    # Remove comments
    t = re.sub(r"(?m)^%.*$", "", t)

    # Keep section titles
    t = re.sub(r"\\(section|subsection|subsubsection)\*?\{([^}]*)\}", r"\n\2\n", t)

    # Preserve visible content in common formatting commands
    t = re.sub(r"\\textbf\{([^}]*)\}", r"\1", t)
    t = re.sub(r"\\emph\{([^}]*)\}", r"\1", t)
    t = re.sub(r"\\href\{[^}]*\}\{([^}]*)\}", r"\1", t)
    t = re.sub(r"\\url\{([^}]*)\}", r"\1", t)

    # Replace list items with bullets
    t = re.sub(r"\\item", "\n- ", t)

    # Remove cite commands
    t = re.sub(r"\\cite\{[^}]*\}", "", t)

    # Remove begin/end environments markers
    t = re.sub(r"\\begin\{[^\}]+\}", "\n", t)
    t = re.sub(r"\\end\{[^\}]+\}", "\n", t)

    # Remove remaining LaTeX commands (best effort)
    t = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?(?:\{[^}]*\})?", "", t)

    # Remove braces
    t = t.replace("{", "").replace("}", "")

    # Normalize whitespace
    t = re.sub(r"\s+", " ", t).strip()
    t = t.replace(" - ", "\n- ")

    return _normalize_whitespace(t)[:max_chars]
