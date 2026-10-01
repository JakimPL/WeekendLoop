# Repository conventions

No conventions file was written for this repository, so read the code and follow what it already
does: the style of the files you touch, the test framework that is already present, and the commit
subject style of the recent history.

Hold to these in any repository:
- Change as little as the task needs, in the files the task names; a fix reaches every place
  with the same defect.
- Two tasks in one run never change the same hand-written file; shared files such as a changelog
  or a generated catalog are appended to, never rewritten.
- Leave dependencies and lockfiles as they are; they are the human's to change.
- Leave continuous integration configuration alone.
- Verify with the tests that cover the change; the orchestrator runs the full gate afterwards.
- Say so in the delivery when the conventions are unclear rather than inventing one.
