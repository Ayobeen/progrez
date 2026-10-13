# Courses in Progrez

Progrez now provides a common learning platform for independent courses. The MVP ships with exactly two published courses:

| Course | Content | Starting point |
| --- | --- | --- |
| Data Analysis Foundations | Original 6 milestones, 12 lessons, and 12 checkpoints | Existing learner history is preserved |
| Introduction to Python | 6 milestones, 12 lessons, and 12 checkpoints | New course journey with its own goal and diagnostic |

## Learner flow

1. Register or sign in. The course library opens after login, including demo login.
2. Select a course. First-time learners set a goal and complete that course's diagnostic; returning learners continue their saved journey.
3. Use the existing Overview, My journey, Comeback, My progress, My Circle, and My profile screens within the selected course.
4. Select **Courses** or **Switch course** to open another journey. Progress in the previous course is retained.

Each course has separate goals, knowledge baseline, confidence, pace preferences, diagnostic results, lesson progress, assessment attempts, competencies, learning signals, momentum, friction events, recovery plans/results, and Circle settings/supporters/messages. Account identity, password, language, and timezone remain account-level settings. Intervention strategy templates are shared; selected content, baselines, and recovery evidence are course-specific.

Circle links belong to a particular course journey. Selecting Python does not grant its supporters access to Data Analysis, and making one journey private does not change the other. Existing Data Analysis profile links remain valid under their existing privacy settings. New course Circles start private. Notifications and optional email delivery use the course stored on the support relationship, independently of a learner's current selection.

## Python curriculum

| Milestone | Lessons |
| --- | --- |
| Python basics | Say hello with Python; Variables remember values |
| Values and input | Choose the right value type; Turn input into useful values |
| Making decisions | Compare and branch; Combine conditions thoughtfully |
| Repeating and collecting | Keep related values in a list; Repeat a useful step |
| Reusable functions | Name a reusable operation; Look up values by name |
| A reliable small program | Handle an expected error; Build a study-session summary |

Lessons use Python 3 examples and original explanations. Code indentation is preserved. Checkpoints test code reasoning with server-scored answers; this MVP does not execute arbitrary Python, provide a code editor, or automatically grade independent projects. The same Comeback, momentum, and Circle tools operate on this course.

## Add another course

Course authoring is an administrator function. A user with the `core.add_course` permission sees **Add a course** in the library. It opens Django administration at `/admin/core/course/add/`. Use `python manage.py createsuperuser` to create an administrator if none exists; ordinary learner accounts are not promoted automatically.

1. Save a **draft Course** with its title, unique slug, description, category, level, sequence, suggested goal, success example, and diagnostic JSON. Keep Published unchecked.
2. Add its **Competencies**, choosing that course and globally unique codes of up to eight characters, such as `WR001`.
3. Add its **Milestones**, selecting the same course and matching competencies. Sequence numbers are unique within a course, so another course may also start at 1.
4. Add each **Mission** under a milestone, including lesson text and its sequence. In the mission editor, add the associated Assessment with checkpoint JSON. Use a 100-point maximum; the pass score may range from 1 to 100.
5. Return to the Course and check **Published**. Publication validates that every milestone has lesson content and checkpoints, that competencies match the course, and that diagnostic questions are complete.

The diagnostic requires at least two questions. All but its last question measure initial knowledge; the final question should provide a short explanation and ask the learner to apply it. Questions use this structure:

```json
[
  {
    "prompt": "Which word is simpler?",
    "options": ["Use", "Utilize"],
    "answer": 0,
    "explanation": "Use is shorter and familiar."
  },
  {
    "prompt": "Read: active voice names who performs an action. Which sentence does this?",
    "options": ["The report was written.", "Ada wrote the report."],
    "answer": 1,
    "explanation": "The second sentence identifies Ada as the person who wrote it."
  }
]
```

The answer index starts at zero. Checkpoints use the same question structure, with at least one question. Answer keys stay on the server. New published courses appear in the library without frontend code changes. Adding courses with different teaching formats, such as executable labs or video, would require additional implementation.

## Upgrade and operations

Back up the SQLite database before upgrading. Stop the old server, then run `migrate`, `seed_demo`, and `backfill_circle`; `start.ps1` performs these steps. The migration assigns all existing learning and Circle records to Data Analysis. Seeding adds Python content without resetting existing progress. Backfilling creates missing Circle profiles and historical achievements without sending retrospective notifications.

The in-app demo reset affects only the selected learner and course. The existing `seed_demo --reset` command restores the four Data Analysis demo journeys; other courses and real learners are retained. Momentum refresh and Circle backfill iterate course journeys independently.

`GET /api/v1/courses/` returns published course summaries and the current learner's progress. `POST /api/v1/courses/select/` accepts `{"slug":"introduction-to-python"}` and returns that course's state. Course-scoped requests carry `X-Progrez-Course`; browser URLs also retain the course for reload/back navigation. The explicit header takes precedence over the session so another tab cannot silently redirect an old form into a different course. Legacy calls without a selection fall back to Data Analysis. Shared Circle links resolve their own course from the stored profile/member rather than this header.

Future production work includes richer course authoring, content versioning for already-enrolled learners, and PostgreSQL deployment validation. The local implementation uses SQLite; no public hosting or live external services are configured by this update.
