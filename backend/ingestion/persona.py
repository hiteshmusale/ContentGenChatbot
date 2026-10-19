from backend.ingestion.common import ParseError, bullet, kv, split_sections

PREFIX = "persona:"


def parse_personas(text: str) -> list[dict]:
    _, sections = split_sections(text)
    errors: list[str] = []
    personas: list[dict] = []

    if not sections:
        raise ParseError(["persona.md has no personas; add '## Persona: <name>' sections"])

    for sec in sections:
        if not sec.title.lower().startswith(PREFIX):
            errors.append(f"'{sec.title}': section titles must look like 'Persona: <name>'")
            continue
        name = sec.title[len(PREFIX):].strip()
        fields: dict[str, str] = {}
        concerns: list[str] = []
        in_concerns = False
        for line in sec.lines:
            b = bullet(line)
            if in_concerns and b is not None:
                concerns.append(b)
                continue
            p = kv(line)
            if p:
                key, val = p[0].lower(), p[1]
                in_concerns = key == "concerns"
                if not in_concerns:
                    fields[key] = val

        for key in ("description", "tone"):
            if not fields.get(key):
                errors.append(f"{name}: missing '{key}'")
        if fields.get("description") and fields.get("tone"):
            personas.append(
                {
                    "name": name,
                    "description": fields["description"],
                    "tone": fields["tone"],
                    "language": fields.get("language", "US English"),
                    "concerns": concerns,
                }
            )

    if errors:
        raise ParseError(errors)
    return personas
