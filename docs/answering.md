# Answering the agent

The agent asks its questions early in the week, so you can answer before the weekend run starts.

## The weekly rhythm

**Thursday.** `weekend-loop prepare` assesses the backlog, writes the plan and saves the triage on
disk for the coming run. If the agent has questions about an issue, it posts them as a comment on
that issue, adds the `weekend:needs-input` label and sends an alert.

**Thursday to Friday.** Answer on GitHub, in the issue's own thread, from the browser or the mobile
app. Reply with one line per question number:

```
1. Knots.
2. Round half up.

Also, leave the CLI alone this time.
```

- Numbered lines answer the matching questions.
- The rest of the comment is saved as a **standing note** on that issue.
- A plain reply to a single question answers it.

Leave the label in place. It tells the agent which threads to read, and the agent removes it when
it opens a pull request for the issue.

Only your own replies count. The agent reads comments from the account it treats as the operator:
the token's account, or `identity.operator_login` if you set one. A colleague's comment stays a
comment.

Answers and notes are saved in `<workspace>/state/briefing/<repo>/` and carry into every later run.
To read the replies now and see what was filed:

```
weekend-loop intake --repo-key demo
```

**Friday.** `weekend-loop weekend` reads the replies first. It then uses Thursday's triage instead
of running it again, with two exceptions. It re-assesses issues you answered or replied on, because
the earlier verdict was made without your answer. It also re-assesses issues edited since. All other
issues are carried over at no cost. The run's notes say how many were carried over and how many were
re-assessed. A prepared triage older than three days is discarded, and the run triages again.

**Monday.** The digest is in `outbox/digest.md` and in the digest issue, next to the draft pull
requests. If a task stopped on a question during the weekend, it asks that question on its issue in
the same format, and you answer it the same way.

`weekend-loop web` shows the runs locally on loopback and writes to the same answer store.

## Alerts

To get alerts, put a Slack or Discord incoming webhook URL in `<workspace>/secrets/alert-webhook.url`
(mode 0600). The agent then sends one message when:

- the prepare pass has questions
- a run finishes
- a run under systemd crashes or is killed

Without that file, no alerts are sent, and runs work as usual.

The question comments are the record. They live on the issues and read well in the GitHub mobile
app. The webhook is the alert. The agent writes with your own token, so GitHub treats those comments
as your own activity and sends you no notification for them.
