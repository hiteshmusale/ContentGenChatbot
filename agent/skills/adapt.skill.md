# Skill: Mechanical adaptation
swimlane: adapt
workflow: adapt_email
label: Mechanical adaptation

## When to use
The user wants an existing email changed in a mechanical way with no creative rewrite:
swapping the logo, changing colors or fonts, replacing the ISI or footer block, fixing a
flagged phrase.

## Inputs
- source_asset (required): The email to edit: "latest" for the most recent email from this chat, or an uploaded HTML file
- changes (required): The exact mechanical changes to apply (e.g. swap logo, change primary color to #0B5CAD)

## Plan wording
List each change as a separate bullet. State that no copy will be rewritten.

## Failure modes
- User asks for a copy or messaging rewrite: that is persona personalization, not this lane.
