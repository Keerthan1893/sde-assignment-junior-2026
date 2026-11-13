# Submission

## Candidate

- **Name:** Keerthan V
- **Assignment chosen:** A (Data)
- **Time spent (honest estimate):** 7 hours

## How to run

From a clean clone, in one PowerShell session (Python 3.11+):

```powershell
# Set up Python and synthetic test input (once)
python -m venv .venv
venv\Scripts\Activate
venv\Scripts\python -c "import secrets; print(secrets.token_hex(32))"
$env:AU_PHONE_HMAC_KEY = Read-Host 'Enter HMAC key; paste the generated value once'
venv\Scripts\python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data
# Run the pipeline (one command)
venv\Scripts\python -m solution run
# Run tests separately
venv\Scripts\python -m unittest discover -s solution/tests -v
```

Generate a random key once and save it in a password manager; reuse it for
every command against the same DB. If the environment already supplies
`AU_PHONE_HMAC_KEY`, skip `Read-Host`. `run` ingests both drops and writes all
five reports using `data/ledger.sqlite` and `reports/`. See `solution/USAGE.md`
for the separate-drop commands and non-Windows setup.
Assignment A uses only the Python standard library, so
`pip install -r requirements.txt` is unnecessary for these commands; the listed
packages support Assignment B's mock provider and optional pytest.

## Design

SQLite stores leads, an immutable event ledger, rejections and file receipts.
The table definitions are in `solution/schema.sql` and are applied by
`solution/core.py` when the database is opened.
`leads` is unique on `(workflow_id, phone_token)`; `batch_id` is set on first
insert and never changed. Non-null values from later valid rows overwrite the
corresponding fields. `events.event_id` is unique, so duplicates cannot enter
the ledger. Each source file's records, metrics and receipt commit in one
transaction. The ledger retains the specified event payload fields needed to
explain a call, while excluding raw phone values and unrecognized payload keys.

The token is HMAC-SHA256 of the canonical phone using a secret supplied through
`AU_PHONE_HMAC_KEY`; the database stores no full phone or customer name. The
key makes phone matching repeatable while making a simple phone-number guessing
attack harder than it would be with an unkeyed hash. The last four phone digits
support the required masked report. Raw lead rows and
event JSON are not persisted. Reports resolve ordinary events by phone token
and phone-less overrides by `(workflow_id, call_id)`, so an event that arrived
before its lead becomes linked later without changing the ledger.

Reports derive calls from linked `call.initiated` events, use IST dates, select
the latest valid override over any set disposition, and apply the documented
bucket precedence. All five outputs are generated from the current database.

## Decisions & assumptions

| Decision | Why | Alternative considered |
|---|---|---|
| Keep orphan and unknown-disposition events in the ledger | Future leads may resolve orphans; their IDs must still dedupe | Discarding them would lose history |
| Resolve event links when reporting | Preserves append-only events and updates orphan counts after new leads | Mutating an event's lead ID |
| Use the event ID to break equal-timestamp disposition ties | Makes results deterministic when the specification does not decide ties | Input order |
| Round decimal values with `ROUND_HALF_UP` | The spec asks for two decimals but does not name a rounding mode | Banker's rounding |
| Reject a changed file at an already ingested path | Prevents silent changes to metrics and merge order | Reprocess and replace old rows |
| Treat conflicting lead identities on one call as unresolvable for a phone-less override | Avoids assigning an override to the wrong lead | Arbitrarily picking one lead |
| Use the earliest linked initiation if a call has several | The generator emits one, but this gives deterministic behavior | Arbitrarily picking one |

The supplied schemas have no required date, decimal or enum fields; the stated
rejection reasons therefore cover all generated required-field failures.

## Failure modes considered

- Re-ingesting an unchanged file is a no-op; changed contents at the same path
  produce an error. A crash during a file rolls back that file's transaction.
- Malformed JSON lines are counted without stopping ingestion. Duplicate event
  IDs are counted and skipped, even across drops.
- Orphans and unknown dispositions are classified against the current leads and
  catalogue. An override can resolve after its other call events arrive.
- Missing or changed HMAC keys fail clearly. The key must be retained outside
  the repository; losing it makes the existing DB unusable.
- The current report implementation loads event rows into memory. I have not
  measured or tuned the optional large-data stretch target.

## Testing

`python -m unittest discover -s solution/tests -v` covers normalization,
rejection order, duplicate merging, late lead arrival, phone-less overrides,
idempotency, PII absence in the DB file, bucket precedence, distinct daily
success leads, and stepwise versus all-at-once output identity. I also ran the
CLI on generated seeds 42 and 777 and compared all five outputs byte for byte
between those ingestion modes. No external expected-output oracle is supplied.

## Results

- Seed 42: 2,057 nonblank lead rows, 108 rejections, 57 merged duplicate rows,
  8,745 event lines, 462 final orphan events. All five report files produced.
- Seed 777: 2,059 nonblank lead rows, 94 rejections, 59 merged duplicate rows,
  9,056 event lines, 428 final orphan events. All five report files produced.
- Both seeds: stepwise and all-at-once reports matched byte for byte; re-ingest
  left reports unchanged. Five tests passed.
- Timing: `python -m solution run` reports its own elapsed time using
  `time.perf_counter()`. On a fresh seed 42 small-scale database, Codex observed
  0.465 seconds for ingestion and reporting (Python 3.12.3; excludes process
  startup and data generation). I should repeat this on my own machine before
  submitting.

## If I had another day

1. Measure and optimize the optional large dataset, especially report memory.
2. Add a production key-management and backup/recovery runbook.
