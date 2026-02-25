import streamlit as st
import pandas as pd
import json
import time
from io import BytesIO

from services.ingest import extract_proposal_texts, extract_pdf_text_from_urls
from services.calls import fetch_calls, NSF_FUNDING_RSS, DOE_OSC_FOA_RSS
from services.profile import build_prof_profile
from services.match import rank_calls

st.set_page_config(page_title="GrantMatch", layout="wide")
st.title("GrantMatch Assistant")

# ---------- Cache wrappers ----------
@st.cache_data(show_spinner=False)
def cached_profile(pub_text: str, proposal_text: str):
    return build_prof_profile(pub_text, proposal_text)

@st.cache_data(ttl=24*3600, show_spinner=False)
def cached_calls(use_nsf: bool, use_doe: bool, use_grants: bool, keywords: tuple):
    return fetch_calls(
        use_nsf=use_nsf,
        use_doe=use_doe,
        use_grants=use_grants,
        keywords=list(keywords),
        limit_each=50
    )

# ---------- Helpers ----------
def _combine_publication_inputs(publications_text: str, summaries_text: str, pdf_text: str = "") -> str:
    parts = []
    pubs = (publications_text or "").strip()
    sums = (summaries_text or "").strip()
    pdfs = (pdf_text or "").strip()

    if pubs:
        parts.append(f"Publication titles/list:\n{pubs}")
    if sums:
        parts.append(f"Publication summaries:\n{sums}")
    if pdfs:
        parts.append(f"Publication PDF extracted text:\n{pdfs}")

    return "\n\n".join(parts).strip()

def _make_project_bundle() -> dict:
    return {
        "publication_pdf_urls": st.session_state.get("publication_pdf_urls", ""),
        "publications_text": st.session_state.get("publications_text", ""),
        "publication_summaries_text": st.session_state.get("publication_summaries_text", ""),
        "proposals_text": st.session_state.get("proposals_text", ""),
        "profile": st.session_state.get("profile", None),
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "version": "grantmatch_project_bundle_v1",
    }

def _load_project_bundle(bundle: dict):
    st.session_state.publication_pdf_urls = bundle.get("publication_pdf_urls", "")
    st.session_state.publications_text = bundle.get("publications_text", "")
    st.session_state.publication_summaries_text = bundle.get("publication_summaries_text", "")
    st.session_state.proposals_text = bundle.get("proposals_text", "")
    st.session_state.profile = bundle.get("profile", None)

    # reset downstream computed items so rerun is explicit
    st.session_state.calls = None
    st.session_state.ranked_calls = None
    st.session_state.call_errors = []
    st.session_state.last_step = "project_loaded"

