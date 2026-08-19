"""Objectsleutels bouwen voor de vault, en paden weigeren die dat niet mogen.

De user-id wordt hier voorgezet en is voor de aanroeper onbereikbaar. Dat is
geen stijlkeuze: deze server draait met een sleutel die alle buckettoegang
heeft, dus "de opslag regelt het wel" is een aanname en geen bescherming --
dezelfde fout die load_credential maakte toen het zonder user_id draaide.

Geen I/O, zodat de gevaarlijkste code in dit project zonder netwerk te
testen is.
"""

from urllib.parse import unquote

NOTE_SUFFIX = ".md"


class UnsafePath(ValueError):
    """Het pad klimt uit de vault, is absoluut, of is geen notitie."""


def _check_segment(name: str, what: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise UnsafePath(f"{what} is leeg")
    if "/" in cleaned or "\\" in cleaned or cleaned in (".", ".."):
        raise UnsafePath(f"{what} mag geen padscheiding bevatten: {name!r}")
    return cleaned


def vault_prefix(user_id: str, vault: str | None = None) -> str:
    """De sleutelprefix van een gebruiker, of van een vault daarbinnen."""
    user = _check_segment(user_id, "user_id")
    if vault is None:
        return f"{user}/"
    return f"{user}/{_check_segment(vault, 'vault')}/"


def storage_key(user_id: str, vault: str, path: str) -> str:
    """Volledige objectsleutel voor een notitie. Werpt UnsafePath."""
    prefix = vault_prefix(user_id, vault)

    # Percent-encoding eerst weghalen: "..%2Fx.md" is hetzelfde pad als
    # "../x.md" en moet dus dezelfde weigering krijgen.
    candidate = unquote(path or "").strip()
    if not candidate:
        raise UnsafePath("pad is leeg")
    if candidate.startswith("/") or candidate.startswith("\\"):
        raise UnsafePath(f"pad moet relatief zijn: {path!r}")
    if not candidate.lower().endswith(NOTE_SUFFIX):
        raise UnsafePath(f"alleen {NOTE_SUFFIX}-notities: {path!r}")

    parts = [p for p in candidate.replace("\\", "/").split("/") if p != ""]
    if any(p == ".." for p in parts):
        raise UnsafePath(f"pad klimt uit de vault: {path!r}")

    return prefix + "/".join(parts)


def note_path(user_id: str, vault: str, key: str) -> str:
    """Sleutel terug naar het pad zoals de aanroeper het kent."""
    prefix = vault_prefix(user_id, vault)
    if not key.startswith(prefix):
        raise UnsafePath(f"sleutel hoort niet bij {vault}: {key!r}")
    return key[len(prefix):]
