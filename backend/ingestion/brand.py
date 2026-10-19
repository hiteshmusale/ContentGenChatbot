import re

from backend.ingestion.common import ParseError, bullet, kv, split_sections

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def parse_brand(text: str) -> list[dict]:
    h1, sections = split_sections(text)
    errors: list[str] = []
    tokens: list[dict] = []
    by_title = {s.title.lower(): s for s in sections}

    name = h1[len("Brand:"):].strip() if h1 and h1.lower().startswith("brand:") else None
    if not name:
        errors.append("first line must be '# Brand: <name>'")
    else:
        tokens.append({"category": "brand", "key": "name", "value": name})

    def pairs(title: str) -> dict[str, str]:
        sec = by_title.get(title)
        if sec is None:
            errors.append(f"missing section '## {title.title()}'")
            return {}
        return {k.lower(): v for line in sec.lines if (p := kv(line)) for k, v in [p]}

    colors = pairs("colors")
    if "primary" not in colors:
        errors.append("Colors: 'primary' color is required")
    for key, val in colors.items():
        if not _HEX.match(val):
            errors.append(f"Colors: '{key}' must be a hex color like #0B5CAD, got '{val}'")
        tokens.append({"category": "color", "key": key, "value": val})

    fonts = pairs("fonts")
    for key in ("heading", "body"):
        if key not in fonts:
            errors.append(f"Fonts: '{key}' font is required")
    tokens += [{"category": "font", "key": k, "value": v} for k, v in fonts.items()]

    logo = pairs("logo")
    if "path" not in logo:
        errors.append("Logo: 'path' is required")
    tokens += [{"category": "logo", "key": k, "value": v} for k, v in logo.items()]

    tone = by_title.get("tone")
    if tone:
        notes = [b for line in tone.lines if (b := bullet(line))]
        tokens += [
            {"category": "tone", "key": f"note_{i}", "value": n} for i, n in enumerate(notes, 1)
        ]

    if errors:
        raise ParseError(errors)
    return tokens
