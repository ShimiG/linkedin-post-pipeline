"""Cron job: publish the drafts whose `/schedule` time has come, then switch this workflow off when none are waiting.

    python -m poster.scheduled

`/schedule` switches the workflow back on, so it uses no Actions minutes while nothing is scheduled.
"""
import sys
from datetime import datetime, timezone

from . import gh, review
from .config import load_config
from .http import HttpError


def run(cfg, at):
    """Publish every scheduled version that is due at `at`. Returns (drafts still waiting, failed)."""
    waiting = failed = 0
    for issue in gh.issues("scheduled", state="open"):
        n, labels = issue["number"], {l["name"] for l in issue.get("labels", [])}
        if "draft" not in labels:
            continue
        times = gh.schedules(issue.get("body"))
        due = sorted(((when, lang) for lang, when in times.items() if when <= at), key=lambda d: d[0])
        if len(due) < len(times):
            waiting += 1
        try:
            if not times:
                review.clear_schedule(issue)
                gh.comment(n, ":warning: This draft was labelled `scheduled` but its time is gone (the hidden `publish-at` line "
                              "was removed from the issue text). Nothing was posted. " + review.SCHEDULE_CANCELLED)
                continue
            for when, lang in due:
                # Take the version off the schedule before posting: whatever goes wrong after this, it is never posted twice.
                review.clear_schedule(issue, lang)
                print(f"#{n}: {lang} scheduled for {when.isoformat()}, publishing")
                review.approve(cfg, issue, lang, scheduled=True)
        except Exception as e:  # keep going: one broken draft must not hold back the others
            failed += 1
            print(f"#{n}: failed: {e}")
    return waiting, failed


def switch_off():
    try:
        gh.set_workflow(gh.SCHEDULED_WORKFLOW, False)
        if gh.issues("scheduled", state="open"):  # a /schedule arrived while this run was finishing
            gh.set_workflow(gh.SCHEDULED_WORKFLOW, True)
        else:
            print("nothing scheduled: workflow switched off until the next /schedule")
    except HttpError as e:
        print(f"could not switch the workflow off ({e}); it will keep checking every 15 minutes")


def main():
    waiting, failed = run(load_config(), datetime.now(timezone.utc))
    print(f"{waiting} scheduled draft(s) still waiting")
    if not waiting:
        switch_off()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
