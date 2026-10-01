---
key: new-chat
title: "The New chat button does nothing"
labels: [enhancement, "weekend:auto"]
expected_verdict: execute
expected_ineligibility: null
acceptance_test: test_new_chat.py
linked_pull_request_branch: null
blocked_by: []
---
When people switch topic they hit "New chat" expecting a clean page, but nothing happens and the old
conversation just stays there. Somebody started this and never finished it: the button in the page
already POSTs to `/api/new-chat` (see `pocketchat/static/app.js`, that part is fine and should stay as
is), but the server has no such route.

What I want: `POST /api/new-chat` throws the conversation away and returns the messages in the same
shape as `GET /api/messages`, so the page ends up showing only the greeting. That needs a way for a
`Chat` to start over, plus the route in `pocketchat/routes.py`. There's also a skipped test in
`tests/test_routes.py` (`test_new_chat_keeps_only_the_greeting`), so please unskip it.

Done when:
- after asking something, `POST /api/new-chat` returns 200 with just the greeting
- `GET /api/messages` afterwards also returns just the greeting
- asking a new question after that works as usual
- the rest of the tests still pass
