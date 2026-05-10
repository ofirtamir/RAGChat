import uuid
import streamlit as st
from pathlib import Path

from config import UPLOADS_DIR, GOOGLE_API_KEY, LANGFUSE_ENABLED
from src.observability import initialize_langfuse
from src.document_loader import load_document, SUPPORTED_EXTENSIONS
from src.chunker import chunk_documents
from src.vector_store import add_documents, get_document_count, clear_vector_store
from src.graph import run_rag_pipeline

st.set_page_config(
    page_title="RAGChat",
    page_icon="🔮",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --- Custom CSS: Light theme, RTL, blue-cyan gradient (inspired by Gini AI) ---
st.markdown("""
<style>
/* ===== Import Hebrew-friendly font ===== */
@import url('https://fonts.googleapis.com/css2?family=Rubik:wght@300;400;500;600;700;800&display=swap');

/* ===== RTL Global ===== */
html, body, [class*="css"] {
    font-family: 'Rubik', -apple-system, BlinkMacSystemFont, sans-serif !important;
    direction: rtl !important;
}

.stApp {
    background: linear-gradient(180deg, #f8fafc 0%, #eef2f7 100%);
}

/* ===== Hide default Streamlit branding ===== */
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: hidden;}

/* ===== Sidebar on the RIGHT ===== */
section[data-testid="stSidebar"] {
    background: #ffffff;
    border-left: 1px solid #e2e8f0;
    border-right: none;
    min-width: 300px;
    direction: rtl;
    right: 0;
    left: auto;
    box-shadow: -4px 0 20px rgba(0, 0, 0, 0.04);
}

/* Move sidebar toggle to right side */
[data-testid="stSidebar"][aria-expanded="true"] {
    right: 0;
    left: auto;
}

section[data-testid="stSidebar"] .stMarkdown h1,
section[data-testid="stSidebar"] .stMarkdown h2,
section[data-testid="stSidebar"] .stMarkdown h3 {
    color: #1e293b !important;
    font-weight: 700;
    text-align: right;
}

/* ===== Sidebar upload area ===== */
section[data-testid="stSidebar"] [data-testid="stFileUploader"] {
    background: #f8fafc;
    border: 2px dashed #cbd5e1;
    border-radius: 14px;
    padding: 16px;
    transition: all 0.3s ease;
}

section[data-testid="stSidebar"] [data-testid="stFileUploader"]:hover {
    border-color: #38bdf8;
    background: #f0f9ff;
}

/* ===== Primary Buttons - Blue/Cyan gradient ===== */
.stButton > button[kind="primary"],
.stButton > button {
    background: linear-gradient(135deg, #0ea5e9 0%, #38bdf8 50%, #67e8f9 100%);
    color: white;
    border: none;
    border-radius: 12px;
    font-weight: 600;
    font-size: 15px;
    font-family: 'Rubik', sans-serif !important;
    padding: 12px 28px;
    transition: all 0.3s ease;
    box-shadow: 0 4px 14px rgba(14, 165, 233, 0.3);
    letter-spacing: 0;
}

.stButton > button:hover {
    background: linear-gradient(135deg, #0284c7 0%, #0ea5e9 50%, #38bdf8 100%);
    box-shadow: 0 6px 20px rgba(14, 165, 233, 0.4);
    transform: translateY(-2px);
}

.stButton > button:active {
    transform: translateY(0);
}

/* ===== Metric cards ===== */
[data-testid="stMetric"] {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 14px;
    padding: 18px 22px;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
    direction: rtl;
}

[data-testid="stMetric"] label {
    color: #64748b !important;
    font-size: 13px;
    font-weight: 500;
}

[data-testid="stMetric"] [data-testid="stMetricValue"] {
    color: #0ea5e9 !important;
    font-weight: 700;
    font-size: 28px !important;
}

/* ===== Chat messages ===== */
[data-testid="stChatMessage"] {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 18px;
    padding: 20px 24px;
    margin-bottom: 14px;
    transition: all 0.2s ease;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.03);
    direction: rtl;
    text-align: right;
}

[data-testid="stChatMessage"]:hover {
    border-color: #cbd5e1;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.06);
}

/* User messages - blue accent on the RIGHT */
[data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-user"]) {
    background: linear-gradient(135deg, #f0f9ff 0%, #ffffff 100%);
    border-right: 4px solid #0ea5e9;
    border-left: none;
}

/* Assistant messages - cyan accent */
[data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-assistant"]) {
    background: linear-gradient(135deg, #ffffff 0%, #f8fafc 100%);
    border-right: 4px solid #67e8f9;
    border-left: none;
}

/* ===== Chat input ===== */
[data-testid="stChatInput"] {
    border-color: #e2e8f0;
    direction: rtl;
}

[data-testid="stChatInput"] textarea {
    background: #ffffff !important;
    border: 2px solid #e2e8f0 !important;
    border-radius: 16px !important;
    color: #1e293b !important;
    font-size: 15px;
    font-family: 'Rubik', sans-serif !important;
    padding: 16px 20px !important;
    transition: all 0.3s ease;
    direction: rtl !important;
    text-align: right !important;
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.06);
}

[data-testid="stChatInput"] textarea::placeholder {
    text-align: right !important;
    color: #94a3b8 !important;
}

[data-testid="stChatInput"] textarea:focus {
    border-color: #38bdf8 !important;
    box-shadow: 0 0 0 4px rgba(56, 189, 248, 0.12), 0 4px 16px rgba(0, 0, 0, 0.06) !important;
}

[data-testid="stChatInput"] button {
    background: linear-gradient(135deg, #0ea5e9, #38bdf8) !important;
    border-radius: 12px !important;
    color: white !important;
}

/* ===== Expanders ===== */
.streamlit-expanderHeader {
    background: #f8fafc !important;
    border: 1px solid #e2e8f0 !important;
    border-radius: 12px !important;
    color: #64748b !important;
    font-size: 13px;
    font-weight: 500;
    direction: rtl;
}

[data-testid="stExpander"] {
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    direction: rtl;
}

/* ===== Progress bar ===== */
.stProgress > div > div {
    background: linear-gradient(90deg, #0ea5e9, #38bdf8, #67e8f9) !important;
    border-radius: 8px;
}

/* ===== Success/Error alerts ===== */
.stSuccess {
    background: #f0fdf4 !important;
    border: 1px solid #86efac !important;
    border-radius: 12px !important;
    color: #166534 !important;
    direction: rtl;
}

.stError {
    background: #fef2f2 !important;
    border: 1px solid #fca5a5 !important;
    border-radius: 12px !important;
    color: #991b1b !important;
    direction: rtl;
}

/* ===== Divider ===== */
hr {
    border-color: #e2e8f0 !important;
    margin: 20px 0 !important;
}

/* ===== Scrollbar ===== */
::-webkit-scrollbar {
    width: 6px;
    height: 6px;
}

::-webkit-scrollbar-track {
    background: #f1f5f9;
}

::-webkit-scrollbar-thumb {
    background: #cbd5e1;
    border-radius: 3px;
}

::-webkit-scrollbar-thumb:hover {
    background: #94a3b8;
}

/* ===== Main header ===== */
.main-header {
    text-align: center;
    padding: 60px 0 20px 0;
}

.main-header .logo-dots {
    display: flex;
    justify-content: center;
    gap: 6px;
    margin-bottom: 24px;
}

.main-header .logo-dots span {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    display: inline-block;
    animation: pulse 2s infinite;
}

.main-header .logo-dots span:nth-child(1) { background: #0ea5e9; animation-delay: 0s; }
.main-header .logo-dots span:nth-child(2) { background: #38bdf8; animation-delay: 0.2s; }
.main-header .logo-dots span:nth-child(3) { background: #67e8f9; animation-delay: 0.4s; }
.main-header .logo-dots span:nth-child(4) { background: #0ea5e9; animation-delay: 0.6s; }
.main-header .logo-dots span:nth-child(5) { background: #38bdf8; animation-delay: 0.8s; }

@keyframes pulse {
    0%, 100% { opacity: 0.4; transform: scale(0.8); }
    50% { opacity: 1; transform: scale(1.2); }
}

.main-header h1 {
    color: #1e293b;
    font-size: 2rem;
    font-weight: 700;
    margin-bottom: 8px;
}

.main-header p {
    color: #94a3b8;
    font-size: 16px;
    font-weight: 400;
}

/* ===== Action buttons row ===== */
.action-buttons {
    display: flex;
    justify-content: center;
    gap: 12px;
    margin: 30px 0;
    flex-wrap: wrap;
    direction: rtl;
}

.action-btn {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    padding: 10px 20px;
    font-size: 14px;
    color: #475569;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.2s ease;
    font-family: 'Rubik', sans-serif;
    text-decoration: none;
    box-shadow: 0 1px 4px rgba(0, 0, 0, 0.04);
}

.action-btn:hover {
    border-color: #38bdf8;
    color: #0ea5e9;
    box-shadow: 0 4px 12px rgba(14, 165, 233, 0.1);
    transform: translateY(-1px);
}

.action-btn .icon {
    font-size: 18px;
}

/* ===== Empty state ===== */
.empty-state {
    text-align: center;
    padding: 40px 20px;
    color: #94a3b8;
}

.empty-state h3 {
    color: #64748b;
    font-weight: 600;
    font-size: 18px;
    margin-bottom: 8px;
}

.empty-state p {
    color: #94a3b8;
    font-size: 14px;
    max-width: 400px;
    margin: 0 auto;
    line-height: 1.7;
}

/* ===== Source badges ===== */
.source-badge {
    display: inline-block;
    background: #f0f9ff;
    border: 1px solid #bae6fd;
    border-radius: 8px;
    padding: 4px 14px;
    margin: 3px;
    font-size: 12px;
    color: #0369a1;
    font-weight: 500;
    direction: rtl;
}

/* ===== Sidebar logo area ===== */
.sidebar-logo {
    text-align: center;
    padding: 20px 0 28px 0;
    border-bottom: 1px solid #e2e8f0;
    margin-bottom: 24px;
}

.sidebar-logo h2 {
    background: linear-gradient(135deg, #0ea5e9, #38bdf8, #67e8f9);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    font-size: 1.6rem;
    font-weight: 800;
    margin: 0;
}

.sidebar-logo p {
    color: #94a3b8;
    font-size: 12px;
    margin: 6px 0 0 0;
}

/* ===== Sidebar section titles ===== */
.sidebar-section {
    color: #94a3b8;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin: 24px 0 12px 0;
    text-align: right;
}

/* ===== Sidebar chat history ===== */
.history-item {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 14px;
    border-radius: 10px;
    color: #475569;
    font-size: 13px;
    font-weight: 400;
    cursor: pointer;
    transition: all 0.2s ease;
    direction: rtl;
    text-align: right;
    margin-bottom: 4px;
    font-family: 'Rubik', sans-serif;
}

.history-item:hover {
    background: #f0f9ff;
    color: #0ea5e9;
}

.history-item .icon {
    font-size: 14px;
    opacity: 0.5;
}

/* ===== Route badge ===== */
.route-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 12px;
    border-radius: 20px;
    font-size: 12px;
    font-weight: 500;
    font-family: 'Rubik', sans-serif;
}

.route-badge.retrieval {
    background: #dbeafe;
    color: #1d4ed8;
    border: 1px solid #93c5fd;
}

.route-badge.chitchat {
    background: #f0fdf4;
    color: #166534;
    border: 1px solid #86efac;
}

.route-badge.full-doc {
    background: #fef9c3;
    color: #854d0e;
    border: 1px solid #fde047;
}

/* ===== Spinner ===== */
.stSpinner > div {
    border-top-color: #0ea5e9 !important;
}

/* ===== Fix text alignment in main area ===== */
.stMarkdown, .stMarkdown p, .stMarkdown li {
    text-align: right;
    direction: rtl;
}

/* ===== New chat button style ===== */
.new-chat-btn {
    display: block;
    width: 100%;
    padding: 14px 20px;
    background: linear-gradient(135deg, #0ea5e9 0%, #38bdf8 50%, #67e8f9 100%);
    color: white;
    border: none;
    border-radius: 14px;
    font-size: 16px;
    font-weight: 600;
    font-family: 'Rubik', sans-serif;
    cursor: pointer;
    transition: all 0.3s ease;
    box-shadow: 0 4px 14px rgba(14, 165, 233, 0.3);
    text-align: center;
    text-decoration: none;
    margin-bottom: 20px;
}

.new-chat-btn:hover {
    box-shadow: 0 6px 20px rgba(14, 165, 233, 0.4);
    transform: translateY(-1px);
}
</style>
""", unsafe_allow_html=True)

# --- Initialize Langfuse ---
initialize_langfuse()

# --- API Key Check ---
if not GOOGLE_API_KEY:
    st.error(
        "**GOOGLE_API_KEY is not set.** "
        "Copy `.env.example` to `.env` and add your Google API key. "
        "Get one at https://aistudio.google.com/apikey"
    )
    st.stop()

# --- Sidebar (RIGHT side) ---
with st.sidebar:
    st.markdown("""
    <div class="sidebar-logo">
        <h2>RAGChat AI</h2>
        <p>חיפוש חכם במסמכים</p>
    </div>
    """, unsafe_allow_html=True)

    # New chat button
    if st.button("+ שיחה חדשה", type="primary", use_container_width=True):
        st.session_state.messages = []
        st.session_state.session_id = str(uuid.uuid4())
        st.rerun()

    st.markdown('<div class="sidebar-section">העלאת מסמכים</div>', unsafe_allow_html=True)

    uploaded_files = st.file_uploader(
        "גרור ושחרר קבצים כאן",
        type=[ext.lstrip(".") for ext in SUPPORTED_EXTENSIONS],
        accept_multiple_files=True,
        label_visibility="collapsed",
    )

    if uploaded_files and st.button("עיבוד מסמכים", type="primary", use_container_width=True):
        progress = st.progress(0)
        status = st.empty()

        for i, uf in enumerate(uploaded_files):
            status.text(f"...{uf.name} מעבד")
            save_path = UPLOADS_DIR / uf.name
            save_path.write_bytes(uf.getvalue())

            docs = load_document(save_path)
            chunks = chunk_documents(docs)
            add_documents(chunks)

            progress.progress((i + 1) / len(uploaded_files))

        status.text("")
        progress.empty()
        st.success(f"!עובדו {len(uploaded_files)} קבצים בהצלחה")

    st.markdown('<div class="sidebar-section">מאגר הידע</div>', unsafe_allow_html=True)

    try:
        count = get_document_count()
    except Exception:
        count = 0
    st.metric("קטעים מאונדקסים", count)

    if count > 0:
        st.markdown("")
        if st.button("מחק את כל המסמכים", use_container_width=True):
            clear_vector_store()
            st.rerun()

    # Langfuse status
    if LANGFUSE_ENABLED:
        st.markdown('<div class="sidebar-section">ניטור</div>', unsafe_allow_html=True)
        st.markdown("✅ **Langfuse מחובר** — [פתח דשבורד](https://cloud.langfuse.com)")

    # Chat history in sidebar
    if st.session_state.get("messages"):
        st.markdown('<div class="sidebar-section">היסטוריית שיחות</div>', unsafe_allow_html=True)
        user_msgs = [m for m in st.session_state.messages if m["role"] == "user"]
        for msg in user_msgs[-8:]:
            text = msg["content"][:40] + "..." if len(msg["content"]) > 40 else msg["content"]
            st.markdown(
                f'<div class="history-item"><span class="icon">💬</span>{text}</div>',
                unsafe_allow_html=True,
            )

# --- Main: Chat Interface ---
# Session state for chat history
if "messages" not in st.session_state:
    st.session_state.messages = []
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())

