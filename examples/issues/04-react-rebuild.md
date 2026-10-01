---
key: react-rebuild
title: "Move the chat page to React"
labels: [refactor, "weekend:auto"]
expected_verdict: skip
expected_ineligibility: null
acceptance_test: null
linked_pull_request_branch: null
blocked_by: []
---
The front-end team asked if we could get Pocketchat's page onto React before it grows any bigger,
since that's what all our other web apps use and anyone from the team could then work on it.

Roughly: throw out the plain `index.html` / `app.js` / `style.css` and rebuild it as a Vite + React app
(`package.json`, `src/`, a build step), with the top bar, the message list and the question box as
separate components that each have tests. `pocketchat/routes.py` then serves the built files. The
Node build and tests also need to go into the GitHub workflow and get a mention in the README.

Users shouldn't notice any change, the page should look and behave the same.
