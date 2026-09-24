---
key: company-cloud
title: "Move Pocketchat to the company cloud"
labels: [enhancement, "weekend:never"]
expected_verdict: null
expected_ineligibility: never_label
acceptance_test: null
overlapping_branch: null
---
## Business requirement

Pocketchat runs on one developer's laptop whenever someone wants a demo. The team wants it on the company
cloud so that anyone in the company can open it at any time.

## Goal

Pocketchat runs on the company cloud behind the company sign-in, at an address everyone can reach.

## Scope

- Package Pocketchat as a container image and add the deployment configuration.
- Agree the address and the sign-in with IT, and set up both.
- Hand the running service over to the platform team.
