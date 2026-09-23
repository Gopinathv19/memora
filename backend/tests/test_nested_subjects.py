"""Nested subjects: a subject can be a folder inside another subject.

The tree is an adjacency list on `subjects.parent_subject_id`, so nesting is
unbounded. These tests cover the rules that make it a file store rather than
just a self-referential table: sibling name uniqueness, the ancestor path,
cycle-free moves, subtree deletion (rows *and* bytes), and the isolation rules
that already applied to flat subjects.
"""

import io


def _setup(client, name="Acme"):
    """A tenant, application and root subject, as the console user."""
    tenant_id = client.post("/api/v1/tenants", json={"name": name}).json()["id"]
    application_id = client.post(
        f"/api/v1/tenants/{tenant_id}/applications",
        json={"name": "App", "slug": "app"},
    ).json()["id"]
    root_id = client.post(
        f"/api/v1/applications/{application_id}/subjects",
        json={"external_id": "case-1"},
    ).json()["id"]
    return tenant_id, application_id, root_id


def _create_folder(client, application_id, external_id, parent_id=None):
    body = {"external_id": external_id}
    if parent_id is not None:
        body["parent_subject_id"] = parent_id
    return client.post(
        f"/api/v1/applications/{application_id}/subjects", json=body
    )


def test_folders_nest_to_any_depth(client):
    _, application_id, root_id = _setup(client)

    # case-1 / Contracts / 2026 / Q1 -- four levels, no depth limit.
    contracts = _create_folder(client, application_id, "Contracts", root_id)
    assert contracts.status_code == 201, contracts.text
    year = _create_folder(
        client, application_id, "2026", contracts.json()["id"]
    )
    assert year.status_code == 201, year.text
    quarter = _create_folder(client, application_id, "Q1", year.json()["id"])
    assert quarter.status_code == 201, quarter.text
    assert quarter.json()["parent_subject_id"] == year.json()["id"]

    # The ancestor path of the deepest folder is the whole chain, nearest
    # first, ending at the root.
    detail = client.get(f"/api/v1/subjects/{quarter.json()['id']}").json()
    assert [entry["external_id"] for entry in detail["path"]] == [
        "2026",
        "Contracts",
        "case-1",
    ]
    assert [entry["id"] for entry in detail["path"]] == [
        year.json()["id"],
        contracts.json()["id"],
        root_id,
    ]
    # A root's path is empty.
    assert client.get(f"/api/v1/subjects/{root_id}").json()["path"] == []


def test_sibling_names_must_be_unique_but_reused_across_parents(client):
    _, application_id, root_id = _setup(client)

    docs = _create_folder(client, application_id, "Docs", root_id)
    assert docs.status_code == 201
    # Same name, same parent: rejected.
    duplicate = _create_folder(client, application_id, "Docs", root_id)
    assert duplicate.status_code == 409
    # Same name, different parent: fine -- that is the point of folders.
    other_root = client.post(
        f"/api/v1/applications/{application_id}/subjects",
        json={"external_id": "case-2"},
    ).json()["id"]
    assert _create_folder(client, application_id, "Docs", other_root).status_code == 201
    # A child may also share its parent's name; only siblings collide.
    assert _create_folder(
        client, application_id, "Docs", docs.json()["id"]
    ).status_code == 201
    # Two roots may not share a name either.
    assert (
        client.post(
            f"/api/v1/applications/{application_id}/subjects",
            json={"external_id": "case-1"},
        ).status_code
        == 409
    )


