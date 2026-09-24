# Answering the agent

## Giving the agent input

A weekend run happens in two moments, so the questions reach you while there is still time to
answer them.

**Thursday** `weekend-loop prepare` assesses the backlog, writes the plan, and leaves the triage on
disk for the coming run to take up. When the agent has questions about an issue, it asks them in a
comment on that issue, labels it `weekend:needs-input`, and pushes an alert.

**Thursday to Friday** you answer on GitHub, in the issue's own thread, from the browser or the
mobile app. Reply with one line per question number:

```
1. Knots.
2. Round half up.

Also, leave the CLI alone this time.
```

Numbered lines answer the matching questions. The rest of the comment is kept as a **standing
note** on that issue, and a plain reply to a single question answers it whole. Leave the label in
place: it tells the agent which threads to read, and the agent takes it off once it opens a pull
request for the issue. Only your own replies count. The agent reads the comments of the account it treats as the
operator — the token's account, or `identity.operator_login` when you set one — so a colleague's
comment stays a comment.

Answers and notes land in ``<workspace>/state/briefing/<repo>/`` and carry into every later run. To read the
replies now and see what was filed:

```
weekend-loop intake --repo-key demo
```

**Friday** `weekend-loop weekend` reads the replies first, then takes up Thursday's triage instead
of buying it again. It re-assesses the issues you answered or replied on — the earlier verdict was
formed without your answer, so it is no longer the right one — and the issues edited since.
Everything else is carried at no cost, and the run's notes say how many were carried and how many
re-bought. A prepared triage older than three days is left behind and the run triages afresh.

**Monday** the digest is in `outbox/digest.md` and in the digest issue, next to the draft pull
requests. A task that stopped on a question during the weekend asks it on its issue in the same
format, and the same kind of reply answers it.

`weekend-loop web` serves a local view of the runs on loopback and writes to the same answer store.

### Alerts

Point `<workspace>/secrets/alert-webhook.url` at a Slack or Discord incoming webhook (mode 0600) and the agent
sends one message when the prepare pass raises questions, one when a run finishes, and one when a
run under systemd crashes or is killed. Without that file it sends nothing and the run is unaffected.

The question comments are the artifact: they live on the issues and read well in the GitHub mobile
app. The webhook is the alert. The agent writes with your own token, so GitHub counts those comments
as your own activity and sends you no notification for them.
