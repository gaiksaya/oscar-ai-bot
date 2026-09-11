# OSCAR Release Notifier

Scheduled Lambda that posts release-readiness updates to the release managers' Slack
channels, so an RM learns that something is blocking a release without having to ask.

## How it works

EventBridge invokes the Lambda every six hours. Per run:

1. `list_active_releases` on the metrics Lambda — every version whose schedule status is
   `active`, with its dates, days remaining and cadence phase.
2. For each release, the phase decides whether a post is even possible
   (`cadence.PHASE_INTERVAL_HOURS`). A release well before RC, already shipped, cancelled or
   with no dates registered is skipped without further work.
3. `get_release_status` on the metrics Lambda — the R/Y/G verdict and the per-criterion
   breakdown.
4. `cadence.should_notify` compares that against what was last posted, recorded in
   DynamoDB. A post goes out on the first run, when the verdict changes, when the set of
   outstanding criteria changes, or as a 24-hour heartbeat — never more often than the
   phase's interval allows.
5. `message_builder.build_message` renders it and the Lambda posts to every configured
   channel, then records what it posted.

**The verdict is not computed here.** It comes from the metrics Lambda, which owns the
rubric, so the number an RM reads in Slack is the same number they get by asking OSCAR
directly. That is also why this function needs no cluster access, no VPC attachment and no
cross-account role — only permission to invoke one Lambda.

## Cadence

| Phase | Posts every | Why |
|---|---|---|
| `out_of_window` | never | More than 14 days before RC; nothing is due yet |
| `pre_rc_daily` | 24h | 8–14 days before RC |
| `pre_rc_frequent` | 6h | 0–7 days before RC |
| `rc_to_release` | 24h | RC cut, release not yet reached |
| `final_push` | 6h | Two days or fewer to release |
| `overdue` | 48h | Active but past its date; already known to be late |
| `released` / `cancelled` / `not_scheduled` | never | Nothing left to report |

Messages lead with the criteria due in the **current** phase: entrance criteria gate the
RC, exit criteria gate GA. Before RC an unfinished exit criterion is expected rather than
news, so presenting it as blocking would train readers to ignore the alert. Criteria that
are open but not yet due are still listed, just separately.

## Configuration

| Source | Key | Purpose |
|---|---|---|
| Central secret | `SLACK_BOT_TOKEN` | Posting to Slack |
| Central secret | `RELEASE_CHANNELS` | Comma-separated channel IDs; unset means the notifier does nothing |
| Lambda env (CDK) | `METRICS_FUNCTION_NAME` | The metrics Lambda to invoke |
| Lambda env (CDK) | `RELEASE_NOTIFY_TABLE_NAME` | Where the last post per version is recorded |
| Lambda env (CDK) | `CENTRAL_SECRET_NAME` | Where to read the two secret values |

Point `RELEASE_CHANNELS` at a test channel first — the cadence only becomes visible over
several days of a real release.

## Tests

```bash
pipenv run pytest tests/lambda/release_notifier/
```
