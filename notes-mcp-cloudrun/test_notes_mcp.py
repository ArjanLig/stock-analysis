"""Tests voor de notities-MCP. Draaien zonder Supabase: de opslagclient
wordt geïnjecteerd, net zoals compute_screener(universe, fetch) dat doet."""

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO_ROOT))

USER = "02d70077-9c3f-4494-a37d-0fa3e8f68f98"


# ── vault_paths ────────────────────────────────────────────────────────────

def test_storage_key_prefixes_the_user():
    from vault_paths import storage_key
    assert storage_key(USER, "portfolio-vault", "Tickers/DECK.md") == (
        f"{USER}/portfolio-vault/Tickers/DECK.md")


def test_parent_traversal_is_refused():
    """Een pad dat uit de vault klimt komt bij de notities van iemand anders
    uit. Dit is de les uit het load_credential-lek: de user-id hoort niet
    beinvloedbaar te zijn door wat de aanroeper meestuurt."""
    from vault_paths import UnsafePath, storage_key
    for bad in ("../other/x.md", "Tickers/../../x.md", "..%2Fx.md"):
        with pytest.raises(UnsafePath):
            storage_key(USER, "portfolio-vault", bad)


def test_absolute_paths_are_refused():
    from vault_paths import UnsafePath, storage_key
    with pytest.raises(UnsafePath):
        storage_key(USER, "portfolio-vault", "/etc/passwd.md")


def test_only_markdown_is_addressable():
    """De tools werken op notities. Bijlagen synchroniseren wel mee, maar
    zijn geen tekst die je kunt lezen of doorzoeken."""
    from vault_paths import UnsafePath, storage_key
    with pytest.raises(UnsafePath):
        storage_key(USER, "portfolio-vault", "Attachments/plaatje.png")


def test_a_vault_name_cannot_contain_a_separator():
    from vault_paths import UnsafePath, storage_key
    with pytest.raises(UnsafePath):
        storage_key(USER, "../andere-user", "x.md")


def test_vault_prefix_without_vault_is_the_user_root():
    from vault_paths import vault_prefix
    assert vault_prefix(USER) == f"{USER}/"
    assert vault_prefix(USER, "lazytheta-vault") == f"{USER}/lazytheta-vault/"


def test_note_path_is_the_inverse_of_storage_key():
    from vault_paths import note_path, storage_key
    key = storage_key(USER, "lazytheta-vault", "06 Open Issues.md")
    assert note_path(USER, "lazytheta-vault", key) == "06 Open Issues.md"


def test_double_url_encoding_is_refused():
    """Double-encoding (e.g., %25 → %) can bypass single-pass decoding.
    A path that looks safe after one decode might be unsafe after two."""
    from vault_paths import UnsafePath, storage_key
    with pytest.raises(UnsafePath):
        storage_key(USER, "portfolio-vault", "..%252Fx.md")


def test_vault_name_is_decoded_and_validated():
    """The vault name must also be decoded and checked for traversal.
    ..%2Fandere-user decodes to ../andere-user and should be refused."""
    from vault_paths import UnsafePath, storage_key
    with pytest.raises(UnsafePath):
        storage_key(USER, "..%2Fandere-user", "x.md")


def test_single_dot_segments_are_filtered():
    """A path like ./x.md should be equivalent to x.md.
    Both refer to the same note; single-dot segments should be normalized away."""
    from vault_paths import storage_key
    path_with_dot = storage_key(USER, "vault", "./x.md")
    path_without_dot = storage_key(USER, "vault", "x.md")
    assert path_with_dot == path_without_dot


# ── vault_storage ──────────────────────────────────────────────────────────

class FakeS3:
    """Genoeg van de boto3-client om VaultStore te testen. Elke sleutel
    bevat (inhoud, etag); een sleutel in `broken` gooit een transportfout."""

    def __init__(self, objects=None, broken=()):
        self.objects = objects or {}
        self.broken = set(broken)
        self.calls = []

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        outer = self

        class _Pager:
            def paginate(self, Bucket, Prefix):
                if "LIST" in outer.broken:
                    raise OSError("bucket unreachable")
                keys = sorted(k for k in outer.objects if k.startswith(Prefix))
                # twee pagina's, zodat paginering echt getest wordt
                half = max(1, len(keys) // 2)
                for chunk in (keys[:half], keys[half:]):
                    yield {"Contents": [{"Key": k} for k in chunk]} if chunk else {}

        return _Pager()

    def get_object(self, Bucket, Key):
        self.calls.append(Key)
        if Key in self.broken:
            raise OSError("connection reset")
        if Key not in self.objects:
            from botocore.exceptions import ClientError
            raise ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "not found"}}, "GetObject")
        body, etag = self.objects[Key]

        class _Body:
            def read(self_inner):
                return body.encode("utf-8")

        return {"Body": _Body(), "ETag": etag}


def test_list_keys_walks_every_page():
    """Eén pagina lezen en stoppen laat notities onzichtbaar achter -- het
    soort stille verlies waar de floor-guard in refresh_universe voor is."""
    from vault_storage import VaultStore
    objects = {f"{USER}/v/n{i}.md": (f"tekst {i}", f'"e{i}"') for i in range(9)}
    store = VaultStore(FakeS3(objects), "vaults")
    assert len(store.list_keys(f"{USER}/v/")) == 9


def test_get_returns_text_and_revision():
    from vault_storage import VaultStore
    store = VaultStore(FakeS3({f"{USER}/v/a.md": ("hallo", '"abc123"')}), "vaults")
    text, revision = store.get(f"{USER}/v/a.md")
    assert text == "hallo"
    assert revision == "abc123"          # zonder de aanhalingstekens van S3


def test_a_missing_note_is_not_an_empty_note():
    from vault_storage import NoteNotFound, VaultStore
    store = VaultStore(FakeS3({}), "vaults")
    with pytest.raises(NoteNotFound):
        store.get(f"{USER}/v/weg.md")


def test_a_transport_failure_never_looks_like_data():
    """Een lege lijst teruggeven terwijl de bucket plat ligt is een onware
    uitspraak over de vault. Zelfde regel als EdgarFetchError."""
    from vault_storage import StorageUnavailable, VaultStore
    store = VaultStore(FakeS3({}, broken=["LIST"]), "vaults")
    with pytest.raises(StorageUnavailable):
        store.list_keys(f"{USER}/")


def test_get_raises_when_the_connection_breaks():
    from vault_storage import StorageUnavailable, VaultStore
    store = VaultStore(FakeS3({f"{USER}/v/a.md": ("x", '"e"')}, broken=[f"{USER}/v/a.md"]),
                       "vaults")
    with pytest.raises(StorageUnavailable):
        store.get(f"{USER}/v/a.md")


def test_get_many_returns_every_requested_note():
    from vault_storage import VaultStore
    objects = {f"{USER}/v/n{i}.md": (f"tekst {i}", f'"e{i}"') for i in range(20)}
    store = VaultStore(FakeS3(objects), "vaults")
    got = store.get_many(list(objects))
    assert len(got) == 20
    assert got[f"{USER}/v/n7.md"] == "tekst 7"
