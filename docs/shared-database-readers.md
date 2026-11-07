# Two installations on one database: who reads which document

A document that is uploaded is saved in the storage of the installation that received it (its `STORAGE_DIR` or bucket)
and queued in `document_extraction_jobs`. When two installations share one database but not their storage (a local
development stack and a deployed copy, say), a reading worker of one can claim the queued job of the other, cannot find
the file, and fails it.

What the code now does (migration `0031_job_instance`):

* Every new job is stamped with `instance`: `INSTANCE_NAME` if set, otherwise the host name plus a hash of the resolved
  storage location.
* A reading worker claims only jobs stamped with its own name, and older jobs with no stamp.
* A claimed job whose file is not in this server's storage is handed back at once: no attempt is counted, it is never marked
  failed, and its reason reads "This file is not in this server's storage...". A missing file at read time is treated the same.
* `python -m app.diag_extraction` prints this installation's name, the `instance` of each job, and flags a job stamped by
  another installation or handled by older code. A failed attempt keeps `state.failure` (exception type and place, no content).

The limit: code that predates the column ignores it. While an older installation (for example a deployed copy that has not
been updated) shares the database, its workers still claim any pending job, including this one's, and fail it for lack of
the file. Update the other installation, or give local work its own database.

## Keeping this installation's jobs out of sight of older readers (migration `0032_job_ready_at`)

An older reader claims every job with `status IN ('pending','processing','retry') AND next_attempt_at <= now()`. It cannot
be told to skip a job, so the job is given nothing to find. For a job stamped with an `instance`, a trigger on
`document_extraction_jobs` keeps the real due time in the new `ready_at` column and stores `2100-01-01` in `next_attempt_at`
(a concrete timestamp every driver reads; `infinity` is avoided on purpose). Whatever writes `next_attempt_at` (the API,
the worker, an older API or reader) is converted by the trigger, so no writer has to know about it.

* Current readers claim by `COALESCE(ready_at, next_attempt_at)`; queue position, retry back-off, leases and the wake-up
  notification work as before.
* Rows with no `instance` (written by older code) are never touched: `ready_at` stays NULL and their own `next_attempt_at` rules.
* `python -m app.diag_extraction` says "hidden from older readers" for a stamped job, and warns when a stamped job is still visible
  (the database update has not been run).
* Apply the update (migrations 0031 and 0032) to the shared database before relying on this. An older reader's own
  jobs, and any job it already holds, are not affected.
