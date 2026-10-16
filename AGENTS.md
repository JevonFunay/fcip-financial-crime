# Working rules for Codex in this repository

Set by Jevon on 30 September 2026. They apply to every session. Project
context lives in `docs/PROJECT_CONTEXT.md` (read it first), progress in
`docs/PROGRESS.md`, the specification in `docs/FRD_*.pdf` and `docs/TRD_*.pdf`.

## Decide yourself, then record it

- Implementation details, test design, refactors and bug fixes within the
  scope of the stage being worked on
- Choices that clearly follow the FRD/TRD — cite the section
- A local commit whenever a stage is finished and every test is green
- Small assumptions that are easy to reverse: go ahead, and record them as
  `AS-xx` in `docs/PROJECT_CONTEXT.md` §8 ("Our own assumptions")

## Stop and ask Jevon first

- Any change to the generator or to the shared data (`backend/sample_data/Tiny`,
  `Small`). Generator 1.4.0 is the last change before training; the next one
  goes through Jevon
- Anything that contradicts the FRD/TRD or a mentor directive, or that is
  still waiting on the mentor (CF-05 and the other open items)
- A new assumption that is not in the spec AND affects the model results or
  the team
- Changing the scope or the order of the stages
- Anything irreversible: deleting data, rewriting git history, force push,
  loosening security
- `git push` — always Jevon, never Codex

## Reporting

- No long report per step: record progress in `docs/PROGRESS.md`
- At the end of every stage: a short summary (at most 10 lines), a list of
  "decisions I took myself" for Jevon to check, and a capstone summary in
  Indonesian
- Stay honest about limitations and odd results
- Before calling the spec silent on something, search it in Indonesian too;
  flag any number that is guessed rather than taken from the FRD/TRD
