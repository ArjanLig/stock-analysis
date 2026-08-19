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


def _is_note(key: str) -> bool:
    return key.lower().endswith(NOTE_SUFFIX)


def _is_unfiled(user_id: str, key: str) -> bool:
    """Check if a note is directly under user prefix (no vault subdirectory)."""
    prefix = vault_prefix(user_id)
    if not key.startswith(prefix):
        return False
    rest = key[len(prefix):]
    return "/" not in rest


def _vault_of(user_id: str, key: str) -> str | None:
    """Extract vault name from key. Returns None for notes not in a vault,
    or for unfiled notes (use _is_unfiled to detect those separately)."""
    prefix = vault_prefix(user_id)
    if not key.startswith(prefix):
        return None
    rest = key[len(prefix):]
    # Only return vault name if there's a slash (real vault structure)
    if "/" in rest:
        return rest.split("/", 1)[0]
    return None


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
    CLAUDE.md ligt met de schrijfregels van die vault.

    Staan er notities buiten elke vaultmap -- los onder de gebruikersprefix --
    dan volgt als laatste een vermelding met `vault: null`. Meestal betekent
    dat een verkeerd ingestelde remote prefix in de synchronisatieplugin.

    `null` en geen sentinel-string, omdat een vaultnaam een padsegment is en
    `null` daar per definitie niet mee kan botsen. De vorige sentinel
    "(unfiled)" kon dat wel: bestond er een échte vault met die naam, dan wees
    een treffer of leesverzoek naar de verkeerde notitie. read_note(vault=None)
    leest zo'n losse notitie gewoon."""
    keys = store.list_keys(vault_prefix(user_id))
    vaults: dict[str, dict] = {}
    unfiled_count = 0

    for key in keys:
        # Vaults met bijlagen moeten ook verschijnen (met notes: 0)
        prefix = vault_prefix(user_id)
        if key.startswith(prefix):
            rest = key[len(prefix):]
            if "/" in rest:
                vault_name = rest.split("/", 1)[0]
                vaults.setdefault(vault_name, {"vault": vault_name, "notes": 0, "claude_md": None})

        # Tel notities (apart voor losse notities)
        if _is_note(key):
            if _is_unfiled(user_id, key):
                unfiled_count += 1
            else:
                vault = _vault_of(user_id, key)
                if vault:
                    entry = vaults.setdefault(vault, {"vault": vault, "notes": 0, "claude_md": None})
                    entry["notes"] += 1
                    if note_path(user_id, vault, key) == CLAUDE_MD:
                        entry["claude_md"] = CLAUDE_MD

    result = [vaults[v] for v in sorted(vaults)]
    if unfiled_count > 0:
        result.append({"vault": None, "notes": unfiled_count, "claude_md": None})
    return result


def read_note(store, user_id: str, vault: str | None, path: str) -> dict:
    """Eén notitie, met revisie. De revisie doet in fase 1 niets, maar staat
    er zodat het contract bij het toevoegen van schrijven niet verandert.

    `vault=None` leest een notitie die los onder de gebruikersprefix staat --
    precies wat list_vaults en search_notes met `vault: null` aanduiden."""
    key = storage_key(user_id, vault, path)
    text, revision = store.get(key)
    return {"vault": vault, "path": path, "revision": revision, "content": text}


def search_notes(store, user_id: str, query: str, vault: str | None = None,
                 max_results: int = 20) -> dict:
    """Zoek op inhoud. Zonder vault doorzoekt hij alles, inclusief losse notities.

    Geeft een omhullende dict terug:

        {"hits": [...], "returned": n, "total_matches": N, "truncated": bool}

    en niet, zoals eerder, een kale lijst treffers. Een kale lijst kan niet
    zeggen dat hij is afgekapt: met 50 treffers en max_results=20 kwamen er 20
    terug, altijd het alfabetische begin, zonder markering. Op "heb ik ooit
    over X geschreven?" is dat een onvolledig antwoord dat niet van een
    volledig antwoord te onderscheiden is -- dezelfde soort stille onwaarheid
    als een lege lijst bij een onbereikbare bucket.

    Waarom een dict en geen extra laatste element in de lijst: een lijst waarin
    het laatste element geen treffer is, is een lijst waarover len() liegt en
    waar elke gewone iteratie overheen struikelt. De dict maakt de drie feiten
    (deze treffers, zoveel gevonden, wel/niet afgekapt) los benoembaar, en de
    MCP-laag geeft hem ongewijzigd door als JSON.

    Daarom telt hij ook dóór na de limiet: total_matches is het aantal notities
    dat de zoekterm bevat, niet het aantal dat je terugkrijgt.
    """
    needle = (query or "").strip()
    empty = {"hits": [], "returned": 0, "total_matches": 0, "truncated": False}
    if not needle:
        return empty

    keys = [k for k in store.list_keys(vault_prefix(user_id, vault)) if _is_note(k)]
    contents = store.get_many(keys)

    hits: list[dict] = []
    total = 0
    limit = max(0, int(max_results))
    for key in keys:
        text = contents.get(key)
        if text is None or needle.lower() not in text.lower():
            continue

        # In welke vault ligt deze notitie? None betekent: los onder de
        # gebruikersprefix. Geen sentinel-string, zodat de treffer niet kan
        # botsen met een échte vault die toevallig zo heet -- read_note op de
        # treffer levert altijd dezelfde notitie op als waar hij vandaan komt.
        if vault:
            found_in = vault
        elif _is_unfiled(user_id, key):
            found_in = None
        else:
            found_in = _vault_of(user_id, key)
            if found_in is None:
                continue          # sleutel hoort niet bij deze gebruiker

        total += 1
        # Doortellen na de limiet, maar geen snippets meer bouwen.
        if len(hits) < limit:
            hits.append({
                "vault": found_in,
                "path": note_path(user_id, found_in, key),
                "snippet": snippet(text, needle),
            })

    return {"hits": hits, "returned": len(hits), "total_matches": total,
            "truncated": total > len(hits)}


__all__ = ["NoteNotFound", "list_vaults", "read_note", "search_notes", "snippet"]
