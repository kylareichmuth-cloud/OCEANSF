# Ocean SF Daily Lead Agent

An autonomous agent that researches one new B2B/partnership lead per day
for Ocean SF — rotating across press contacts, micro-influencers, and
boutique wholesale prospects — using the Anthropic API with web search
tool use, and drafts a personalized outreach email for each one.

**This agent never sends email automatically.** It writes each draft to
the local `drafts/` folder for you to read, edit, and send yourself from
your own email client. That's a deliberate design choice: unreviewed
AI-drafted cold outreach to real press, boutiques, or influencers is a
real reputational risk, so a human stays in the loop before anything
goes out. It also does not look up or verify a real email address for
the contact — you'll need to find and confirm that yourself before
sending.

## How it works

1. **Category rotation** — deterministically picks today's lead type
   (press / influencer / boutique) based on the date, so coverage rotates
   evenly across categories over time.
2. **Memory** — `leads_log.json` stores every lead the agent has already
   surfaced. Each run passes that history back into the prompt so the
   agent never re-suggests a contact it has already found.
3. **Tool-using research** — the agent calls the Claude API with the
   `web_search` tool enabled, so it does real, current research rather
   than guessing from training data.
4. **Structured output** — the model is instructed to return a single
   JSON object matching a fixed schema (name, link, why it fits,
   recommended angle, email subject, full email body). This is what
   makes the output reliable enough to log or write to a file, instead
   of unstructured prose you'd have to re-read every day.
5. **Draft, don't send** — `deliver_lead()` writes a plain-text draft to
   `drafts/YYYY-MM-DD_type_name.txt` containing the full email, clearly
   marked `STATUS: DRAFT`. Nothing is emailed automatically.
6. **Scheduling** — runs automatically via a GitHub Actions cron workflow
   (`.github/workflows/daily_lead.yml`), so it fires in the cloud every
   morning with no computer needing to be on. A local cron alternative is
   documented in `schedule_cron_example.txt`.

## Setup

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY="sk-ant-..."
python agent.py
```

For the GitHub Actions schedule to work, add `ANTHROPIC_API_KEY` as a
repository secret (Settings → Secrets and variables → Actions).

## Extending it

- **Delivery**: `deliver_lead()` in `agent.py` writes each draft to the
  `drafts/` folder by default. There's a commented-out block using
  `smtplib` that will additionally email the draft *to yourself* (not
  the contact) — uncomment and add `SENDER_EMAIL` / `RECIPIENT_EMAIL` /
  `SENDER_APP_PASSWORD` secrets if you'd rather have drafts land in your
  own inbox than in a local folder. Swapping in a Slack webhook or
  writing to a Google Sheet would follow the same pattern.
- **Actually sending**: if you eventually want one-click sending after
  review (rather than copy/paste), the natural next step is Gmail's API
  with a `create_draft` call instead of `send` — that still leaves the
  final send action in your Gmail UI, not in the script.
- **New brands**: the brand context and lead-type instructions are
  isolated at the top of `agent.py` (`BRAND_CONTEXT`, `LEAD_TYPE_INSTRUCTIONS`)
  so the same architecture could be repointed at a different brand or
  client by editing those two blocks.
- **New lead categories**: add an entry to `LEAD_TYPES` and
  `LEAD_TYPE_INSTRUCTIONS`.

## Architecture notes (for write-ups / interviews)

This is a small but complete example of an agentic pipeline:
- **Tool use**: the model doesn't just generate text, it calls a real
  search tool and reasons over the results.
- **Structured output**: JSON schema enforcement turns free-form model
  output into something a program can reliably parse and act on.
- **Statefulness**: the agent maintains its own memory across runs
  (the log file) so behavior improves/adapts over time instead of being
  a stateless one-shot call.
- **Autonomous scheduling**: it runs unattended on a cron trigger, with
  no human needing to initiate each run — a CI/CD pipeline doing
  AI-driven work rather than just tests/builds.
