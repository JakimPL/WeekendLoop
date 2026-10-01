---
key: new-chat-count
title: "How often do people start over?"
labels: [enhancement, "weekend:auto"]
expected_verdict: execute
expected_ineligibility: null
acceptance_test: test_new_chat_count.py
linked_pull_request_branch: null
blocked_by: [new-chat]
---
Once the New chat button actually works (#1), product wants to know whether anyone uses it. Nothing
fancy: a `GET /api/stats` that answers with how many times this chat was started over, like
`{"new_chats": 3}`. Count it on the `Chat` itself, next to its messages. No database, it can go back
to zero when the server restarts.

Done when:
- a fresh chat reports `{"new_chats": 0}`
- every `POST /api/new-chat` adds one
- asking questions leaves the number alone
