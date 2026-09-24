---
key: dark-mode
title: "Add a dark mode"
labels: [enhancement]
expected_verdict: null
expected_ineligibility: open_linked_pull_request
acceptance_test: null
overlapping_branch: feat/dark-mode
---
## Business requirement

People who chat with Pocketchat in the evening find the white page too bright. Most of them already run
their computer in dark mode and expect the page to follow it.

## Goal

The page switches to dark colors whenever the computer is set to dark mode.

## Scope

- `pocketchat/static/style.css`: dark values for the color variables under
  `@media (prefers-color-scheme: dark)`.
- The page follows the system setting; a separate switch on the page is a later topic.

## Acceptance criteria

- With the system in dark mode, the page background is dark and every text stays readable.
- With the system in light mode, the page looks as it does today.
