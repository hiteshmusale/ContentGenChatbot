# Skill: Persona personalization
swimlane: personalize
workflow: personalize_email
label: Persona personalization

## When to use
The user wants an existing or approved email adapted for a different audience persona,
changing tone and emphasis while keeping the approved claims intact.

## Inputs
- source_asset (required): The email to adapt: "latest" for the most recent email from this chat, or an uploaded HTML file
- product (required): Product the email is about
- persona (required): The persona to adapt the email for
- notes (optional): Any extra guidance on tone or emphasis

## Plan wording
State the target persona, which claims will be emphasized (verbatim, never reworded),
and that tone and emphasis, not claim text, will change.

## Failure modes
- Persona not in the knowledge base: list the options.
