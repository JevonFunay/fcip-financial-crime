# FCIP frontend design system

Updated 13 November 2026 using the `ui-ux-pro-max` skill. Jevon asks for a
cleaner interface with fewer ornaments and unnecessary explanations. This
supersedes the navy-sidebar presentation from 12 November (AS-21 → AS-22).

## Direction

Use a light, functional workspace: white navigation, neutral canvas, flat
panels and teal for actions and selected states. The skill's verified
Minimalism & Swiss Style recommendation fits this application. The generated
marketing hero and gold accent do not serve this workspace and are excluded.
Retain system fonts and the existing SVG family, without new packages.

Show a page title, its actions and its content. Omit slogans, promotional
sections, repeated introductions, navigation captions, decorative icon blocks
and workflow explanations already conveyed by labels. Keep help where it
changes a decision: file limits, score units, model state, evaluation locks,
approval consequences, validation errors and contribution interpretation.
Optional batch metadata uses native `details`/`summary`, retaining values when
closed. Do not hide required fields, errors or irreversible-action consequences.

## Source of truth

`frontend/src/tokens.css` defines semantic colours, type sizes, spacing, radii,
shadows and motion. `index.css` owns shared components and the workspace shell.
`review.css`, `analysis.css` and `lab.css` own feature-specific layouts and consume
the same tokens. Existing `--blue` names alias the teal primary for compatibility.

| Role | Treatment |
| --- | --- |
| Primary action | Teal `--primary`, white text, darker hover |
| Secondary action | White surface, neutral border and text |
| Navigation | White, 224 px; one line per item, teal selected text and pale background |
| Body / support | 14 / 13 px, metadata normally 12 px; mobile inputs 16 px |
| Headings / metrics | 28 px page title, 18 px section title, 32 px main metric |
| Spacing | 4, 8, 12, 16, 20, 24, 32, 40, 48 px |
| Surfaces | 6 px controls, 8 px panels; thin borders, no panel shadows |
| Status | Label plus semantic colour; green / amber / red / neutral |
| Chart | Teal precision and dashed purple recall with visible labels |
| Focus | 3 px ring, including on light navigation and disclosure summaries |

Sizes, colours and breakpoints are reversible UI design decisions (AS-22),
not numeric requirements from the FRD/TRD.

## Responsive behaviour

- Above 1200 px: four summary cards; multi-column workspace panels.
- Up to 1200 px: two summary cards; existing detail and filter layouts adapt.
- Up to 900 px: existing keyboard-operable navigation drawer; panels stack.
- Up to 650 px: single-column forms, 16 px inputs, at least 44 px buttons.
- Dense tables retain all columns in their own scroll container; the document
  must fit the viewport. Long IDs and user-provided values wrap where appropriate.
- Respect reduced motion. Do not use hover as the only route to information.

## Page application

Login is a centred 360 px form without a promotional panel. Overview shows
existing server counts and operations. Alert, case, Model Flags and Audit share
plain panels, tables, filters and badges. Investigation links use secondary
buttons. Lab keeps seven steps as a compact progress list, validation/final
boundaries and role-based controls. Its progress list wraps into two columns
on small screens, with `aria-current="step"` identifying the current step.

Retain the complete demonstration and bilingual signal captions as plain text
(FR-406 / BR-406.1, TRD §10.6, FRD L-01, as previously verified in PROJECT_CONTEXT
§13). The shared demonstration caption sits in the page footer; signal captions
stay with the results. No collapsed or dismissible legal captions. Never invent
trends, counts, model quality, status or data to decorate production screens.

## Review

Check at 320/375/768/1024/1440 px, including landscape, populated and empty
states, keyboard drawer focus/Escape, visible focus, text contrast and reduced
motion. Browser fixtures must intercept all API requests before navigation;
never let upload/detection/model actions reach a live backend for visual QA.
