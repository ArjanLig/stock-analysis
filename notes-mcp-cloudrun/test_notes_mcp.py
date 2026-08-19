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
