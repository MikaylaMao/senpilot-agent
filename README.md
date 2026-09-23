# Senpilot regulatory filing agent

An email request names one matter (`M` plus five digits) and one document type. The agent searches the Nova Scotia UARB public documents database, reads matter metadata and the five tab counts, downloads up to ten documents from the selected tab, attaches a ZIP, and replies to the requester.

## Quick evaluation: no email account required

Requires Python 3.11+ and Chromium. From this directory:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
python agent.py --demo "Other Documents from M12205"
```

The command prints a matter summary and creates `M12205_other_documents.zip` in this directory. It does not connect to email or send a message. Inspect the archive with `unzip -l M12205_other_documents.zip`. The live public site must be available for retrieval. Run `python -m pytest -q` for the offline parsing, metadata, and ZIP checks.

## End-to-end email evaluation

Use an evaluator-owned mailbox that supports IMAP and SMTP; for personal Gmail, an app password may be used where supported. Never put a real password in the submitted source or send it to the candidate.

1. Copy `.env.example` to `.env` and fill `MAIL_USERNAME`, `MAIL_PASSWORD`, and `MAIL_FROM` with the evaluator's test mailbox. Keep the server settings for Gmail or adapt them to the selected provider. Set `ALLOWED_SENDERS` to the exact sender address used for the test. `.env` is ignored by Git.
2. Send an **unread** message from that allowed address to the agent mailbox. Example subject: `Other Documents from M12205` (the body can be empty).
3. Run `python agent.py --once` in the same directory. It processes currently unread requests once and prints `Processed 1 messages` on success.
4. Check the sender inbox for a reply containing the matter summary and ZIP attachment. The ZIP has up to ten files from the chosen document type.

To keep checking for new messages, run `python agent.py` instead of `--once`. The process must remain running. The agent marks a request read after sending a response. SMTP failures leave it unread for retry, although an ambiguous SMTP disconnect can produce a duplicate reply.

## Run continuously for reviewer emails

The included Dockerfile packages Python and Chromium; `compose.yaml` runs the polling process with automatic restart. Use a dedicated mailbox for reviewers rather than a personal inbox. Configure its credentials in `.env` on the host only, or enter them as private service variables in the hosting dashboard. Do not commit `.env` or include it in a Docker image.

On an always-on machine with Docker Compose, run:

```bash
docker compose up -d --build
docker compose logs -f agent
```

Reviewers then email the configured `MAIL_USERNAME`. The service checks for unread mail every `POLL_SECONDS` (30 seconds by default) and replies with the ZIP. Stop with `docker compose down`. This requires the host to stay online and allow outbound IMAP (993), SMTP (465), and HTTPS traffic. Keep only one agent instance per mailbox to avoid competing reads. If `ALLOWED_SENDERS` is set, only those addresses can receive a response; for unknown reviewers, use an empty allowlist **only on a dedicated inbox**.

For hosted deployment, build the Dockerfile as a continuously running background worker with one instance and no public HTTP port. Set the variables in `.env.example` in the host's private environment settings. Do not assume a generic free web service will work: some sleep when idle, and some hosting providers block SMTP outbound. Verify the provider supports ports 993 and 465, then send a real email to the hosted mailbox and check the attachment before sharing the address. This repository contains the worker package; creating a cloud service still requires the owner's hosting account and mailbox credentials.

## Site behavior and limits

The challenge link uses `UARBI5` (letter I), which showed “Database not available.” The working database URL is `UARB15` (digit 1), now used by default. An end-to-end email request for M12205 / Other Documents was tested: it returned 10 PDFs in a ZIP, a total count of 62 across the five required categories, and matter metadata.

FileMaker renders only some document rows at once, so the downloader scrolls and tracks document numbers. It stops with an error rather than silently sending fewer than ten when more files exist but cannot be reached. Different document records may point to identical PDF bytes; records are preserved separately. Matter metadata follows the visible header layout. Other sections such as Hearings and Related Matters are excluded from the total because the requested document types are the five listed in the challenge. The site can become temporarily unavailable; retry the local demo when it is back.

No external language model is required for the constrained request format: deterministic extraction avoids interpreting arbitrary instructions inside emails and makes results reproducible. The orchestration is an agent loop from email to retrieval to response. Add LLM extraction only if broader phrasing becomes a product requirement, and validate its structured output against the same strict matter and type rules.
