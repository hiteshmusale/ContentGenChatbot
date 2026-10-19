from backend.ingestion.brand import parse_brand
from backend.ingestion.claims import parse_claims
from backend.ingestion.common import ParseError
from backend.ingestion.persona import parse_personas
from backend.ingestion.rules import parse_rules
from backend.models import BrandToken, Claim, Persona, Rule

# doc_type -> (parser, model the parsed rows are stored in)
DOC_TYPES = {
    "brand": (parse_brand, BrandToken),
    "persona": (parse_personas, Persona),
    "claims": (parse_claims, Claim),
    "rules": (parse_rules, Rule),
}

__all__ = ["DOC_TYPES", "ParseError"]
