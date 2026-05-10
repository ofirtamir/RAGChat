# RAGChat

A RAG (Retrieval-Augmented Generation) chatbot that answers questions based on your uploaded documents. Built with LangChain, ChromaDB, Google Gemini Flash, and Streamlit.

## Features

- **Document Upload**: Support for PDF, TXT, and Markdown files
- **Smart Query Planning**: Automatically decomposes complex queries into sub-queries for better retrieval
- **Vector Search**: ChromaDB-powered similarity search over document embeddings
- **Generative Answers**: Grounded answers using Google Gemini Flash with source citations
- **Chat UI**: Interactive Streamlit chat interface with document management sidebar

## Architecture

```
User Query → Planner → Retriever → LLM → Answer
                ↓
        Sub-queries (if complex)
                ↓
        Multiple retrievals → Synthesis
```

| Module | Description |
|--------|-------------|
| `config.py` | Central configuration and environment variables |
| `src/document_loader.py` | Load and parse PDF, TXT, Markdown files |
| `src/chunker.py` | Split documents into chunks using RecursiveCharacterTextSplitter |
| `src/embeddings.py` | Google embedding model wrapper |
| `src/vector_store.py` | ChromaDB vector store operations |
| `src/retriever.py` | Similarity search retriever |
| `src/planner.py` | Query planner that decomposes complex queries |
| `src/rag_chain.py` | Main RAG pipeline orchestration |
| `app.py` | Streamlit chat UI |

## Setup

### Prerequisites

- Python 3.11+
- Google API Key (from [Google AI Studio](https://aistudio.google.com/apikey))

### Installation

1. Clone the repository:
   ```bash
   git clone <repo-url>
   cd RAGChat
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Configure environment:
   ```bash
   cp .env.example .env
   ```
   Edit `.env` and add your Google API key.

4. Run the app:
   ```bash
   streamlit run app.py
   ```

## Usage

1. Open the app in your browser (Streamlit will show the URL)
2. Use the sidebar to upload PDF, TXT, or Markdown files
3. Click "Process Documents" to ingest them into the vector store
4. Ask questions in the chat input
5. View query plans and sources in the expandable sections below each answer

## Configuration

All settings can be adjusted in `config.py`:

| Setting | Default | Description |
|---------|---------|-------------|
| `LLM_MODEL` | `gemini-2.0-flash` | Gemini model for generation |
| `CHUNK_SIZE` | `1000` | Characters per chunk |
| `CHUNK_OVERLAP` | `200` | Overlap between chunks |
| `RETRIEVER_K` | `5` | Number of chunks to retrieve |
| `PLANNER_MAX_SUBQUERIES` | `4` | Max sub-queries for complex questions |
