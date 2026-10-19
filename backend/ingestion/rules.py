from backend.ingestion.common import ParseError, bullet, kv, split_sections

SECTIONS = {
    "required blocks": "required_block",
    "banned phrases": "banned_phrase",
    "mandatory text": "mandatory_text",
}


def parse_rules(text: str) -> list[dict]:
    _, sections = split_sections(text)
    errors: list[str] = []
    rules: list[dict] = []

    for sec in sections:
        rule_type = SECTIONS.get(sec.title.lower())
        if rule_type is None:
            errors.append(f"unknown section '## {sec.title}' (expected: {', '.join(SECTIONS)})")
            continue
        for line in sec.lines:
            b = bullet(line)
            if b is None:
                errors.append(f"{sec.title}: lines must be bullets, got '{line.strip()}'")
            elif rule_type == "required_block":
                p = kv(line)
                if not p or not p[1]:
                    errors.append(f"{sec.title}: use '- Name: text', got '{b}'")
                else:
                    rules.append({"rule_type": rule_type, "name": p[0], "value": p[1]})
            else:
                rules.append({"rule_type": rule_type, "name": None, "value": b})

    names = {r["name"].lower() for r in rules if r["rule_type"] == "required_block"}
    if "isi" not in names:
        errors.append("Required Blocks: an 'ISI' block is required")

    if errors:
        raise ParseError(errors)
    return rules