def test_listing_by_parent_and_roots_only(client):
    _, application_id, root_id = _setup(client)
    contracts_id = _create_folder(
        client, application_id, "Contracts", root_id
    ).json()["id"]
    _create_folder(client, application_id, "Invoices", root_id)
    _create_folder(client, application_id, "2026", contracts_id)

    # Children of the root.
    children = client.get(
        "/api/v1/subjects", params={"parent_subject_id": root_id}
    ).json()
    assert sorted(s["external_id"] for s in children) == ["Contracts", "Invoices"]
    assert all(s["parent_subject_id"] == root_id for s in children)

    # Roots only: the two cases, not the folders inside them.
    roots = client.get(
        "/api/v1/subjects", params={"roots_only": "true"}
    ).json()
    assert sorted(s["external_id"] for s in roots) == ["case-1"]

    # No filter: everything, including nested folders.
    everything = client.get("/api/v1/subjects").json()
    assert len(everything) == 4

    # child_count is part of the listing, so a UI can badge folders.
    root_row = next(s for s in everything if s["id"] == root_id)
    assert root_row["child_count"] == 2


def test_rename_and_move(client):
    _, application_id, root_id = _setup(client)
    contracts_id = _create_folder(
        client, application_id, "Contracts", root_id
    ).json()["id"]
    invoices_id = _create_folder(client, application_id, "Invoices", root_id).json()[
        "id"
    ]

    # Rename: unique among the *new* siblings.
    renamed = client.patch(
        f"/api/v1/subjects/{contracts_id}", json={"external_id": "Agreements"}
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["external_id"] == "Agreements"

    # Renaming to a sibling's name collides.
    assert (
        client.patch(
            f"/api/v1/subjects/{contracts_id}", json={"external_id": "Invoices"}
        ).status_code
        == 409
    )

    # Move Contracts under Invoices.
    moved = client.patch(
        f"/api/v1/subjects/{contracts_id}",
        json={"parent_subject_id": invoices_id},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["parent_subject_id"] == invoices_id

    # Moving a folder inside its own subtree is a cycle: rejected.
    assert (
        client.patch(
            f"/api/v1/subjects/{invoices_id}",
            json={"parent_subject_id": contracts_id},
        ).status_code
        == 422
    )
    # So is being its own parent.
    assert (
        client.patch(
            f"/api/v1/subjects/{invoices_id}",
            json={"parent_subject_id": invoices_id},
        ).status_code
        == 422
    )

    # An explicit null parent moves the folder back to the root.
    to_root = client.patch(
        f"/api/v1/subjects/{contracts_id}", json={"parent_subject_id": None}
    )
    assert to_root.status_code == 200, to_root.text
    assert to_root.json()["parent_subject_id"] is None


def test_deleting_a_folder_removes_its_subtree_rows_and_bytes(client):
    _, application_id, root_id = _setup(client)
    contracts_id = _create_folder(
        client, application_id, "Contracts", root_id
    ).json()["id"]
    year_id = _create_folder(client, application_id, "2026", contracts_id).json()[
        "id"
    ]

    # A file in the nested folder, uploaded so Memora stores the bytes.
    payload = b"contract bytes"
    upload = client.post(
        f"/api/v1/subjects/{year_id}/sources/upload",
        files={"file": ("contract.pdf", io.BytesIO(payload), "application/pdf")},
    )
    assert upload.status_code == 201, upload.text
    source_id = upload.json()["id"]
    storage_uri = upload.json()["storage_uri"]

    # A file in the root, which must survive.
    keeper = client.post(
        f"/api/v1/subjects/{root_id}/sources",
        json={"type": "url", "storage_uri": "https://example.com/keep"},
    ).json()["id"]

    assert (
        client.delete(f"/api/v1/subjects/{contracts_id}").status_code == 204
    )
    # The whole subtree is gone, root to leaf.
    for subject_id in (contracts_id, year_id):
        assert client.get(f"/api/v1/subjects/{subject_id}").status_code == 404
    assert client.get(f"/api/v1/sources/{source_id}").status_code == 404
    # The stored bytes went with it; the root's file did not.
    from app.storage.local import LocalStorageBackend
    from app.core.config import get_settings
    from pathlib import Path

    backend = LocalStorageBackend(Path(get_settings().storage_dir))
    try:
        backend.open(storage_uri)
        raise AssertionError("stored bytes should have been deleted")
    except Exception:
        pass
    assert client.get(f"/api/v1/sources/{keeper}").status_code == 200


def test_sources_move_between_folders(client):
    _, application_id, root_id = _setup(client)
    contracts_id = _create_folder(
        client, application_id, "Contracts", root_id
    ).json()["id"]

    source_id = client.post(
        f"/api/v1/subjects/{root_id}/sources",
        json={"type": "file", "filename": "doc.pdf"},
    ).json()["id"]

    moved = client.post(
        f"/api/v1/sources/{source_id}/move",
        json={"target_subject_id": contracts_id},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["subject_id"] == contracts_id
    # The file now appears in the folder's listing, not the root's.
    assert [
        s["id"] for s in client.get(
            "/api/v1/sources", params={"subject_id": contracts_id}
        ).json()
    ] == [source_id]
    assert (
        client.get("/api/v1/sources", params={"subject_id": root_id}).json() == []
    )

    # A folder in another application is not a valid target.
    other_app = client.post(
        f"/api/v1/tenants/{client.get(f'/api/v1/subjects/{root_id}').json()['tenant_id']}/applications",
        json={"name": "Other", "slug": "other"},
    ).json()["id"]
    foreign_subject = client.post(
        f"/api/v1/applications/{other_app}/subjects",
        json={"external_id": "elsewhere"},
    ).json()["id"]
    assert (
        client.post(
            f"/api/v1/sources/{source_id}/move",
            json={"target_subject_id": foreign_subject},
        ).status_code
        == 422
    )


def test_cross_tenant_folder_access_is_404(client):
    """The isolation rules of flat subjects hold for folders too."""
    _, application_id, root_id = _setup(client, "Acme")

    # A second tenant with its own credential.
    globex_tenant = client.post("/api/v1/tenants", json={"name": "Globex"}).json()[
        "id"
    ]
    globex_app = client.post(
        f"/api/v1/tenants/{globex_tenant}/applications",
        json={"name": "GlobexApp", "slug": "globex-app"},
    ).json()["id"]
    token = client.post(
        f"/api/v1/applications/{globex_app}/credentials", json={"name": "k"}
    ).json()["token"]
    auth = {"Authorization": f"Bearer {token}"}

    # Creating a folder inside Acme's subject: 404, not 403.
    assert (
        client.post(
            f"/api/v1/applications/{application_id}/subjects",
            json={"external_id": "intruder", "parent_subject_id": root_id},
            headers=auth,
        ).status_code
        == 404
    )
    # Listing another tenant's folder children: 404.
    assert (
        client.get(
            "/api/v1/subjects",
            params={"parent_subject_id": root_id},
            headers=auth,
        ).status_code
        == 404
    )
    # Moving a source into a foreign subject: 404.
    source_id = client.post(
        f"/api/v1/subjects/{root_id}/sources",
        json={"type": "url", "storage_uri": "https://example.com/x"},
    ).json()["id"]
    globex_subject = client.post(
        f"/api/v1/applications/{globex_app}/subjects",
        json={"external_id": "g1"},
        headers=auth,
    ).json()["id"]
    assert (
        client.post(
            f"/api/v1/sources/{source_id}/move",
            json={"target_subject_id": globex_subject},
            headers=auth,
        ).status_code
        == 404
    )
    # Deleting a foreign folder: 404.
    assert (
        client.delete(f"/api/v1/subjects/{root_id}", headers=auth).status_code == 404
    )


def test_parent_must_be_in_the_same_application(client):
    tenant_id, application_id, root_id = _setup(client)
    other_app = client.post(
        f"/api/v1/tenants/{tenant_id}/applications",
        json={"name": "Other", "slug": "other"},
    ).json()["id"]
    foreign_root = client.post(
        f"/api/v1/applications/{other_app}/subjects",
        json={"external_id": "foreign"},
    ).json()["id"]

    response = _create_folder(
        client, application_id, "cross-app", foreign_root
    )
    assert response.status_code == 422
    assert "application" in response.json()["detail"]
