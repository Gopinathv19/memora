"""External source connectors.

A connector turns an outside service (Google Drive today, Dropbox or OneDrive
tomorrow) into inputs for the *existing* RAG pipeline. It never duplicates
parsing, chunking, embedding, graph or retrieval logic -- see
app/connectors/google_drive/rag_adapter.py for the one integration point.
"""