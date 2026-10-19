"""Turns the model's proposed edit operations into validated, executable ones. The model
only proposes; anything that doesn't check out against the knowledge base or the
workspace's files is reported back to the user instead of being guessed."""
from workflows.adapt import norm_hex


def validate_ops(
    raw: list[dict], brand_colors: dict[str, str], logos: list[str], brand_logo: str | None
) -> tuple[list[dict], list[str]]:
    ops: list[dict] = []
    problems: list[str] = []

    def color(value: str | None, label: str) -> str | None:
        v = (value or "").strip()
        resolved = norm_hex(brand_colors.get(v.lower())) if v.lower() in brand_colors else norm_hex(v)
        if resolved is None:
            roles = ", ".join(f"{k} ({norm_hex(c)})" for k, c in brand_colors.items())
            problems.append(f"I need a hex color for {label} (got '{v}'). Brand colors: {roles}.")
        return resolved

    for r in raw:
        t = r.get("type")
        if t == "swap_logo":
            want = (r.get("logo") or "").strip()
            if want.lower() in ("", "brand", "brand logo", "default"):
                want = brand_logo or ""
            match = next((l for l in logos if l.lower() == want.lower()), None)
            if match:
                op = {"type": "swap_logo", "logo": match}
                if r.get("target_ordinal"):  # "the 2nd logo"
                    op["target"] = {"type": "logo", "ordinal": r["target_ordinal"]}
                ops.append(op)
            else:
                avail = ", ".join(logos) or "none uploaded yet"
                problems.append(f"Logo '{want or r.get('logo')}' isn't uploaded. Available logos: {avail}.")
        elif t == "change_color":
            old, new = color(r.get("color"), "the color to replace"), color(r.get("to_color"), "the new color")
            if old and new:
                ops.append({"type": "change_color", "color": old, "to_color": new})
        elif t in ("replace_section_text", "recolor_section"):
            tt, n = r.get("target_type"), r.get("target_ordinal")
            label = f"{tt} #{n}" if n else f"the {tt}"
            if not tt:
                problems.append("Which section should I change (header, subheader, body, CTA, footer ...)?")
            elif t == "replace_section_text":
                text = (r.get("text") or "").strip()
                if text:
                    ops.append({"type": t, "target": {"type": tt, "ordinal": n}, "text": text})
                else:
                    problems.append(f"What should the new text of {label} be?")
            else:
                prop, new = r.get("property"), color(r.get("to_color"), "the new color")
                if prop not in ("background", "text"):
                    problems.append(f"Do you want the background color or the text color of {label} changed?")
                elif new:
                    ops.append({"type": t, "target": {"type": tt, "ordinal": n}, "property": prop, "to_color": new})
        elif t == "replace_isi":
            ops.append({"type": "replace_isi"})
        elif t == "replace_text":
            if (r.get("find") or "").strip():
                ops.append({"type": "replace_text", "find": r["find"], "replace": r.get("replace") or ""})
            else:
                problems.append("I need the exact text to find for a text replacement.")
        else:
            problems.append(f"Unsupported change type '{t}'.")
    return ops, problems
