"""Which imported Google Drive files changed in Drive? (plan Step 7, detection only)

`drive_imports` records each imported file's `modified_time`. Comparing it with the file's current Drive
`modified_time` says which imports are stale, and which files are gone. The result is a list of changes.
This module does not call Drive, import anything or delete anything.

Acting on a change is a product decision, so it is left to the caller. What the existing import does is worth
knowing before deciding: `rag_adapter.import_pdf(..., reimport=True)` creates a **new** source and re-points the
`drive_imports` row at it; the old source row is **not** removed. After a re-import the subject therefore holds
both versions, for vector search as much as for the graph. Once the new source is extracted and chunked, the
index job (`index_job.py`) builds its graph automatically, and removing the superseded source (the existing
source delete) makes the graph's delete hook clean up everything of the old one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class DriveChange:
    drive_file_id: str
    file_name: str
    source_id: str
    reason: str                      # "modified" | "removed"
    imported_modified_time: str
    drive_modified_time: str | None


def _parse(value: str | None) -> datetime | None:
    """RFC 3339 as Drive writes it ("2026-10-17T09:30:00.000Z")."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def detect_changes(imports: list[dict], listing: list[dict]) -> list[DriveChange]:
    """`imports`: dicts with drive_file_id, source_id, file_name, modified_time (what was imported).
    `listing`: dicts with id and modified_time (Drive now). A file Drive no longer lists is `removed`; a file
    whose Drive modification time is later than the imported one is `modified`. Unparseable times are never
    reported as a change (no evidence), and files in Drive that were never imported are not this module's concern."""
    current = {f["id"]: f for f in listing}
    out: list[DriveChange] = []
    for imp in sorted(imports, key=lambda i: i["drive_file_id"]):
        file = current.get(imp["drive_file_id"])
        if file is None:
            out.append(DriveChange(imp["drive_file_id"], imp["file_name"], str(imp["source_id"]), "removed",
                                   imp["modified_time"], None))
            continue
        then, now = _parse(imp["modified_time"]), _parse(file.get("modified_time"))
        if then is not None and now is not None and now > then:
            out.append(DriveChange(imp["drive_file_id"], imp["file_name"], str(imp["source_id"]), "modified",
                                   imp["modified_time"], file["modified_time"]))
    return out


def load_imports(db, connection_id) -> list[dict]:
    """The connection's imports as plain dicts (read-only)."""
    from sqlalchemy import select

    from app.db.models.drive_import import DriveImport

    rows = db.execute(select(DriveImport).where(DriveImport.connection_id == connection_id)).scalars()
    return [{"drive_file_id": r.drive_file_id, "source_id": r.source_id, "file_name": r.file_name,
             "modified_time": r.modified_time} for r in rows]