# Empty state - welcome screen
if not st.session_state.messages:
    st.markdown("""
    <div class="main-header">
        <div class="logo-dots">
            <span></span><span></span><span></span><span></span><span></span>
        </div>
        <h1>שלום</h1>
        <p>אנחנו כאן כדי לעזור לך למצוא הכל.</p>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div class="action-buttons">
        <div class="action-btn"><span class="icon">📁</span>האחסון שלי</div>
        <div class="action-btn"><span class="icon">🔍</span>חיפוש במסמכים</div>
        <div class="action-btn"><span class="icon">📄</span>סיכום מסמך</div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div class="empty-state">
        <p>העלה מסמכים באמצעות הסיידבר, ואז שאל שאלות על התוכן שלהם.<br>
        הבינה המלאכותית תחפש במסמכים שלך כדי למצוא תשובות רלוונטיות.</p>
    </div>
    """, unsafe_allow_html=True)

# Display chat history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if "route" in msg and msg["route"]:
            route = msg["route"]
            if route["needs_retrieval"]:
                st.markdown(
                    '<span class="route-badge retrieval">🔍 אחזור מסמכים</span>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    '<span class="route-badge chitchat">💬 שיחה חופשית</span>',
                    unsafe_allow_html=True,
                )
        if "rewritten" in msg and msg.get("rewritten") and msg.get("route", {}).get("needs_retrieval", True):
            with st.expander("שאילתה מותאמת לחיפוש"):
                rw = msg["rewritten"]
                st.markdown(f"**שאילתה מקורית:** {rw['original_query']}")
                st.markdown("**שאילתות חיפוש:**")
                for rq in rw["rewritten_queries"]:
                    st.markdown(f"- `{rq}`")
                st.markdown(f"**נימוק:** {rw['reasoning']}")
        if "plan" in msg and msg.get("route", {}).get("needs_retrieval", True):
            with st.expander("תוכנית שאילתה"):
                plan = msg["plan"]
                cols = st.columns(2)
                with cols[0]:
                    st.markdown(f"**שאילתה מורכבת:** `{plan['is_complex']}`")
                with cols[1]:
                    st.markdown(f"**נימוק:** {plan['reasoning']}")
                if plan["is_complex"]:
                    st.markdown("**תת-שאילתות:**")
                    for sq in plan["sub_queries"]:
                        st.markdown(f"- {sq}")
        if "sources" in msg and msg["sources"]:
            with st.expander("מקורות"):
                sources_html = " ".join(
                    f'<span class="source-badge">{s}</span>' for s in msg["sources"]
                )
                st.markdown(sources_html, unsafe_allow_html=True)

