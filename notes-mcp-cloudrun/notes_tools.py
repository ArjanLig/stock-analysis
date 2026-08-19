"""De drie leestools, gebouwd op VaultStore.

Zoeken heeft geen index. Dat kan omdat de schaal het toelaat: 1,63 MB over
244 bestanden, dus alles ophalen kost per zoekopdracht een fractie van de
gratis egress. Een index zou een tweede kopie van de notities zijn, en
tweede kopieen van dezelfde waarheid hebben in dit project al drie keer een
bug opgeleverd.

Herzien zodra de gezamenlijke markdown boven ~10 MB komt of het aantal
bestanden boven ~1.000.
"""

from __future__ import annotations

from vault_paths import NOTE_SUFFIX, note_path, storage_key, vault_prefix
from vault_storage import NoteNotFound

CLAUDE_MD = "CLAUDE.md"


def _vault_of(user_id: str, key: str) -> str | None:
    prefix = vault_prefix(user_id)
    if not key.startswith(prefix):
        return None
    rest = key[len(prefix):]
    return rest.split("/", 1)[0] if "/" in rest else None


def _is_note(key: str) -> bool:
    return key.lower().endswith(NOTE_SUFFIX)


def snippet(text: str, query: str, radius: int = 120) -> str:
    """Het stukje tekst rond de treffer, zodat zichtbaar is waarom een
    notitie in de uitkomst staat."""
    lowered = text.lower()
    at = lowered.find((query or "").lower())
    if at < 0:
        head = text[: radius * 2].strip()
        return head + ("…" if len(text) > radius * 2 else "")
    start = max(0, at - radius)
    end = min(len(text), at + len(query) + radius)
    body = text[start:end].strip()
    return ("…" if start > 0 else "") + body + ("…" if end < len(text) else "")


def list_vaults(store, user_id: str) -> list[dict]:
    """Welke vaults er zijn, hoeveel notities erin staan, en of er een
    CLAUDE.md ligt met de schrijfregels van die vault."""
    keys = store.list_keys(vault_prefix(user_id))
    vaults: dict[str, dict] = {}
    for key in keys:
        vault = _vault_of(user_id, key)
        if not vault or not _is_note(key):
            continue
        entry = vaults.setdefault(vault, {"vault": vault, "notes": 0, "claude_md": None})
        entry["notes"] += 1
        if note_path(user_id, vault, key) == CLAUDE_MD:
            entry["claude_md"] = CLAUDE_MD
    return [vaults[v] for v in sorted(vaults)]


def read_note(store, user_id: str, vault: str, path: str) -> dict:
    """Eén notitie, met revisie. De revisie doet in fase 1 niets, maar staat
    er zodat het contract bij het toevoegen van schrijven niet verandert."""
    key = storage_key(user_id, vault, path)
    text, revision = store.get(key)
    return {"vault": vault, "path": path, "revision": revision, "content": text}


def search_notes(store, user_id: str, query: str, vault: str | None = None,
                 max_results: int = 20) -> list[dict]:
    """Zoek op inhoud. Zonder vault doorzoekt hij alles."""
    needle = (query or "").strip()
    if not needle:
        return []

    keys = [k for k in store.list_keys(vault_prefix(user_id, vault)) if _is_note(k)]
    contents = store.get_many(keys)

    hits = []
    for key in keys:
        text = contents.get(key)
        if text is None or needle.lower() not in text.lower():
            continue
        found_in = vault or _vault_of(user_id, key)
        if not found_in:
            continue
        hits.append({
            "vault": found_in,
            "path": note_path(user_id, found_in, key),
            "snippet": snippet(text, needle),
        })
        if len(hits) >= max_results:
            break
    return hits


__all__ = ["NoteNotFound", "list_vaults", "read_note", "search_notes", "snippet"]
