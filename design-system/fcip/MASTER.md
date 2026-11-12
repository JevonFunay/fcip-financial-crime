# FCIP frontend design system

Applied 12 November 2026 using the `ui-ux-pro-max` skill. This is a visual
refresh of the existing application, authorised by Jevon. It adds no workflow,
permissions, model policy or backend capability.

## Direction

Use a clean, restrained enterprise workspace: navy navigation, teal actions,
white panels and a cool neutral canvas. The skill's verified Minimalism & Swiss
Style recommendation fits this application. Its generated marketing-page
patterns do not fit the product and are excluded. Retain system fonts and the
existing SVG icon family; no font service, package or animation library is needed.

## Source of truth

`frontend/src/tokens.css` defines semantic colours, type sizes, spacing, radii,
shadows and motion. `index.css` owns shared components and the workspace shell.
`review.css`, `analysis.css` and `lab.css` own feature-specific layouts and consume
the same tokens. Existing `--blue` names alias the teal primary for compatibility.

| Role | Treatment |
| --- | --- |
| Primary action | Teal `--primary`, white text, darker hover |
| Secondary action | White surface, neutral border and text |
| Navigation | Navy; selected item has text, background and an accent edge |
| Body / support | 14 / 13 px, metadata normally 12 px; mobile inputs 16 px |
| Headings / metrics | 32 px page title, 18 px section title, 36 px main metric |
| Spacing | 4, 8, 12, 16, 20, 24, 32, 40, 48 px |
| Surfaces | 10 px controls, 14 px panels; subtle shared shadow |
| Status | Label plus semantic colour; green / amber / red / neutral |
| Chart | Teal precision and dashed purple recall with visible labels |
| Focus | 3 px ring; light teal on navy navigation |

Sizes, colours and breakpoints are reversible UI design decisions (AS-21),
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

Login uses the same navy/teal identity and a restrained split layout. Overview
prioritises existing server counts and operations. Alert, case, Model Flags and
Audit share panel, table, filter, badge and button treatments. Lab keeps its seven
steps, validation/final boundaries and role-based controls intact.

Retain every demonstration and signal caption (FR-406 / BR-406.1, TRD §10.6,
FRD L-01, as previously verified in PROJECT_CONTEXT §13). Never invent trends,
counts, model quality, status or data to decorate production screens.

## Review

Check at 320/375/768/1024/1440 px, including landscape, populated and empty
states, keyboard drawer focus/Escape, visible focus, text contrast and reduced
motion. Browser fixtures must intercept all API requests before navigation;
never let upload/detection/model actions reach a live backend for visual QA.
