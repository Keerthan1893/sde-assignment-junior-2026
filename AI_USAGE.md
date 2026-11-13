# AI Usage Disclosure

## Tools used

Codex (ChatGPT).

## What I used them for

I asked Codex to review both assignments, recommend one, inspect the repository,
create an implementation plan, implement Assignment A, add tests and draft the
run and submission documentation. Codex produced the current solution code.

## What I wrote or decided myself

I chose to proceed with Assignment A and requested the complete implementation.
The code and detailed design were drafted by Codex. Before submitting, I need
to review the implementation and ensure I can explain and modify it live.

## Where the AI was wrong or unhelpful

The first test run failed on Windows because Codex used SQLite connection
context managers without explicitly closing the connections. The temporary
directory cleanup exposed the open handles; Codex changed the code and tests to
close them, then reran the tests successfully. An experimental Windows key
helper also failed across Windows security contexts; at my request, Codex
removed it and kept the application on a standard environment variable
supplied by the user or deployment. A glossary review then found that the
first ledger version dropped call duration and override reasons; Codex added
safe payload storage and confirmed that all five reports stayed identical.

## How I verified AI-generated code

Codex ran five unit/integration tests and generated seeds 42 and 777. For each
seed, it compared all five reports after incremental and all-at-once ingestion;
the files matched byte for byte. It also tested re-ingest idempotency and scanned
a test DB for plaintext name/full-phone bytes. I still need to review the code
myself before claiming personal verification in the interview.
