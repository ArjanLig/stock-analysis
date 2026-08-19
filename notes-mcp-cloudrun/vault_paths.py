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


def _fully_decoded(text: str, what: str) -> str:
    """De volledig gedecodeerde vorm, of UnsafePath als die er niet is.

    "..%252Fx.md" decodeert naar "..%2Fx.md" en daarna naar "../x.md"; alleen
    iteratief decoderen ziet zo'n aanval. De vorige versie gaf bij het bereiken
    van de limiet de half-gedecodeerde vorm terug en gebruikte die ook als
    sleutel -- dat viel fail-open: vanaf vijf lagen (..%252525252Fx.md) werd de
    invoer geaccepteerd met een gecodeerde padscheiding er nog in.

    Niet stabiel binnen de limiet betekent dat we niet weten waar de invoer
    naar wijst, en dan is weigeren het enige veilige antwoord.
    """
    current = text
    for _ in range(MAX_DECODE_ITERATIONS):
        decoded = unquote(current)
        if decoded == current:
            return decoded
        current = decoded
    raise UnsafePath(f"{what} blijft na {MAX_DECODE_ITERATIONS} decodeerslagen gecodeerd: {text!r}")


def _check_segment(name: str, what: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise UnsafePath(f"{what} is leeg")
    if "/" in cleaned or "\\" in cleaned or cleaned in (".", ".."):
        raise UnsafePath(f"{what} mag geen padscheiding bevatten: {name!r}")
    return cleaned


def _safe_segment(raw: str, what: str) -> str:
    """Keur af op de gedecodeerde vorm, maar geef de oorspronkelijke terug.

    De gedecodeerde vorm zegt waar de invoer naar wijst; de oorspronkelijke is
    hoe het object in de bucket heet. Decoderen en dan de decode gebruiken
    herschrijft legitieme namen ('Rendement 100%2B.md' werd '...100+.md') en
    maakt zulke notities onbereikbaar.
    """
    original = (raw or "").strip()
    _check_segment(_fully_decoded(original, what), what)
    return _check_segment(original, what)


def vault_prefix(user_id: str, vault: str | None = None) -> str:
    """De sleutelprefix van een gebruiker, of van een vault daarbinnen."""
    user = _safe_segment(user_id, "user_id")
    if vault is None:
        return f"{user}/"
    return f"{user}/{_safe_segment(vault, 'vault')}/"


def storage_key(user_id: str, vault: str | None, path: str) -> str:
    """Volledige objectsleutel voor een notitie. Werpt UnsafePath.

    Validatie gebeurt op de gedecodeerde vorm, de sleutel wordt gebouwd uit de
    oorspronkelijke invoer. Zie _fully_decoded en _safe_segment.
    """
    prefix = vault_prefix(user_id, vault)

    original = (path or "").strip()
    decoded = _fully_decoded(original, "pad")
    if not original or not decoded:
        raise UnsafePath("pad is leeg")
    for form in (original, decoded):
        if form.startswith("/") or form.startswith("\\"):
            raise UnsafePath(f"pad moet relatief zijn: {path!r}")
    if not decoded.lower().endswith(NOTE_SUFFIX):
        raise UnsafePath(f"alleen {NOTE_SUFFIX}-notities: {path!r}")

    # Alleen de gedecodeerde vorm laat traversal zien: '..%2Fx.md' is één
    # segment tot je het decodeert.
    if any(p == ".." for p in decoded.replace("\\", "/").split("/")):
        raise UnsafePath(f"pad klimt uit de vault: {path!r}")

    # Lege segmenten en losse punten weg (./x.md → x.md), op de invoer zelf.
    parts = [p for p in original.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts:
        raise UnsafePath("pad is leeg")

    return prefix + "/".join(parts)


def note_path(user_id: str, vault: str | None, key: str) -> str:
    """Sleutel terug naar het pad zoals de aanroeper het kent.

    `vault=None` staat voor een notitie los onder de gebruikersprefix."""
    prefix = vault_prefix(user_id, vault)
    if not key.startswith(prefix):
        raise UnsafePath(f"sleutel hoort niet bij {vault}: {key!r}")
    return key[len(prefix):]
