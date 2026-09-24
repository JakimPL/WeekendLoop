---
key: new-chat
title: "The New chat button does nothing"
labels: [enhancement, "weekend:auto"]
expected_verdict: execute
expected_ineligibility: null
acceptance_test: test_new_chat.py
overlapping_branch: null
---
## Business requirement

People press New chat when they change topic and want a clean page. The button is already on the
page, but clicking it does nothing, so the old conversation stays on screen. The work was started
and never finished.

## Goal

Clicking New chat clears the conversation and shows only Pocketchat's greeting again.

## Scope

- `pocketchat/static/app.js` already sends `POST /api/new-chat` when the button is clicked and shows the
  messages that come back; it stays as it is.
- `pocketchat/routes.py`: answer `POST /api/new-chat` by starting the conversation over and returning its
  messages, in the same shape as `GET /api/messages`.
- `pocketchat/chat.py`: let a chat start over with only the greeting.
- `tests/test_routes.py`: remove the skip from `test_new_chat_keeps_only_the_greeting`.

## Acceptance criteria

- After a question and its answer, `POST /api/new-chat` responds with status 200 and only the greeting.
- `GET /api/messages` afterwards returns only the greeting.
- A question asked after New chat gets an answer as usual.
- The existing tests keep passing.
