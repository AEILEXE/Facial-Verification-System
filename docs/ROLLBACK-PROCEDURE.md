# FANS-C Rollback Procedure

Use this when a version just deployed to a production server (an upgrade,
per `dev/V2.2.0-UPGRADE-CHECKLIST.md` or its equivalent for a future
release) needs to be reverted — a regression is found, or a deployment step
failed partway through. This procedure is version-agnostic; version-specific
notes for the current release are in the section at the bottom.

**Do this only after the pre-upgrade backups described in the upgrade
checklist's Section 1 were actually taken.** If they were not taken, stop
and assess data-loss risk before proceeding — do not guess.

---

## 1. Decide: full rollback or forward-fix?

- A cosmetic or non-blocking bug (wrong wording, a chart not rendering) is
  usually better **forward-fixed** in a follow-up build than rolled back —
  rollback re-exposes the server to any bugs the new version fixed.
- A **release-blocking** regression (verification broken, payout broken,
  login broken, data corruption) warrants an immediate rollback while a fix
  is prepared.

## 2. Stop the running application

```powershell
taskkill /IM fans_c.exe /F
```

If `taskkill` is denied (rare, permissions issue):

```powershell
wmic process where name='fans_c.exe' delete
```

Confirm the Task Scheduler tasks ("FANS-C Verification System", "FANS-C
Watchdog") are not immediately restarting the process — disable them
temporarily if needed:

```powershell
Disable-ScheduledTask -TaskName "FANS-C Verification System"
Disable-ScheduledTask -TaskName "FANS-C Watchdog"
```

## 3. Restore the database and `.env`

Using the backups taken immediately before the upgrade (per the upgrade
checklist Section 1):

```powershell
Copy-Item "db.sqlite3.backup-<timestamp>" db.sqlite3 -Force
Copy-Item ".env.backup-<timestamp>" .env -Force
```

If a PostgreSQL deployment (`USE_SQLITE=False`), restore from the
pre-upgrade dump instead using your standard `pg_restore` procedure.

**Do not skip this step even if the new version's migrations looked
non-destructive.** Migrations that ran between the old and new version are
part of what needs to be undone — restoring the DB file/dump is the only
reliable way to also undo the schema state, since Django does not ship
automatic reverse migrations for this project.

## 4. Reinstall the previous version

Run the previous version's installer (e.g. `FANS-C-Setup-v2.1.18.exe`).
The installer's upgrade path will detect the newer version is present and
downgrade the application files; the restored `db.sqlite3`/`.env` from Step
3 will match that version's expected schema.

If the previous version's installer is not readily available, restore the
previous version's install directory from a file-level backup instead (if
one was taken before the upgrade) rather than attempting a from-source
downgrade on a production machine.

## 5. Re-enable scheduled tasks and restart

```powershell
Enable-ScheduledTask -TaskName "FANS-C Verification System"
Enable-ScheduledTask -TaskName "FANS-C Watchdog"
```

Reboot the server, or start the tasks manually, and confirm the application
comes up on the previous version (check the version shown in the
System/About page).

## 6. Verify

- Log in with an existing account.
- Confirm beneficiary records and face embeddings are intact (verify one
  real beneficiary end-to-end).
- Confirm the most recent claims/payouts from before the rollback are still
  present — the DB restore in Step 3 rewinds to the pre-upgrade backup
  point, so anything recorded *during* the brief window the new version was
  live is lost. Cross-check against the audit log and, if the gap matters,
  manually re-enter anything that happened in that window.

## 7. Record the incident

Note in the deployment's own records: what version was rolled back from/to,
why, the timestamp of the restored backup (and therefore the data-loss
window from Step 6, if any), and who performed the rollback. This becomes
the input for the next release's fix.

---

## Version-specific notes

### Rolling back v2.2.0 → v2.1.18

> Note: neither "v2.2.0" nor "v2.1.18" was ever packaged as a real,
> separately released installer — both were internal development labels
> later folded into the v2.1.x release line (final release: v2.1.17). This
> appendix is kept as a historical record of the rollback reasoning used
> at the time; for the current release, see the general procedure above.

- All four v2.2.0 migrations (`logs/0012`, `logs/0013`,
  `verification/0021`, `verification/0022`) are additive/label-only and
  non-destructive going forward, but v2.1.18 code does not know about the
  `Notification.priority` field or `StipendEvent.late_approval_reason` —
  restoring the pre-upgrade `db.sqlite3` backup (Step 3) is what actually
  handles this; do not attempt to run v2.1.18 against an un-restored
  v2.2.0 database.
- No `.env` variables introduced in v2.2.0 are required by v2.1.18; restoring
  the pre-upgrade `.env` backup is sufficient and expected to work cleanly.
