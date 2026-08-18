"""Turn a pre-scan section into the parts a summary card needs.

The Moat and Risk prompts emit a fixed shape: a bold verdict line, one
sentence, three labelled bullets, and one closing line. This reads it back so
the page can render a card instead of a wall of markdown.

Returns None whenever the text is not in that shape — sixty-odd tickers still
hold output from the older, longer templates, and half-parsing one into a card
would look like a rendering bug. The caller falls back to plain markdown.
"""

import re

# **Moat: Wide 🛡️ · Stable ➡️ · 4/5**  — the score is optional because Risk is
# a three-level rating with no 0-5 scale, and inventing one would put false
# precision on the card.
_VERDICT = re.compile(r"^\*\*([^:*]+):\s*(.+?)\*\*\s*$", re.M)
_SCORE = re.compile(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)")
_BULLET = re.compile(r"^[-*]\s+\*\*(.+?)\*\*\s*:?\s*(.*)$")
_FOOTER = re.compile(r"^\*\*(.+?):?\*\*:?\s*(.+)$")

_STRIP_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF←-⇿☀-➿️]+"
)


def _clean(text):
    return _STRIP_EMOJI.sub("", text or "").replace("·", " ").strip(" ·:-")


def gauge_fraction(score, out_of):
    """How much of the arc to fill, clamped to the dial."""
    if not out_of:
        return 0.0
    return max(0.0, min(1.0, score / out_of))


def parse_verdict_section(content):
    """Parse a verdict-shaped section, or None if it is not one."""
    if not content or not content.strip():
        return None

    match = _VERDICT.search(content)
    if not match or match.start() > 400:
        # The verdict opens the section. Finding one deep in the body means
        # this is an older report that happens to bold a heading.
        return None

    head, rest = match.group(2), content[match.end():]

    score = out_of = None
    score_match = _SCORE.search(head)
    if score_match:
        score, out_of = float(score_match.group(1)), float(score_match.group(2))
        head = head[:score_match.start()] + head[score_match.end():]

    parts = [_clean(p) for p in head.split("·")]
    parts = [p for p in parts if p]
    label = parts[0] if parts else ""
    qualifiers = parts[1:]

    summary_lines, bullets, footer_label, footer_text = [], [], "", ""
    for raw in rest.split("\n"):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            break                      # Sources and anything after it
        bullet = _BULLET.match(line)
        if bullet:
            bullets.append({"label": bullet.group(1).strip(),
                            "text": bullet.group(2).strip()})
            continue
        if bullets:
            # Prose after the bullets is the closing line, whether or not it
            # kept its bold label.
            foot = _FOOTER.match(line)
            if foot:
                footer_label, footer_text = foot.group(1).strip(), foot.group(2).strip()
            else:
                footer_text = (footer_text + " " + line).strip()
            continue
        summary_lines.append(line)

    if not bullets:
        # A card with an empty body is worse than the text it replaced.
        return None

    return {
        "label": label,
        "qualifiers": qualifiers,
        "score": score,
        "out_of": out_of,
        "summary": " ".join(summary_lines).strip(),
        "bullets": bullets,
        "footer_label": footer_label,
        "footer_text": footer_text,
    }


# One vocabulary in, one widget out. Robustness stores robust/mid/fragile, the
# scorecard green/yellow/red, and the overall verdict says "borderline" — all
# three are the same three-point scale wearing different words.
_DEFAULT_LABELS = ("Weak", "Mixed", "Strong")
_GLYPHS = ("&#10005;", "&#8211;", "&#10003;")          # ✕  –  ✓
_TONES = ("#c0603f", "#c79a3a", "#2f8f4e")
_TINTS = ("#f7dfd7", "#fbedd2", "#d9e7dd")


# Ordered worst to best. Risk is the one scale that runs the other way in
# plain English — "high" is the bad end — so it is mapped by meaning rather
# than by the word's position.
_BAND_INDEX = {
    # stored band names
    "fragile": 0, "red": 0, "mid": 1, "yellow": 1, "borderline": 1,
    "robust": 2, "green": 2,
    # moat
    "none": 0, "narrow": 1, "wide": 2,
    # business clarity
    "opaque": 0, "understandable": 1, "simple": 2,
    # growth runway
    "short": 0, "moderate": 1, "long": 2,
    # metrics / general strength
    "weak": 0, "poor": 0, "mixed": 1, "strong": 2,
    # market sentiment
    "bearish": 0, "neutral": 1, "bullish": 2,
    # AI exposure
    "exposed": 0, "resilient": 1, "anti-fragile": 2, "antifragile": 2,
    # verdict
    "pass": 0, "revisit": 1, "deep dive": 2, "deep_dive": 2,
    # risk — inverted on purpose
    "high": 0, "medium": 1, "low": 2,
}

# Business phases are stages, not grades: phase 5 is not better than phase 3.
# Colouring them would turn a description into a compliment, so they resolve to
# no colour at all.
_NOT_A_JUDGEMENT = frozenset({
    "loss-making", "loss making", "growth", "margin expansion",
    "profitable growth", "capital return", "decline",
})


def band_tone(label):
    """The colour a verdict label earns, or None when it is not a judgement."""
    key = str(label or "").strip().lower()
    if not key or key in _NOT_A_JUDGEMENT:
        return None
    idx = _BAND_INDEX.get(key)
    return _TONES[idx] if idx is not None else None

def three_state_html(band, labels=None, size=44, theme=None):
    """A weak / mixed / strong selector with one state lit.

    An unrecognised band lights nothing. Three grey circles read as "not
    assessed", which is what it is — confidently lighting the middle one would
    turn a missing rating into a middling one.
    """
    from html import escape

    labels = labels or _DEFAULT_LABELS
    theme = theme or {}
    muted = theme.get("text_muted", "#9a958c")
    text = theme.get("text", "#2b2b2f")
    active = _BAND_INDEX.get(str(band or "").strip().lower())

    circles = []
    for i in range(3):
        on = (i == active)
        fill = _TONES[i] if on else _TINTS[i]
        glyph = "#fff" if on else _TONES[i]
        circles.append(
            f'<div data-active="{1 if on else 0}" style="text-align:center;'
            f'min-width:{size + 26}px">'
            f'<div style="width:{size}px;height:{size}px;border-radius:50%;'
            f'margin:0 auto;background:{fill};'
            f'{"" if on else f"border:1.5px solid {_TONES[i]}66;"}'
            f'display:flex;align-items:center;justify-content:center;'
            f'color:{glyph};font-size:{int(size * 0.42)}px;line-height:1;'
            f'{"box-shadow:0 2px 10px " + _TONES[i] + "40;" if on else "opacity:0.55;"}">'
            f'{_GLYPHS[i]}</div>'
            f'<div style="margin-top:7px;font-size:0.68rem;letter-spacing:0.07em;'
            f'font-weight:{700 if on else 600};'
            f'color:{text if on else muted};text-transform:uppercase">'
            f'{escape(str(labels[i]))}</div></div>'
        )
    return ('<div style="display:flex;justify-content:center;gap:14px;'
            'align-items:flex-start">' + "".join(circles) + "</div>")
