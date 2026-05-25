---
name: personal-automation-maintenance
description: Maintain Codex automations on the personal computer. Use when reviewing, creating, or editing personal automation prompts, recurring runs, reminders, monitors, thread wakeups, or app-backed automation settings from this device's workflow review.
---

# Personal Automation Maintenance

## Overview

Keep personal automation changes explicit, reviewable, and verified. Treat automation prompt edits as live behavior changes: preserve existing schedule and destination details unless the user asks to change them.

## Workflow

1. Inspect the existing automation before changing it. Confirm the automation id, name, current prompt, schedule, destination, status, workspace, and environment if the app exposes them.
2. Update only the requested prompt text unless the user also asks for schedule, status, workspace, model, or environment changes.
3. After updating the automation prompt, ask the app to run the automation once manually if the app exposes a manual-run action or review prompt.
4. If manual run is unavailable, say that clearly and report the prompt update without inventing a workaround.
5. Stop and report any merge conflict, authentication failure, missing automation id, unavailable target automation, or uncertainty about overwriting another automation.

## Scope

- Work only on personal-device automation instructions unless the user explicitly approves a broader change.
- Prefer updating an existing automation over creating a duplicate.
- Do not force a run by changing the schedule, creating a temporary duplicate, or fabricating a one-off cron job.
- Do not expose raw RRULE strings to the user unless they explicitly ask for them.

## Reporting

When finished, report whether the prompt was updated and whether the one-time manual run was requested, unavailable, or blocked.
