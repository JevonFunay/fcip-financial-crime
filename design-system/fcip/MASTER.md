# FCIP frontend design system

Updated 13 November 2026 using the `ui-ux-pro-max` skill. Jevon asks for a more
attractive interface that remains restrained. AS-24 refines AS-22's clean
layout through colour, typography and surface treatment, preserving its
short copy and progressive disclosure.

## Direction

Use a light, functional workspace: white navigation, a warm grey-green canvas,
forest green actions and dark selected navigation. Give white panels thin
borders, restrained shadows and consistent rounded corners. The skill's
verified Minimalism & Swiss Style recommendation fits this application; its
generated marketing gateway and dark/neon financial palette do not fit the
existing brief. Retain system fonts and the existing SVG family, without new
packages, external fonts or decorative animation.

Use one dark summary card for transaction volume, keeping every value sourced
from the same API. All other summary cards remain white. Small line icons
support metric labels without introducing additional icon containers. The
existing shield mark, favicon and user initials provide compact identity.
Avoid gradients, glowing effects, decorative charts, invented trends and
additional explanatory copy.

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
| Primary action | Forest green `--primary`, white text, darker hover |
| Secondary action | White surface, neutral border and text |
| Navigation | White, 240 px; one line per item, dark forest selected item and white text |
| Body / support | 15 / 14 px, metadata normally 12 px; mobile inputs 16 px |
| Headings / metrics | 32 px page title, 18 px section title, 38 px metric (28 px on phones) |
| Spacing | 4, 8, 12, 16, 20, 24, 32, 40, 48 px |
| Surfaces | 8 px controls, 12 px panels, 18 px login card; thin borders and subtle shadows |
| Featured summary | Dark `--feature-bg`, white value, pale label/icon; transaction volume only |
| Status | Label plus semantic colour; green / amber / red / neutral |
| Chart | Forest green precision and dashed purple recall with visible labels |
| Focus | 3 px ring, including on light navigation and disclosure summaries |

Sizes, colours and breakpoints are reversible UI design decisions (AS-24),
not numeric requirements from the FRD/TRD.

## Responsive behaviour

- Above 1200 px: four summary cards; multi-column workspace panels.
- Up to 1200 px: two summary cards; existing detail and filter layouts adapt.
- Up to 900 px: existing keyboard-operable navigation drawer; panels stack.
- Up to 650 px: single-column forms, 16 px inputs, at least 44 px buttons.
- Reserve two lines for metric labels on phones so values in each row align.
- Hide the optional user-initial avatar below 768 px, retaining role/logout.
- Dense tables retain all columns in their own scroll container; the document
  must fit the viewport. Long IDs and user-provided values wrap where appropriate.
- Respect reduced motion. Do not use hover as the only route to information.

## Page application

Login is a centred white card (440 px outside, up to 360 px form) on the shared
canvas, without a promotional panel. On phones the outer border, shadow and
padding disappear, leaving the form available at up to 360 px. Overview shows
existing server counts and operations. Alert, case, Model Flags and Audit share
panels, tables, filters and badges. Model versions and thresholds have a lightly
tinted value group. Investigation links use secondary buttons. Lab keeps seven
steps as a compact progress list with a filled current-step number, validation/final
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
