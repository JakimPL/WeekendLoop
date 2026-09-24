---
key: dark-mode
title: "Add a dark mode"
labels: [enhancement]
expected_verdict: null
expected_ineligibility: open_linked_pull_request
acceptance_test: null
overlapping_branch: feat/dark-mode
---
People chatting in the evening find the white page too bright, and most of them already run their
computer in dark mode, so the page should follow that setting.

Add dark values for the color variables in `pocketchat/static/style.css` under
`@media (prefers-color-scheme: dark)`. A manual switch on the page is a separate topic, so only the
system setting matters here.

Acceptance criteria:
- with the system in dark mode the page background is dark and all text stays readable
- with the system in light mode the page looks exactly as it does today
