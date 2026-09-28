# Writing about work

This guide is for anyone who writes about a change to software: the issue that asks for it, the
plan that prepares it, the pull request that delivers it, and the questions asked along the way.
The reader is busy and may never open the code. The rules come first; every later section spells
one of them out.

## The rules

1. Open with what a person notices, in their words: what goes wrong, what is missing, or what they
   cannot do.
2. Name who notices it: a user of the product, the person who runs it, a developer who calls the
   code, or nobody yet.
3. Say what is different after the change, for that same person, in one or two sentences.
4. Show how to see it: numbered steps a newcomer can follow, each with what they should see. When
   nothing is visible, say so in one sentence, and say who could notice and how.
5. Put code names, file paths, commands and design choices after that. They are for the person who
   reads the diff.
6. Write one idea per sentence, in plain words. Explain a term a newcomer would not know the first
   time it appears.
7. Make every claim checkable: a step to follow, a command to run, or a test to read.
8. Stop when the content stops. Leave out what the reader cannot use.

## Plain words

- Describe the event before its code: "the database was unreachable" comes before
  `StoreUnavailable`.
- Describe the part before its name: "the background job that archives finished games" comes
  before "the sweep".
- Use the words the product shows: button labels, page titles, command names.
- Give amounts with their units: "an hour", "every minute", "at most 50 games".
- Write whole sentences with a verb.
- Keep one word for one thing. When the text says "table" once, it means the same table every time.
- Put an internal name in backticks, once, in the second half of the text.

## Describing a task

A task is work that has not happened yet: an issue, or a plan for one. Write its parts in this
order.

1. **The situation.** What a person meets today, in their words. One short paragraph.
2. **The goal.** What will be true afterwards, for that person.
3. **What to build.** The decisions already taken: behavior, shapes, names, limits.
4. **Where it lives.** The files to change, and the files to create marked "(new)".
5. **Done when.** Checkbox lines. Each is something a reviewer can observe or a test can assert.
6. **How to see it.** Steps in the running product, or the sentence from "When nothing is visible".
7. **Still open.** Each open question, with the default the work assumes.

A reader who stops after part 2 knows why the task matters. A worker who reads to the end knows how
to do it.

## Describing a delivery

A delivery is finished work: a pull request, or a report of one. Write its parts in this order.

1. **What was wrong.** One or two sentences, in the words of the person who noticed.
2. **What changes.** What that person gets now. When nothing is visible, this is where it says so.
3. **How to see it.** Numbered steps, each with what the reviewer should see, or the sentence from
   "When nothing is visible".
4. **How it was checked.** The commands that ran and their results, then anything seen by hand.
5. **Decisions.** Each choice the task left open and each departure from the task, with its reason.
6. **Open questions.** What only the task's author can answer.

Write parts 1 to 3 for someone who will never open the diff. Write parts 4 to 6 for someone who
will.

## Asking a question

- Ask one thing per question, in one or two sentences.
- Put the context in the question: what you found, and why it matters.
- Offer the choices you see, and name the one you would take.
- Ask only what the code, the documentation and the task leave open.

Example: "The new rule can stand last in the settings list or beside the two rules it relates to.
Beside them reads better, but it reorders a file other tasks append to. Which do you prefer? Without
an answer, it stands last."

## When nothing is visible

Many good changes have no effect a user can see: a fix that matters only when something fails, a
check against a request no screen can send, code moved without changing what it does, a faster
query, groundwork for a later task. Say so plainly, in this form:

> Nothing changes for <who>, because <why>. <Who could notice> would see <what>, by <how>.

- "Nothing changes for a player, because the app offers only the letters of the table's alphabet.
  A request built by hand, or a future bot, that sends another letter is now refused. The tests in
  `tests/test_games.py` show it."
- "Nothing changes for a user, because the code moved and its behavior stayed. The full test suite
  passes unchanged."

After that sentence, give the steps for the person who could notice, and say who they are for:
"For the person who runs the server: stop the database, then watch the log."

## Examples

Each pair shows one opening written twice. The first version needs the code open; the second
stands on its own.

**A background job.**
- First: "`TableSweep.once` now wraps `registry.close` in a handler for `StoreUnavailable`."
- Second: "When a game ends, a background job saves it and closes its table an hour later. When the
  database was down at that moment, the job stopped for good, and no game was saved again until a
  restart. Now the job logs a warning, keeps the table, and saves it on its next round."

**A form in a web page.**
- First: "Added `maxLength` to `ProfileForm` and a 422 path to `update_profile`."
- Second: "A name longer than 40 characters was saved, and it broke the scoreboard's layout. Now
  the name field stops at 40 characters and says why."

**A command-line tool.**
- First: "Refactored `parse_args` into subparsers and added a `--dry-run` flag to `sync`."
- Second: "`sync` changed files with no way to preview it first. Now `sync --dry-run` prints what it
  would change and leaves every file as it was. Try `sync --dry-run ~/photos`, then check that the
  folder is the same."

**A library.**
- First: "`Client.retry` now honors `Retry-After`."
- Second: "A caller that hit the rate limit got an error at once, even when the server said to wait
  two seconds. Now the client waits as long as the server asks, up to 30 seconds, and tries once
  more. Callers see fewer failures, and calls that succeed behave as before."

## Before you send

Answer each question with yes before you send.

- Does the first sentence say what a person notices, with no code name in it?
- Does the text say who is affected?
- After the first paragraph, could someone who never opened the code say what changed?
- Does every step say what the reader should see?
- When nothing is visible, does one sentence say so?
- Is every unusual term explained the first time it appears?
- Could the reader check every claim?
- Does the text end when the content ends?
