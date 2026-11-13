

from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import json
import os
import sqlite3
import time
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path

from .normalize import IST, header, normalize_field, phone, timestamp

SCHEMAS = Path(__file__).resolve().parents[1] / "assignment-a-data" / "schemas"
CSV_HEADERS = {
    "batch_summary.csv": ["workflow_id", "batch_id", "total_leads", "yet_to_reach_out", "in_progress_telephony_failed", "in_progress_dialed", "completed"],
    "daily_overview.csv": ["workflow_id", "date", "total_calls", "connected_calls", "connectivity_rate", "success_leads"],
    "lead_report.csv": ["workflow_id", "external_ref", "batch_id", "phone_masked", "bucket", "total_calls", "connected_calls", "last_call_at", "current_disposition", "ever_success"],
    "rejections.csv": ["workflow_id", "batch_id", "row_number", "reason"],
}
PAYLOAD_FIELDS = {
    "call.telephony_failed": ("reason",),
    "call.ended": ("duration_sec", "end_reason"),
    "disposition.set": ("disposition_code",),
    "disposition.override": ("disposition_code", "overridden_by", "reason"),
}


def load_schemas() -> dict:
    result = {}
    for path in SCHEMAS.glob("*.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        result[schema["workflow_id"]] = schema
    return result


def load_dispositions() -> dict:
    with (SCHEMAS / "dispositions.csv").open(newline="", encoding="utf-8") as stream:
        return {(r["workflow_id"], r["code"]): {
            "terminal": r["is_terminal"] == "true", "success": r["is_success"] == "true"
        } for r in csv.DictReader(stream)}


def secret_from_env() -> bytes:
    value = os.environ.get("AU_PHONE_HMAC_KEY", "")
    if len(value) < 32:
        raise ValueError("Set AU_PHONE_HMAC_KEY to a stable random secret of at least 32 characters")
    return value.encode("utf-8")


def token(secret: bytes, canonical_phone: str) -> str:
    return hmac.new(secret, canonical_phone.encode("ascii"), hashlib.sha256).hexdigest()


def connect(db_path: Path, secret: bytes) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
    if "payload_json" not in {r["name"] for r in db.execute("PRAGMA table_info(events)")}:
        db.execute("ALTER TABLE events ADD COLUMN payload_json TEXT NOT NULL DEFAULT '{}'")
        db.commit()
    verifier = hmac.new(secret, b"assignment-a-key-check", hashlib.sha256).hexdigest()
    old = db.execute("SELECT value FROM metadata WHERE name='key_check'").fetchone()
    if old and not hmac.compare_digest(old["value"], verifier):
        db.close()
        raise ValueError("AU_PHONE_HMAC_KEY does not match the key used for this database")
    if not old:
        db.execute("INSERT INTO metadata VALUES ('key_check', ?)", (verifier,))
        db.commit()
    return db


def source_receipt(db: sqlite3.Connection, path: Path) -> tuple[str, str, bool]:
    source = str(path.resolve())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    old = db.execute("SELECT content_sha256 FROM ingested_files WHERE source_path=?", (source,)).fetchone()
    if old and old["content_sha256"] != digest:
        raise ValueError(f"Already ingested path changed contents: {path}")
    return source, digest, bool(old)


def ingest_leads(db: sqlite3.Connection, path: Path, workflow: str, schema: dict, secret: bytes) -> None:
    source, digest, done = source_receipt(db, path)
    if done:
        return
    batch = path.stem
    fields = schema["fields"]
    reference = next(f["name"] for f in fields if f.get("reference"))
    defaults = {f["name"]: f["default"] for f in fields if "default" in f}
    metrics = Counter()
    with path.open(newline="", encoding="utf-8-sig") as stream, db:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None:
            raise ValueError(f"Missing CSV header: {path}")
        aliases = {header(alias): f["name"] for f in fields for alias in [f["name"], *f.get("aliases", [])]}
        columns = {}
        for raw in reader.fieldnames:
            if raw is not None and header(raw) in aliases:
                name = aliases[header(raw)]
                if name in columns:
                    raise ValueError(f"Two columns map to {name} in {path}")
                columns[name] = raw
        row_number = 0
        for raw_row in reader:
            cells = (cell for value in raw_row.values()
                     for cell in (value if isinstance(value, list) else [value]))
            if all(not str(cell or "").strip() for cell in cells):
                continue
            row_number += 1
            metrics["lead_rows_total"] += 1
            values = {}
            failure = None
            warnings = 0
            for field in fields:
                name = field["name"]
                raw = str(raw_row.get(columns[name]) or "").strip() if name in columns else ""
                if not raw:
                    if field.get("required"):
                        failure = f"missing_required:{name}"
                        break
                    values[name] = None
                    continue
                normalized = normalize_field(raw, field)
                if normalized is None:
                    if field["type"] == "phone":
                        if field.get("required"):
                            failure = "invalid_phone"
                            break
                    elif field.get("required"):
                        # Current schemas have no required date, decimal, or enum.
                        failure = f"invalid_{field['type']}:{name}"
                        break
                    else:
                        warnings += 1
                values[name] = normalized
            if failure:
                db.execute("INSERT INTO rejections VALUES (?, ?, ?, ?, ?)",
                           (source, workflow, batch, row_number, failure))
                continue
            metrics["field_warnings"] += warnings
            canonical = values.pop("phone")
            external_ref = values.pop(reference)
            values = {f["name"]: values.get(f["name"]) for f in fields if not f.get("pii") and f["name"] != reference}
            phone_token = token(secret, canonical)
            existing = db.execute("SELECT fields_json FROM leads WHERE workflow_id=? AND phone_token=?",
                                  (workflow, phone_token)).fetchone()
            if existing:
                metrics["duplicate_lead_rows_merged"] += 1
                updates = {k: v for k, v in values.items() if v is not None}
                old_fields = json.loads(existing["fields_json"])
                old_fields.update(updates)
                for name, default in defaults.items():
                    if old_fields.get(name) is None:
                        old_fields[name] = default
                # Reference is required and therefore always a non-null update.
                db.execute("UPDATE leads SET external_ref=?, fields_json=? WHERE workflow_id=? AND phone_token=?",
                           (external_ref, json.dumps(old_fields), workflow, phone_token))
            else:
                for name, default in defaults.items():
                    if values.get(name) is None:
                        values[name] = default
                db.execute("INSERT INTO leads VALUES (?, ?, ?, ?, ?, ?)",
                           (workflow, phone_token, canonical[-4:], batch, external_ref, json.dumps(values)))
        db.execute("INSERT INTO ingested_files (source_path, content_sha256, lead_rows_total, duplicate_lead_rows_merged, field_warnings) VALUES (?, ?, ?, ?, ?)",
                   (source, digest, metrics["lead_rows_total"], metrics["duplicate_lead_rows_merged"], metrics["field_warnings"]))


def ingest_events(db: sqlite3.Connection, path: Path, secret: bytes) -> None:
    source, digest, done = source_receipt(db, path)
    if done:
        return
    metrics = Counter()
    with path.open(encoding="utf-8") as stream, db:
        for line in stream:
            if not line.strip():
                continue
            metrics["events_total"] += 1
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                metrics["malformed_events"] += 1
                continue
            if not isinstance(event, dict) or not event.get("event_id"):
                metrics["malformed_events"] += 1
                continue
            event_id = str(event["event_id"])
            if db.execute("SELECT 1 FROM events WHERE event_id=?", (event_id,)).fetchone():
                metrics["duplicate_events_dropped"] += 1
                continue
            canonical = phone(str(event.get("phone") or ""))
            phone_token = token(secret, canonical) if canonical else None
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            try:
                occurred = timestamp(str(event.get("occurred_at"))).isoformat()
            except (ValueError, TypeError):
                occurred = None
            safe_payload = {name: payload[name] for name in PAYLOAD_FIELDS.get(event.get("event_type"), ())
                            if name in payload}
            db.execute("""INSERT INTO events
                (event_id, workflow_id, call_id, event_type, occurred_at, phone_token,
                 disposition_code, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                       (event_id, event.get("workflow_id"), event.get("call_id"), event.get("event_type"),
                        occurred, phone_token, payload.get("disposition_code"), json.dumps(safe_payload)))
        db.execute("INSERT INTO ingested_files (source_path, content_sha256, events_total, malformed_events, duplicate_events_dropped) VALUES (?, ?, ?, ?, ?)",
                   (source, digest, metrics["events_total"], metrics["malformed_events"], metrics["duplicate_events_dropped"]))


def drop_paths(path: Path) -> list[Path]:
    if (path / "leads").is_dir() and (path / "events").is_dir():
        return [path]
    drops = sorted(p for p in path.iterdir() if p.is_dir() and (p / "leads").is_dir() and (p / "events").is_dir())
    if not drops:
        raise ValueError(f"No drop directories found under {path}")
    return drops


def ingest(db: sqlite3.Connection, input_path: Path, secret: bytes) -> None:
    schemas = load_schemas()
    for drop in drop_paths(input_path):
        lead_paths = sorted((drop / "leads").glob("*/*.csv"), key=lambda p: (p.name, str(p)))
        for path in lead_paths:
            workflow = path.parent.name
            if workflow not in schemas:
                raise ValueError(f"Unknown workflow directory: {workflow}")
            ingest_leads(db, path, workflow, schemas[workflow], secret)
        for path in sorted((drop / "events").glob("*.jsonl")):
            ingest_events(db, path, secret)


def linked_events(db: sqlite3.Connection) -> tuple[list[dict], int, int]:
    leads = {(r["workflow_id"], r["phone_token"]) for r in db.execute("SELECT workflow_id, phone_token FROM leads")}
    events = [dict(r) for r in db.execute("SELECT * FROM events")]
    call_leads = defaultdict(set)
    for event in events:
        key = (event["workflow_id"], event["phone_token"])
        if event["event_type"] != "disposition.override" and key in leads and event["call_id"]:
            call_leads[(event["workflow_id"], event["call_id"])].add(key)
    dispositions = load_dispositions()
    linked = []
    orphan_count = unknown_count = 0
    for event in events:
        key = (event["workflow_id"], event["phone_token"])
        if event["event_type"] == "disposition.override":
            candidates = call_leads[(event["workflow_id"], event["call_id"])]
            key = next(iter(candidates)) if len(candidates) == 1 else None
        elif key not in leads:
            key = None
        if key is None:
            orphan_count += 1
            continue
        if str(event["event_type"] or "").startswith("disposition.") and (event["workflow_id"], event["disposition_code"]) not in dispositions:
            unknown_count += 1
            continue
        event["lead_key"] = key
        linked.append(event)
    return linked, orphan_count, unknown_count


def call_facts(events: list[dict]) -> dict:
    grouped = defaultdict(list)
    for event in events:
        if event["call_id"]:
            grouped[(event["lead_key"], event["call_id"])].append(event)
    result = defaultdict(list)
    for (lead_key, call_id), group in grouped.items():
        initiated = [e for e in group if e["event_type"] == "call.initiated" and e["occurred_at"]]
        if not initiated:
            continue
        start = min(e["occurred_at"] for e in initiated)
        overrides = [e for e in group if e["event_type"] == "disposition.override" and e["occurred_at"]]
        sets = [e for e in group if e["event_type"] == "disposition.set" and e["occurred_at"]]
        choices = overrides if overrides else sets
        effective = max(choices, key=lambda e: (e["occurred_at"], e["event_id"]))["disposition_code"] if choices else None
        result[lead_key].append({"call_id": call_id, "start": start,
                                 "connected": any(e["event_type"] == "call.connected" for e in group),
                                 "telephony_failed": any(e["event_type"] == "call.telephony_failed" for e in group),
                                 "disposition": effective})
    return result


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def report(db: sqlite3.Connection, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    linked, orphans, unknown = linked_events(db)
    calls_by_lead = call_facts(linked)
    dispositions = load_dispositions()
    schemas = load_schemas()
    batches = defaultdict(Counter)
    daily = defaultdict(lambda: {"total_calls": 0, "connected_calls": 0, "success_leads": set()})
    lead_rows = []
    for lead in db.execute("SELECT * FROM leads"):
        workflow = lead["workflow_id"]
        lead_key = (workflow, lead["phone_token"])
        calls = calls_by_lead[lead_key]
        connected = sum(c["connected"] for c in calls)
        terminal = any(dispositions.get((workflow, c["disposition"]), {}).get("terminal") for c in calls)
        success = any(dispositions.get((workflow, c["disposition"]), {}).get("success") for c in calls)
        if not calls:
            bucket = "yet_to_reach_out"
        elif terminal:
            bucket = "completed"
        elif all(c["telephony_failed"] for c in calls):
            bucket = "in_progress_telephony_failed"
        else:
            bucket = "in_progress_dialed"
        summary = batches[(workflow, lead["batch_id"])]
        summary["total_leads"] += 1
        summary[bucket] += 1
        latest = max(calls, key=lambda c: (c["start"], c["call_id"])) if calls else None
        with_disposition = [c for c in calls if c["disposition"]]
        current = max(with_disposition, key=lambda c: (c["start"], c["call_id"]))["disposition"] if with_disposition else ""
        lead_rows.append({"workflow_id": workflow, "external_ref": lead["external_ref"],
                          "batch_id": lead["batch_id"], "phone_masked": "+91******" + lead["phone_last4"],
                          "bucket": bucket, "total_calls": len(calls), "connected_calls": connected,
                          "last_call_at": timestamp(latest["start"]).astimezone(IST).isoformat() if latest else "",
                          "current_disposition": current, "ever_success": "true" if success else "false"})
        for call in calls:
            day = timestamp(call["start"]).astimezone(IST).date().isoformat()
            item = daily[(workflow, day)]
            item["total_calls"] += 1
            item["connected_calls"] += int(call["connected"])
            if dispositions.get((workflow, call["disposition"]), {}).get("success"):
                item["success_leads"].add(lead_key)
    batch_rows = [{"workflow_id": wf, "batch_id": batch,
                   **{name: counts[name] for name in CSV_HEADERS["batch_summary.csv"][2:]}}
                  for (wf, batch), counts in sorted(batches.items())]
    daily_rows = [{"workflow_id": wf, "date": day, "total_calls": item["total_calls"],
                   "connected_calls": item["connected_calls"],
                   "connectivity_rate": f"{item['connected_calls'] / item['total_calls']:.4f}",
                   "success_leads": len(item["success_leads"])}
                  for (wf, day), item in sorted(daily.items())]
    rejection_rows = [dict(r) for r in db.execute("SELECT workflow_id, batch_id, row_number, reason FROM rejections ORDER BY workflow_id, batch_id, row_number")]
    for name, rows in (("batch_summary.csv", batch_rows), ("daily_overview.csv", daily_rows),
                       ("lead_report.csv", sorted(lead_rows, key=lambda r: (r["workflow_id"], r["external_ref"], r["batch_id"], r["phone_masked"]))),
                       ("rejections.csv", rejection_rows)):
        write_csv(out / name, CSV_HEADERS[name], rows)
    sums = db.execute("""SELECT COALESCE(SUM(lead_rows_total), 0) AS lead_rows_total,
        COALESCE(SUM(duplicate_lead_rows_merged), 0) AS duplicate_lead_rows_merged,
        COALESCE(SUM(field_warnings), 0) AS field_warnings,
        COALESCE(SUM(events_total), 0) AS events_total,
        COALESCE(SUM(malformed_events), 0) AS malformed_events,
        COALESCE(SUM(duplicate_events_dropped), 0) AS duplicate_events_dropped
        FROM ingested_files""").fetchone()
    quality = {
        "lead_rows_total": sums["lead_rows_total"],
        "lead_rows_rejected": len(rejection_rows),
        "rejections_by_reason": dict(sorted(Counter(r["reason"] for r in rejection_rows).items())),
        "duplicate_lead_rows_merged": sums["duplicate_lead_rows_merged"],
        "field_warnings": sums["field_warnings"],
        "events_total": sums["events_total"],
        "malformed_events": sums["malformed_events"],
        "duplicate_events_dropped": sums["duplicate_events_dropped"],
        "orphan_events": orphans,
        "unknown_disposition_events": unknown,
    }
    (out / "data_quality.json").write_text(json.dumps(quality, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Assignment A interaction ledger")
    sub = parser.add_subparsers(dest="command", required=True)
    ingest_parser = sub.add_parser("ingest", help="Ingest one drop or a directory of drops")
    ingest_parser.add_argument("input", type=Path)
    ingest_parser.add_argument("--db", type=Path, default=Path("data/ledger.sqlite"))
    report_parser = sub.add_parser("report", help="Write the five contracted reports")
    report_parser.add_argument("--db", type=Path, default=Path("data/ledger.sqlite"))
    report_parser.add_argument("--out", type=Path, default=Path("reports"))
    run_parser = sub.add_parser("run", help="Ingest all drops and write reports")
    run_parser.add_argument("input", nargs="?", type=Path, default=Path("data"))
    run_parser.add_argument("--db", type=Path, default=Path("data/ledger.sqlite"))
    run_parser.add_argument("--out", type=Path, default=Path("reports"))
    args = parser.parse_args()
    try:
        started = time.perf_counter() if args.command == "run" else None
        secret = secret_from_env()
        if args.command == "report" and not args.db.is_file():
            raise ValueError(f"Database does not exist: {args.db}")
        with closing(connect(args.db, secret)) as db:
            if args.command in ("ingest", "run"):
                ingest(db, args.input, secret)
            if args.command in ("report", "run"):
                report(db, args.out)
        if args.command == "run":
            print(f"Reports written to {args.out} in {time.perf_counter() - started:.3f} seconds")
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f"error: {exc}\n")

