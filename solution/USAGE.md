# Assignment A: run instructions

## Why the HMAC key is needed

The assignment requires phone numbers to be protected in the database while
call events still match the correct lead. The pipeline normalizes each phone,
then combines it with the secret key to make a repeatable HMAC token. The same
phone and key make the same token, so matching works without storing the full
phone. A plain hash would be easier to reverse by trying possible phone
numbers. Keep the key stable: a new key creates different tokens and cannot
match phones already stored in an existing database.

## One-time local setup

The solution uses only the Python standard library (Python 3.11 or newer).
From the repository root on Windows:

```powershell
python -m venv .venv
# First run only: generate a key and save the printed value privately.
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
# Paste that value once when the following command prompts you.
$env:AU_PHONE_HMAC_KEY = Read-Host 'Enter HMAC key'
.\.venv\Scripts\python.exe -c "import os; assert len(os.environ.get('AU_PHONE_HMAC_KEY', '')) >= 32; print('HMAC key ready')"
.\.venv\Scripts\python.exe assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data
```

The generator creates synthetic input files for the exercise. In normal use,
the file drops are supplied to the pipeline; you do not generate them on every
run. Set `AU_PHONE_HMAC_KEY` again in each new shell unless your environment
already supplies it.

## Run the pipeline

Once the input files and environment variable are ready, **one command** runs
the pipeline:

```powershell
.\.venv\Scripts\python.exe -m solution run
```

The command prints the total time for ingestion and report generation. It
does not include one-time setup or sample-data generation. Use a fresh `--db`
path to time ingestion; an existing DB skips files already ingested.

To run its tests separately:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s solution/tests -v
```

`run` ingests both drops in order and writes all five reports. Its defaults are
input `data/`, database `data/ledger.sqlite`, and output `reports/`. To see an
intermediate report, use `python -m solution ingest data/drop_1` followed by
`python -m solution report`, then repeat for `data/drop_2`. The `--db` and
`--out` options remain available when you need another path.

Generate the key only once and save it privately outside this repository. In
later PowerShell sessions, skip the generation line and paste the **same** key
when `Read-Host` prompts you. Do not put the key after `Read-Host` on the
command line: that text would become the prompt itself. If your IDE or
deployment already supplies `AU_PHONE_HMAC_KEY`, skip the `Read-Host` line.
The application reads only the environment variable; a `.env` file is not
loaded automatically. Never commit the key. In production, a secret manager
should inject it into the process environment.

If you already created a database with a different key, use a new `--db` path.

On Linux or macOS, supply the saved key in the current shell:

```bash
python3 -m venv .venv
read -r -s -p 'Paste your saved HMAC key: ' AU_PHONE_HMAC_KEY
export AU_PHONE_HMAC_KEY
.venv/bin/python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data
.venv/bin/python -m solution run
.venv/bin/python -m unittest discover -s solution/tests -v
```

The same key must be used for every run against an existing database. A newly
generated key requires a fresh database.

To ingest all drops in one command into a fresh DB:

```powershell
.\.venv\Scripts\python.exe -m solution ingest data --db data/all.sqlite
.\.venv\Scripts\python.exe -m solution report --db data/all.sqlite --out data/all_reports
```

Do not regenerate a different seed into the same path and use the old database:
ingested file receipts deliberately reject changed file contents. Use a fresh
DB and output directory for a different seed.

The generated `data/` tree, SQLite databases and `reports/` are gitignored.
