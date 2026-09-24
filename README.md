# Senpilot regulatory filing agent

Email the agent a Nova Scotia UARB matter number (`M` followed by five digits) and one document type: Exhibits, Key Documents, Other Documents, Transcripts, or Recordings. It finds the matter, counts files across those five categories, downloads up to ten files of the requested type, and replies with a ZIP and matter summary.

## Try it locally

With Python 3.11+ installed:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
python agent.py --demo "Other Documents from M12205"
```

The demo prints the response and saves `M12205_other_documents.zip` locally; it does not send email. The public UARB site must be available. Run `python -m pytest -q` for offline checks.

## Email the hosted agent

Send an email to the agent mailbox with a subject such as `Other Documents from M12205`. The body may be empty. The workflow in `.github/workflows/check-mail.yml` checks unread email every 15 minutes and replies to allowed senders. You can start a check immediately under **Actions → Check agent mailbox → Run workflow**. Scheduled GitHub Actions jobs may run late or fail; check the run log if a reply does not arrive.

To configure your own **private** GitHub repository:

1. Add Actions repository **secrets** `MAIL_USERNAME` (the agent Gmail address) and `MAIL_PASSWORD` (its Gmail app password) under **Settings → Secrets and variables → Actions**.
2. Add an Actions repository **variable** `ALLOWED_SENDERS`, for example `@senpilot.com,student@uwaterloo.ca`. Entries may be exact addresses or `@domain` rules. This compares the displayed From address and does not authenticate its owner. An empty value accepts any sender, so use an allowlist if the agent shares a personal mailbox.
3. Send a new email from an allowed address. Open the next run's **Process unread requests** log and confirm `Processed 1 messages` and that the reply contains the ZIP. A successful run with `Processed 0 messages` means it found no unread allowed request.

Keep the real password in GitHub Actions secrets or a local `.env`; never commit `.env`. Stop any locally running agent with `docker compose down` before testing Actions so only one process reads the mailbox. The 15-minute schedule uses GitHub Actions minutes, including checks with no messages; monitor usage during evaluation.

## Run email locally instead

Copy `.env.example` to `.env` and fill the mailbox credentials and `ALLOWED_SENDERS`. Send a new request email, then run `python agent.py --once` to check once, or `python agent.py` to keep polling while your computer is awake. `POLL_SECONDS` applies only to continuous polling, not GitHub Actions.

The optional `Dockerfile` and `compose.yaml` run that continuous mode on a machine with Docker: `docker compose up -d --build`; stop it with `docker compose down`. A sleeping laptop cannot check email, and only one agent should poll the mailbox at a time.
