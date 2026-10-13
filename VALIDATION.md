# Validation record

Core walkthrough validated on 2026-10-10; Circle update validated on 2026-10-12.

## Automated checks

- `python manage.py check`: no issues.
- `python manage.py makemigrations --check --dry-run`: no missing migrations.
- `python manage.py test tests`: 61 tests passed in an isolated test database.
- JavaScript syntax check: passed.
- Fresh SQLite migration and content/demo seed: passed.
- Momentum refresh management command: verified against the separate browser-test database.

Local runtime: Python 3.10.4, Django 5.2.6, Django REST Framework 3.16.1, Pydantic 2.6.4. Python 3.12 is the recommended deployment runtime; PostgreSQL configuration is provided but was not exercised locally.

## Browser checks

Used a separate freshly seeded SQLite database and local server for the browser walkthrough, preserving the working app’s existing records.

- Demo sign-in and complete dashboard.
- Tunde simulation produces prerequisite-gap evidence and ranked explanations.
- Learner confirmation creates the matching recovery plan.
- Start captures a baseline; each completed recovery step updates the plan.
- Reloading the final mission retains recovery context and the confidence question.
- Submitting actual correct answers records assessment improvement from 41% to 100%.
- The tested result displayed momentum from 15 to 45 and a recovery score of 92. These are calculated outcomes, not inserted demo results.
- Desktop layout visually inspected.
- Phone-width layout visually inspected; document width equals viewport width, with no page-level horizontal overflow.
- Final browser console error log: empty.

## Circle update checks (2026-10-12)

- 24 Circle tests plus the existing 23 core tests passed. Coverage includes private defaults, field allowlists, ownership, access revocation, follower permissions, CSRF, abuse limits, silent backfill, milestone deduplication, unsubscribe after privacy changes, and optional email consent/delivery eligibility.
- Django checks, migration drift check, and syntax checks for both browser scripts passed.
- Separate learner and supporter browser sessions used `qa-circle.sqlite3`; the actual database retains five learners with all five profiles Private.
- Browser: private default, invited-only sharing, sibling invitation, joining without a learner account, and completing both data-cleaning checkpoints.
- Browser: one data-cleaning milestone appeared in the supporter inbox; its Proud of you message appeared in the learner inbox and could be marked read.
- Browser: returning to Private hid the supporter journey; Stop following still worked on the unavailable-profile screen.
- Desktop owner and supporter layouts visually inspected. A requested viewport override did not change the test tab dimensions, so a new phone-width Circle check is not claimed. The earlier phone check above concerns the core application.
- Main server restarted with the updated code and the My Circle page opened successfully on port 8000. Existing learner progress was preserved.

## Multi-course update (2026-10-13)

- All 61 tests passed, including 14 multi-course tests. They cover registration/login course selection, independent goals/diagnostics/pace/momentum/progress, cross-course ID rejection, tab context precedence, Python recovery, Circle privacy/inbox/notification/email isolation, selective demo reset, all 12 Python checkpoints, reseeding, and adding a third course without frontend changes.
- Fresh SQLite migration and seeding produced exactly two courses, 24 lessons, 12 competencies, and the existing shared intervention templates.
- Backed up the main database to pre-courses-backup.sqlite3, applied migrations, and seeded without resetting learners. A column-by-column comparison verified preservation of all 377 existing rows across 23 learner/content/Circle tables. All five existing learners were retained.
- Browser walkthrough in a separate qa-courses.sqlite3 database: demo login opens the course library; Python selection prompts a Python goal and diagnostic; passing the first Python checkpoint gives 8% progress; Python Circle starts private; switching back to Data Analysis retains its 33% sample progress and original goal/content.
- Desktop course library and course navigation inspected visually. Phone-width inspection was not repeated for this update.
- Python lesson styling preserves indentation; checkpoints do not execute code. The main app was restarted with the migrated database on port 8000.

## Not represented as verified

- Public hosting, production WSGI/proxy setup, or a PostgreSQL deployment.
- Live external AI calls. The optional gateway contract, schema validation, and timeout fallback are implemented; a live endpoint/key was not supplied.
- Offline progress synchronization, translations, email delivery, or full accessibility certification.

See README.md for implementation decisions, formulas, and MVP limits.
