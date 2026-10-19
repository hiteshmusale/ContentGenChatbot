"""Pure helpers for the generate lane: the numeric-invention check on model-written copy."""
import re

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?%?")


def numbers(text: str) -> set[str]:
    return {m.group(0).replace(",", "").rstrip(".") for m in _NUM.finditer(text)}


def invented_numbers(copy_texts: list[str], allowed_sources: list[str]) -> list[str]:
    """Numbers in model-written copy that appear in neither an approved claim nor the
    user's own inputs. A statistic the model made up cannot be caught by comparing
    against the claims list, so any unsupported number is treated as invented."""
    allowed: set[str] = set()
    for src in allowed_sources:
        allowed |= numbers(src)
    return sorted(numbers(" ".join(copy_texts)) - allowed)