# ---------- Session state ----------
defaults = {
    "publication_pdf_urls": "",
    "publications_text": "",
    "publication_summaries_text": "",
    "proposals_text": "",
    "profile": None,
    "calls": None,
    "call_errors": [],
    "ranked_calls": None,
    "attempt_id": 0,
    "last_step": "idle",
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ---------- Sidebar ----------
with st.sidebar:
    st.header("Project")

    # Load previously saved project JSON
    project_json_file = st.file_uploader("Load project JSON", type=["json"], key="project_json_loader")
    if project_json_file is not None:
        try:
            loaded = json.load(project_json_file)
            _load_project_bundle(loaded)
            st.success("Loaded project JSON.")
        except Exception as e:
            st.error(f"Failed to load project JSON: {e}")

    st.header("Inputs")
    st.text_area(
        "Publication ARXIV PDF URLs (optional, one per line)",
        height=100,
        key="publication_pdf_urls"
    )
    st.text_area(
        "Publication list / titles (paste LaTeX or plain text)",
        height=180,
        key="publications_text"
    )
    st.text_area(
        "Publication summaries (optional)",
        height=140,
        key="publication_summaries_text"
    )

    proposal_files = st.file_uploader(
        "Upload prior proposals (PDF/DOCX)",
        type=["pdf", "docx"],
        accept_multiple_files=True
    )
    reuse_saved_profile = st.checkbox(
        "Reuse saved profile if available",
        value=True,
        help="If a loaded project JSON already contains a profile, skip rebuilding it (saves API quota)."
    )
    st.subheader("Funding sources")
    use_nsf = st.checkbox("NSF (RSS)", value=True)
    use_doe = st.checkbox("DOE Office of Science (RSS)", value=True)
    use_grants = st.checkbox("Grants.gov (API)", value=False)  # default off until parser is stable

    with st.expander("Show feed URLs"):
        if use_nsf:
            st.caption(f"NSF RSS: {NSF_FUNDING_RSS}")
        if use_doe:
            st.caption(f"DOE RSS: {DOE_OSC_FOA_RSS}")

    refresh_calls = st.checkbox("Refresh calls (ignore cache)", value=False)

    run_btn = st.button("Build Profile & Find Calls", type="primary")
    rerun_btn = st.button("Rerank Results")
    st.caption("Rerank uses the same profile and fetched calls.")

# ---------- Main layout ----------
colA, colB = st.columns([0.9, 1.6])

# ---------- Run pipeline ----------
if run_btn:
    st.session_state.attempt_id = 0
    st.session_state.last_step = "started"

    # Extract publication PDF text (optional)
    pdf_url_lines = (st.session_state.get("publication_pdf_urls") or "").splitlines()
    pdf_urls = [u.strip() for u in pdf_url_lines if u.strip()]

    pdf_pub_text = ""
    if pdf_urls:
        with st.spinner("Reading publication PDFs from URLs..."):
            pdf_pub_text = extract_pdf_text_from_urls(pdf_urls, max_urls=5, max_chars_per_pdf=6000)

    with st.spinner("Reading prior proposals..."):
        # Only re-extract if new files uploaded this run; otherwise keep loaded text if any
        if proposal_files:
            st.session_state.proposals_text = extract_proposal_texts(proposal_files)
        st.session_state.last_step = "proposals_loaded"

    with st.spinner("Building professor profile..."):
        pubs_combined = _combine_publication_inputs(
            st.session_state.publications_text,
            st.session_state.publication_summaries_text,
            pdf_pub_text,
        )

        if not pubs_combined.strip():
            st.error("Please paste publication titles/list (and optionally summaries) before running.")
            st.stop()

        can_reuse = (
            reuse_saved_profile
            and isinstance(st.session_state.get("profile"), dict)
            and len(st.session_state.get("profile", {})) > 0
        )

        if can_reuse:
            st.info("Reusing saved profile from loaded project JSON.")
            st.session_state.last_step = "profile_reused"
        else:
            st.session_state.profile = cached_profile(pubs_combined, st.session_state.proposals_text or "")
            st.session_state.last_step = "profile_built"

    profile = st.session_state.profile if isinstance(st.session_state.profile, dict) else {}
    methods = profile.get("methods_keywords", []) if isinstance(profile.get("methods_keywords", []), list) else []
    keywords = tuple(methods)

    if refresh_calls:
        cached_calls.clear()

    with st.spinner("Fetching funding calls..."):
        result = cached_calls(use_nsf, use_doe, use_grants, keywords)

        # unpack (calls, errors) if fetch_calls returns tuple
        if isinstance(result, tuple) and len(result) == 2:
            calls, errors = result
        else:
            calls, errors = result, []

        if calls is None:
            calls = []
        if isinstance(calls, tuple):
            calls = list(calls)
        if not isinstance(calls, list):
            calls = [calls]

        # keep only dicts (ranking expects dicts)
        calls = [c for c in calls if isinstance(c, dict)]

        st.session_state.calls = calls
        st.session_state.call_errors = errors or []
        st.session_state.last_step = "calls_fetched"

    with st.spinner("Ranking calls..."):
        st.session_state.ranked_calls = rank_calls(
            st.session_state.profile,
            st.session_state.calls,
            attempt_id=0
        )
        st.session_state.last_step = "calls_ranked"

# ---------- Rerank only ----------
if rerun_btn:
    if not st.session_state.profile or not st.session_state.calls:
        st.error("Run 'Build Profile & Find Calls' first.")
    else:
        st.session_state.attempt_id += 1
        with st.spinner("Re-ranking..."):
            st.session_state.ranked_calls = rank_calls(
                st.session_state.profile,
                st.session_state.calls,
                attempt_id=st.session_state.attempt_id
            )
        st.session_state.last_step = "calls_reranked"

# ---------- Save project JSON ----------
bundle = _make_project_bundle()
bundle_name = ("grantmatch_project").strip().replace(" ", "_")
bundle_bytes = json.dumps(bundle, ensure_ascii=False, indent=2).encode("utf-8")

st.download_button(
    label="Save Project JSON",
    data=bundle_bytes,
    file_name=f"{bundle_name}.json",
    mime="application/json",
    width="content"
)

# ---------- Debug / status ----------
st.write(
    "Debug:",
    "last_step=", st.session_state.get("last_step"),
    "pub_chars=", len(_combine_publication_inputs(st.session_state.publications_text, st.session_state.publication_summaries_text)),
    "proposal_chars=", len(st.session_state.get("proposals_text") or ""),
    "calls=", len(st.session_state.get("calls") or []),
    "ranked=", len(st.session_state.get("ranked_calls") or []),
)

if st.session_state.get("call_errors"):
    with st.expander("Some sources failed (details)"):
        for e in st.session_state.call_errors:
            st.write(f"- {e}")

# ---------- Display ----------
with colA:
    st.subheader("Professor Profile")
    if st.session_state.profile:
        with st.expander("Show profile details", expanded=False):
            st.json(st.session_state.profile)

        # Optional quick view of recency keyword signal
        if isinstance(st.session_state.profile, dict) and st.session_state.profile.get("keyword_recency_weights"):
            with st.expander("Recency-weighted keyword signal"):
                st.json(st.session_state.profile["keyword_recency_weights"])
    else:
        st.info("Paste publication titles/summaries and click 'Build Profile & Find Calls'.")

with colB:
    st.subheader("Matched Calls (Ranked)")
    ranked = st.session_state.ranked_calls
    if ranked:
        df = pd.DataFrame(ranked)
        cols = [c for c in ["fit_score", "rank_mode", "agency", "title", "deadline", "link"] if c in df.columns]
        if cols:
            st.dataframe(df[cols], width="stretch", hide_index=True)
        else:
            st.dataframe(df, width="stretch", hide_index=True)

        idx = st.number_input("Inspect result index", min_value=0, max_value=len(ranked)-1, value=0)
        item = ranked[int(idx)]

        st.markdown("### Why it fits")
        why_fit = item.get("why_fit", [])
        if isinstance(why_fit, list):
            for w in why_fit:
                st.write(f"- {w}")
        else:
            st.write(why_fit)

        st.markdown("### Recommended pitch")
        st.write(item.get("recommended_pitch", ""))

        if item.get("link"):
            st.markdown(f"[Open call link]({item['link']})")
    else:
        st.info("No ranked calls yet.")