---
key: support-hours
title: "Tell people when support is around"
labels: [enhancement, "weekend:auto"]
expected_verdict: execute
expected_ineligibility: null
acceptance_test: test_support_hours.py
linked_pull_request_branch: null
blocked_by: []
---
People keep asking the mockup when they can talk to a human. Until the real assistant knows, give
it a prepared answer for questions about support or opening hours: the support team is around
Monday to Friday, 9:00 to 17:00 CET.

Prepared answers are in `pocketchat/answers.py`, next to the others.
