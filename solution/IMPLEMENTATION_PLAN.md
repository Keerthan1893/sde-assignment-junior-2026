# Assignment A implementation plan

## Goal and constraints

Build a Python 3.11+ CLI backed by SQLite. The documented interface will be:

```text
python -m solution ingest data/drop_1 --db data/ledger.sqlite
python -m solution ingest data/drop_2 --db data/ledger.sqlite
python -m solution report --db data/ledger.sqlite --out reports/
python -m unittest discover -s solution/tests -v
```

For routine use, `python -m solution run` performs both ingestion and reporting
with default input, database, and output paths.

Also accept `python -m solution ingest data --db data/ledger.sqlite` to ingest
all drops in sorted order. This provides an explicit all-at-once comparison.

Keep all provided assignment files unchanged. Put code and tests in `solution/`.
Use the generated data only locally. Complete the core requirements before considering
the large-data or SQL-only stretch goals.

## Proposed design

- `solution/core.py`: CLI, SQLite access, input checks, ingestion and reporting.
- `solution/schema.sql`: table and index definitions.
- `solution/normalize.py`: header, phone, date, decimal, enum and timestamp parsing.
- `solution/tests/`: focused unit and integration tests.

SQLite tables:

1. `leads`: unique `(workflow_id, phone_token)`, first `batch_id`, reference,
   optional non-PII fields, and phone's last four digits for the required mask.
   Do not persist customer names; they are only needed for validation.
2. `events`: unique `event_id`, workflow, call ID, type, UTC timestamp, phone token
   where present, and only the payload values needed for derived facts. Insert
   once and never update or delete; unknown dispositions remain recorded but
   excluded from effective-disposition calculations.
3. `rejections`: workflow, batch, nonblank row number and one reason. No source
   row or PII.
4. `ingested_files`: relative source path, SHA-256 content digest and counters
   for that file. Commit each file's records and receipt in one transaction.

Use HMAC-SHA256 of the canonical phone with a stable secret supplied through an
environment variable. A plain hash is weak because phone numbers can be guessed.
Never persist the raw phone, raw lead row, raw event line, or full JSON payload.
Document that the same secret must be supplied for every command using a DB.
Keep the secret outside the DB and repository. The last four digits suffice for
`phone_masked`; no encrypted name is needed for the reports.

Derive links at report time from `(workflow_id, phone_token)`; for a phone-less
override, resolve `(workflow_id, call_id)` through another event on that call.
This lets an earlier orphan become linked after a later lead upload without
modifying the event ledger. Evaluate event quality in the specified order:
malformed, duplicate, currently orphaned, then unknown disposition. Persist
malformed and duplicate counts; recompute orphan and unknown counts against the
current leads and disposition catalogue.

## Build order and acceptance checks

### 1. Set up and inspect input (about 30 minutes)

- Add package entry point and SQLite schema initialization.
- Generate a small seed locally; confirm file paths, headers and event shapes.
- Add a documented way to set the HMAC secret before running commands.
- Check `git status` so generated data and DB files stay untracked.

### 2. Normalize and ingest leads (about 1.5 hours)

- Match headers by the schema's names and aliases after lowercasing, trimming,
  and collapsing runs of underscores/whitespace.
- Skip fully blank rows. Count nonblank rows from 1; validate required fields
  in schema order and record only the first specified rejection reason.
- Normalize optional values; convert invalid optional values to null and count
  one warning per invalid field on accepted rows.
- Merge duplicate identities in drop/file/row order. Preserve the first batch;
  overwrite only with non-null incoming values. Apply defaults when reading
  final lead facts, so a later valid value can replace an earlier missing one.
- Tests: phone formats and invalid phones; all date formats and impossible
  dates; decimal prefixes, rounding and negatives; schema aliases, rejection
  order, blank rows, duplicate merge and warning counts.

### 3. Ingest events (about 1.5 hours)

- Process nonempty JSONL lines in filename order. Store each well-formed,
  first-seen `event_id` once, including events currently orphaned or carrying
  unknown dispositions. Do not store PII-bearing raw input.
- Normalize event phones and all timestamp forms; treat naive timestamps as
  IST and store a comparable UTC instant.
- Skip a file already ingested with the same digest. Fail clearly if the same
  relative file path now has different content; this avoids silent counter
  changes on re-ingest.
- Tests: malformed/duplicate order, cross-drop duplicates, event before lead,
  phone-less override before its call's other events, and idempotent re-ingest.

### 4. Derive facts and write reports (about 2 hours)

- Count only linked calls with a linked `call.initiated`. Use the initiation's
  IST date. Compute connected and telephony-failed flags across that call's
  linked events.
- Choose the latest valid override by time; otherwise the latest valid set.
  Keep original events untouched. For equal timestamps, use `event_id` as a
  documented deterministic tie-breaker.
- Derive lead buckets in the exact specified precedence, current disposition
  from the most recently initiated call that has one, and ever-success from
  any call's effective disposition.
- Write all five outputs with exact headers, sort keys, empty values, lowercase
  booleans, four-decimal connectivity rates and IST timestamps.
- Tests: effective disposition, bucket precedence, date boundary in IST,
  distinct daily success leads, latest-call selection and empty reports.

### 5. End-to-end verification and submission (about 1.5 hours)

- Run seed 42: drop 1, report; drop 2, report; re-ingest both, report again.
  The second report must be byte-identical after re-ingest.
- In a separate DB, ingest the `data` root in one command and compare all
  five report files. Run at least one additional seed to avoid seed-specific
  assumptions. Check that the SQLite DB has no plaintext names or full phones.
- Run `python -m unittest discover -s solution/tests -v`. Record exact commands and observed results.
- Fill `SUBMISSION.md` and `AI_USAGE.md` honestly, including the HMAC secret
  setup, schema, assumptions, test scope and any unfinished work.

## Decisions to document

- Required invalid non-phone fields have no rejection code in the supplied
  rules; current schemas only make strings and phones required. Do not invent
  an output reason without a case requiring one.
- For contradictory events on one call, use only events that resolve to the
  same workflow and lead; document this conflict policy.
- File receipts identify the same relative path across runs. The command
  should make the input root/path convention explicit so re-ingest is stable.
- Do not pursue performance optimization until the small-seed reports and
  restart/re-ingest invariants are correct.
