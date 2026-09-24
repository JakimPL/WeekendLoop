---
key: react-rebuild
title: "Rebuild the chat page with React"
labels: [refactor, "weekend:auto"]
expected_verdict: skip
expected_ineligibility: null
acceptance_test: null
overlapping_branch: null
---
## Business requirement

Pocketchat's page is written in plain HTML and JavaScript. Before it grows, the front-end team wants it on
React, the toolkit the company's other web applications use, so any front-end developer can pick it
up. People using Pocketchat see the same page as today.

## Goal

The chat page rebuilt as a React application, looking and behaving exactly as it does now.

## Scope

- Replace `pocketchat/static/index.html`, `pocketchat/static/app.js` and `pocketchat/static/style.css` with a React
  application built with Vite: `package.json`, `src/`, and a build step.
- Split the page into components (top bar, message list, question box), each with its own tests.
- Serve the built files from `pocketchat/routes.py`.
- Add the Node.js build and tests to `.github/workflows/tests.yml` and describe them in `README.md`.

## Acceptance criteria

- The page looks and behaves as it does today.
- `npm run build` and `npm test` pass in CI next to the Python tests.
