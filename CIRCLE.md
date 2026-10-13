# Progrez Circle

Circle adds four features to the learning MVP: a privacy-controlled journey profile, shareable links, preset encouragement, and milestone-follow notifications. It has no public feed or freeform messaging.

## Start and upgrade

Run `python manage.py migrate` followed by `python manage.py backfill_circle`. The backfill creates private profiles and historical celebration moments without sending retrospective notifications. It preserves learner progress and can be repeated safely. `start.ps1` includes both commands.

Sign in, choose a course, and open **My Circle**. Each course has its own profile, sharing preferences, supporters, achievements, and encouragement inbox. Existing and new learner profiles start **Private**. Choose **My Circle** for invited supporters or **Public** for anyone with the profile link. Save the selected fields, preview them, and copy a link. Localhost links work only on the same computer; external sharing requires deployment to a reachable HTTPS host.

Invitations are created locally for the learner to send. No message is sent automatically. A supporter needs no learner account. Their signed private link grants access for up to 90 days and should be kept private. Revoking, blocking, or unsubscribing invalidates it. Public followers do not gain invited-only access when a learner changes to My Circle.

## Shared information

The learner controls the display name, goal, course, overall completion percentage, demonstrated skills, milestone list, celebration moments, and whether encouragement is allowed. Goal sharing starts off. Assessment scores, momentum, confidence, constraints, friction, and recovery details are excluded by an explicit server-side allowlist. No struggle alerts are generated.

Privacy and field choices are checked when reading a profile or notification, writing encouragement, and delivering queued email. Returning to Private prevents supporter access and further delivery. Previously viewed information and already delivered email cannot be recalled.

## Encouragement and milestone updates

Supporters can choose one of five preset messages. A visitor or supporter can send one encouragement per learner per 24 hours, with an additional connection-level limit. Repeat submission of the same message is idempotent. Learners can mark messages read, hide them, block supporters, and mute future updates.

Completing a demonstrated competency creates one milestone event, combining the milestone and skill rather than sending duplicate notifications. Only active followers receive new, permitted events. Existing achievements are not replayed to new followers. The private supporter inbox works without email. Visible inboxes poll every 10 seconds; reloading also refreshes them.

Encouragement receipt, opening, and subsequent learning resumption are recorded as signals. They do not inflate momentum or prove that encouragement caused learning to resume.

## Optional email

Email is disabled by default. Set `CIRCLE_EMAIL_ENABLED=true`, `CIRCLE_SITE_ORIGIN` to the deployed HTTPS origin, and the SMTP variables in `.env.example` in the server environment. The reference file is not loaded automatically. Use the same environment for the worker:

```powershell
python manage.py deliver_circle_mail --limit 50
```

Schedule this explicit command through the deployment environment if recurring delivery is desired. Supporters must opt in and confirm their address before milestone emails are eligible. Verification links expire after 48 hours. The worker checks current consent, privacy, access, and field visibility immediately before sending, skips ineligible rows, and caps failed attempts at three. Sent rows are not deliberately retried, but SMTP acceptance followed by a process/database failure can still produce a duplicate on retry; this is not an exactly-once email system.

No SMTP provider, live email delivery, external scheduler, or public deployment was configured or exercised during local validation. Email behavior was tested with Django's in-memory backend.

## Implementation

`apps/circle/` contains models, migrations, scoped API operations, projections, event creation, and the email worker. `static/circle.js` and `static/circle.css` provide the owner and supporter screens. API endpoints are under `/api/v1/circle/`; mutations require CSRF protection. Shared responses disable caching and indexing and use a no-referrer policy.

Owner endpoints: `manage`, `inbox`, `privacy`, `invite`, `members/{id}/control`, and `encouragement/{id}/seen|hide`. Supporter endpoints: `public/{slug}`, `public/{slug}/follow|encourage`, `support/{token}`, and `support/{token}/activate|preferences|encourage|seen|unsubscribe`. Email confirmation uses `verify/{token}`; GET does not confirm consent, POST does.

Before deployment, use a stable secret key, HTTPS, and appropriate shared-cache/rate-limit infrastructure. The connection limit uses the direct peer address, not untrusted forwarded headers; a reverse proxy requires a deliberate trusted-client-address configuration. Browser visitor limits deter casual repetition but do not establish identity. The MVP caps each Circle at 100 supporters.
