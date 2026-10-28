# Knowledge Library

Private knowledge pages stay behind a sign-in. A public summary is a separate, approved document. Email sends only that approved version, and only after a person confirms the send.

Task IDs: ADP-13 authentication, ADP-14 visibility, ADP-15 public summaries, ADP-16 email, ADP-17 data model, ADP-18 API, ADP-19 tests, ADP-20 screens, ADP-21 customer dashboards, ADP-22 weekly email, ADP-23 mailbox setup.

## Run

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

## First super admin

There is no public registration and no API that grants a privileged role. Create the first super admin once from an operator shell. The password is read from the environment, not from the command line, and only its scrypt hash is stored.

```powershell
$env:ADP_BOOTSTRAP_EMAIL = "admin@example.com"
$env:ADP_BOOTSTRAP_PASSWORD = "a-long-unique-password"
.\.venv\Scripts\python -m app.bootstrap
Remove-Item Env:ADP_BOOTSTRAP_PASSWORD
```

The command refuses to run when a super admin already exists. Sign in, change the password, and create everyone else from Users. Further role changes require a signed-in super admin. The last active super admin cannot be removed.

Passwords are hashed with scrypt (N=16384, r=8, p=1). Sessions last at most 12 hours and go idle after 2 hours. Reset links expire after 30 minutes and work once. Login and reset requests are rate limited. Passwords, reset tokens, and message bodies are redacted from logs.

## Roles

| Role | Access |
| --- | --- |
| VIEWER | Read published private pages |
| EDITOR | Edit pages and draft public summaries |
| EDITOR_PLUS | Editor access, plus ingestion and email drafts |
| SUPER_ADMIN | Approve summaries, manage users, sources, publication, and email |

An editor plus cannot send public email unless a super admin grants that permission on the user.

## Public summaries

Editors write a summary or request an AI draft. The draft is labeled and is not published or emailed. A super admin approves it. The public page and emails use that approved snapshot. Editing the summary returns it to draft; the previous public version stays up until the new one is approved. If the private page changes, the summary is marked possibly outdated and is not rewritten.

## Email

Sending is disabled until a super admin sets the address and service in Settings. The local service stores mail in the mailbox for development and tests. It does not contact an external server. SMTP sends through the host saved there.

A share is a one-to-one message and does not subscribe the recipient. A newsletter goes only to confirmed subscribers, skips unsubscribed and suppressed addresses, and includes a one-time unsubscribe link. Failed deliveries can be retried; a delivery that already succeeded is not sent again.

## Tests

```powershell
.\.venv\Scripts\python -m pytest
```
