# PPT Layout Feedback Loop

## Why this exists

Generated PPT pages can look correct in HTML while still failing after conversion because of real slide constraints such as fixed height, line wrapping, footer pressure, and card density. When a user reports a visual defect, the fix should not stop at the one page. We should turn the incident into a reusable rule and upstream it through a pull request.

## Trigger conditions

Use this playbook when a user reports any of the following on a generated PPT page:

- text occlusion
- block overlap
- footer pressing into cards
- fixed-height intro boxes clipping wrapped titles
- three-column information pages becoming too dense after conversion

## Iteration workflow

1. Read the generated HTML page that failed.
2. Identify the page pattern, not just the local symptom.
   - Example: top intro banner + three cards + footer
3. Apply a conservative fix to the generated page first.
   - Prefer reducing density over squeezing more text in.
4. Extract the reusable layout rule from the fix.
5. Record the rule in docs and open a PR proactively.

## Stable rules learned from the 2026-03-28 incident

### Pattern
A page with:
- fixed header
- fixed-height intro box
- three-column card grid
- footer note

### What caused the defect
The intro box and card grid together consumed too much vertical space. After text wrapped during rendering and conversion, the effective content height exceeded the safe slide area and the footer visually pressed into the bottom of the cards.

### Reusable mitigation

- Use `min-height` instead of fixed `height` for intro/summary boxes when title wrapping is possible.
- For dense three-column information pages, use safer text sizes:
  - intro title: `26-28px`
  - intro body: `16px`
  - card title: `20-22px`
  - card list text: `16px`
- Reduce spacing before reducing readability:
  - card gap: around `20px` instead of `24px+`
  - card padding: around `18px` instead of `22px+`
- Keep the effective card area around `340-360px` height when the page also contains an intro box and a footer.
- Add a top border and top padding to the footer area so it is visually and spatially separated from the main content.
- Prefer shorter copy in three-column cards. If a card title exceeds two lines, shorten copy first, then reduce font size.

## Default response for future incidents

When the user says a PPT page is blocked/overlapped:

- fix the current generated HTML
- regenerate the PPTX
- validate the corrected file
- add or update a rule in this document
- create a PR unless the user explicitly says not to

## Rationale

This keeps the PPT generation process self-improving. A user-reported defect becomes repository knowledge, not just a repaired artifact.
