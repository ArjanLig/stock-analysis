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
MAX_DECODE_ITERATIONS = 5


class UnsafePath(ValueError):
    """Het pad klimt uit de vault, is absoluut, of is geen notitie."""


def _decode_until_stable(text: str) -> str:
    """Decode URL-encoding repeatedly until stable or iteration limit reached.

    Defense against double-encoding attacks like ..%252Fx.md (where %25 decodes
    to %, creating ..%2Fx.md which then decodes to ../x.md). We decode iteratively
    to catch multi-level attacks, with a reasonable limit to prevent infinite loops.
    """
    previous = text
    for _ in range(MAX_DECODE_ITERATIONS):
        decoded = unquote(previous)
        if decoded == previous:
            # Stable: no more encoding detected
            return decoded
        previous = decoded
    # If we hit the iteration limit, return the last decode (safety fallback)
    return previous


def _check_segment(name: str, what: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise UnsafePath(f"{what} is leeg")
    if "/" in cleaned or "\\" in cleaned or cleaned in (".", ".."):
        raise UnsafePath(f"{what} mag geen padscheiding bevatten: {name!r}")
    return cleaned


def vault_prefix(user_id: str, vault: str | None = None) -> str:
    """De sleutelprefix van een gebruiker, of van een vault daarbinnen."""
    user = _decode_until_stable(user_id or "").strip()
    user = _check_segment(user, "user_id")
    if vault is None:
        return f"{user}/"
    vault_decoded = _decode_until_stable(vault or "").strip()
    vault_checked = _check_segment(vault_decoded, "vault")
    return f"{user}/{vault_checked}/"


def storage_key(user_id: str, vault: str, path: str) -> str:
    """Volledige objectsleutel voor een notitie. Werpt UnsafePath."""
    prefix = vault_prefix(user_id, vault)

    # Percent-encoding iteratively weghalen: "..%252Fx.md" (double-encoded)
    # decodes to "..%2Fx.md", which then decodes to "../x.md" and must be
    # rejected. We iterate until stable to catch multi-level encoding attacks.
    candidate = _decode_until_stable(path or "").strip()
    if not candidate:
        raise UnsafePath("pad is leeg")
    if candidate.startswith("/") or candidate.startswith("\\"):
        raise UnsafePath(f"pad moet relatief zijn: {path!r}")
    if not candidate.lower().endswith(NOTE_SUFFIX):
        raise UnsafePath(f"alleen {NOTE_SUFFIX}-notities: {path!r}")

    # Filter out empty segments and single dots (./x.md → x.md)
    parts = [p for p in candidate.replace("\\", "/").split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise UnsafePath(f"pad klimt uit de vault: {path!r}")

    return prefix + "/".join(parts)


def note_path(user_id: str, vault: str, key: str) -> str:
    """Sleutel terug naar het pad zoals de aanroeper het kent."""
    prefix = vault_prefix(user_id, vault)
    if not key.startswith(prefix):
        raise UnsafePath(f"sleutel hoort niet bij {vault}: {key!r}")
    return key[len(prefix):]
