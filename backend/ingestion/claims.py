import re

from backend.ingestion.common import ParseError, kv, split_sections

REQUIRED = ("product", "text", "reference")
_ID = re.compile(r"^[A-Za-z][A-Za-z0-9]*-\d+$")


def parse_claims(text: str) -> list[dict]:
    _, sections = split_sections(text)
    errors: list[str] = []
    claims: list[dict] = []
    seen: set[str] = set()

    if not sections:
        raise ParseError(["claims.md has no claims; add '## CLM-001' style sections"])

    for sec in sections:
        cid = sec.title
        if not _ID.match(cid):
            errors.append(f"'{cid}': claim id must look like CLM-001")
        if cid in seen:
            errors.append(f"{cid}: duplicate claim id")
        seen.add(cid)

        fields = {k.lower(): v for line in sec.lines if (p := kv(line)) for k, v in [p]}
        for key in REQUIRED:
            if not fields.get(key):
                errors.append(f"{cid}: missing '{key}'")
        if all(fields.get(k) for k in REQUIRED):
            claims.append(
                {
                    "claim_id": cid,
                    "product": fields["product"],
                    "text": fields["text"],  # verbatim: only surrounding whitespace is trimmed
                    "reference": fields["reference"],
                }
            )

    if errors:
        raise ParseError(errors)
    return claims
