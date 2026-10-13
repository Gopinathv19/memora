"""The Google Drive connector.

Modules:
* oauth.py           -- authorization URL, state signing, token exchange/refresh, encryption
* drive_client.py    -- thin Drive API v3 client (list, get, download)
* file_discovery.py  -- list the account's PDFs
* rag_adapter.py     -- download a Drive PDF into the existing RAG ingestion
* service.py         -- connection management (save, find, refresh, disconnect)
* routes.py          -- /api/v1/gdrive/* endpoints

The DB models live in app/db/models/ (drive_connection, drive_import) so
Alembic autogenerate sees them like every other table.
"""