# Chat input
if prompt := st.chat_input("מצא מידע במאגר..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        # Build chat history for context
        chat_history = [
            {"role": m["role"], "content": m["content"]}
            for m in st.session_state.messages[:-1]
        ]

        with st.spinner("...מחפש"):
            result = run_rag_pipeline(
                prompt,
                chat_history=chat_history,
                session_id=st.session_state.session_id,
            )

        st.markdown(result["answer"])

        plan_dict = result["plan"].model_dump()
        route = result.get("route")
        route_dict = route.model_dump() if route else None
        rewritten = result.get("rewritten")
        rewritten_dict = rewritten.model_dump() if rewritten else None
        full_doc = result.get("full_doc_decision")
        full_doc_dict = full_doc.model_dump() if full_doc else None

        # Show route badge
        if route_dict:
            if route_dict["needs_retrieval"]:
                if full_doc_dict and full_doc_dict.get("needs_full_document"):
                    st.markdown(
                        '<span class="route-badge full-doc">📄 מסמך מלא</span>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        '<span class="route-badge retrieval">🔍 אחזור מסמכים</span>',
                        unsafe_allow_html=True,
                    )
            else:
                st.markdown(
                    '<span class="route-badge chitchat">💬 שיחה חופשית</span>',
                    unsafe_allow_html=True,
                )

        # Show full document info
        if full_doc_dict and full_doc_dict.get("needs_full_document"):
            with st.expander("📄 אחזור מסמך מלא"):
                st.markdown("**מסמכים שנאחזרו במלואם:**")
                for src in full_doc_dict["target_sources"]:
                    st.markdown(f"- `{src}`")
                st.markdown(f"**נימוק:** {full_doc_dict['reasoning']}")

        # Show rewritten query (only for partial retrieval queries)
        if rewritten_dict and route_dict and route_dict["needs_retrieval"] and not (full_doc_dict and full_doc_dict.get("needs_full_document")):
            with st.expander("שאילתה מותאמת לחיפוש"):
                st.markdown(f"**שאילתה מקורית:** {rewritten_dict['original_query']}")
                st.markdown("**שאילתות חיפוש:**")
                for rq in rewritten_dict["rewritten_queries"]:
                    st.markdown(f"- `{rq}`")
                st.markdown(f"**נימוק:** {rewritten_dict['reasoning']}")

        # Show query plan (only for retrieval queries)
        if route_dict and route_dict["needs_retrieval"]:
            with st.expander("תוכנית שאילתה"):
                cols = st.columns(2)
                with cols[0]:
                    st.markdown(f"**שאילתה מורכבת:** `{plan_dict['is_complex']}`")
                with cols[1]:
                    st.markdown(f"**נימוק:** {plan_dict['reasoning']}")
                if plan_dict["is_complex"]:
                    st.markdown("**תת-שאילתות:**")
                    for sq in plan_dict["sub_queries"]:
                        st.markdown(f"- {sq}")

        if result["sources"]:
            with st.expander("מקורות"):
                sources_html = " ".join(
                    f'<span class="source-badge">{s}</span>' for s in result["sources"]
                )
                st.markdown(sources_html, unsafe_allow_html=True)

    st.session_state.messages.append({
        "role": "assistant",
        "content": result["answer"],
        "plan": plan_dict,
        "route": route_dict,
        "rewritten": rewritten_dict,
        "full_doc": full_doc_dict,
        "sources": result["sources"],
    })
