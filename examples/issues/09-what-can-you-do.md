---
key: what-can-you-do
title: "Pocketchat can't say what it can do"
labels: [enhancement, "weekend:auto"]
expected_verdict: execute
expected_ineligibility: null
acceptance_test: test_what_can_you_do.py
linked_pull_request_branch: null
blocked_by: []
---
The first thing people type is "what can you do?" and they get the generic fallback, which is a bit
sad for a demo. Pocketchat should answer that one properly: say it's a mockup and list what it can
actually answer right now (greetings, who it is, weekends, jokes).

The prepared answers live in `pocketchat/answers.py`.
