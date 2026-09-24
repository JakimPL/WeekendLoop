---
key: readme
title: "Fill in the missing parts of the README"
labels: [documentation, "weekend:auto"]
expected_verdict: execute
expected_ineligibility: null
acceptance_test: test_readme.py
overlapping_branch: null
---
## Business requirement

New colleagues open the README to try Pocketchat. Two of its sections still say TODO, so everyone has to
ask a teammate how to start the mockup and what to do once it is running.

## Goal

`README.md` explains, in plain English, how to start Pocketchat and how to use the page.

## Scope

- `README.md`, section "How to run it": the command that installs Pocketchat, the command that starts it,
  and the address to open in a browser, as they are defined in `pyproject.toml` and `pocketchat/server.py`.
- `README.md`, section "How to use it": a short walkthrough for someone who is not a developer:
  asking a question, reading the answer, and a note that the answers are prepared in advance.
- The other sections and the headings stay as they are.

## Acceptance criteria

- No TODO is left in `README.md`.
- "How to run it" names `uv sync`, `uv run pocketchat` and the address `http://localhost:8000`.
- "How to use it" is a walkthrough of at least a few sentences.
- Everything the README states matches the current code.
