---
key: answer-ratings
title: "Let people rate Pocketchat's answers"
labels: [enhancement, "weekend:auto"]
expected_verdict: needs_input
expected_ineligibility: null
acceptance_test: null
overlapping_branch: null
---
## Business requirement

We want to know which of Pocketchat's answers people find helpful, so the team knows what to improve
first. Today there is no way to tell Pocketchat that an answer was good or bad, and the feedback we get
arrives by word of mouth.

## Goal

People can rate each of Pocketchat's answers, in the rating style the product team chose at Tuesday's
review.

## Scope

- `pocketchat/static/app.js` and `pocketchat/static/style.css`: show the rating controls under each of
  Pocketchat's answers.
- `pocketchat/chat.py` and `pocketchat/routes.py`: accept a rating for an answer and keep it with the
  conversation.
- `tests/test_routes.py`: cover rating an answer.
- Keep it simple; Pocketchat is still a mockup.

## Acceptance criteria

- Every answer from Pocketchat shows the rating controls the product team chose.
- A rating given on the page is kept with that answer.
