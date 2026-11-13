from __future__ import annotations

import csv
import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from solution.core import connect, ingest, report
from solution.normalize import IST, date, decimal, header, phone, timestamp

SECRET = b"test-only-secret-0123456789abcdef-0123456789abcdef"


class NormalizeTests(unittest.TestCase):
    def test_header_and_phone(self):
        self.assertEqual(header("  MOBILE__No  "), "mobile no")
        for raw in ("9876543210", "+91 98765 43210", "09876543210", "91-98765-43210", "(+91) 9876543210"):
            self.assertEqual(phone(raw), "+919876543210")
        self.assertEqual(phone("९८७६५४३२१०"), "+919876543210")
        for raw in ("", "N/A", "5123456789", "+1 415 555 0134", "987654321"):
            self.assertIsNone(phone(raw))

    def test_dates_decimal_and_timestamps(self):
        for raw in ("2026-12-01", "01/12/2026", "01-12-2026", "01-dec-2026"):
            self.assertEqual(date(raw), "2026-12-01")
        self.assertIsNone(date("31/02/2026"))
        self.assertIsNone(date("01-Not-2026"))
        for raw in ("₹1,234.565", "Rs. 1234.565", "INR 1234.565"):
            self.assertEqual(decimal(raw), "1234.57")
        self.assertIsNone(decimal("-10"))
        self.assertEqual(timestamp("2026-10-05T04:30:00Z"), timestamp("2026-10-05 10:00:00"))
        self.assertEqual(timestamp("2026-10-05T10:00:00+05:30").astimezone(IST).hour, 10)


def event(event_id, kind, *, call="call1", phone_value="9876543210", code=None):
    value = {"event_id": event_id, "event_type": kind, "workflow_id": "motor_renewal",
             "call_id": call, "occurred_at": "2026-10-05T18:30:00Z", "payload": {}}
    if phone_value is not None:
        value["phone"] = phone_value
    if code:
        value["payload"]["disposition_code"] = code
    return json.dumps(value)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for number in (1, 2):
            (self.root / f"drop_{number}" / "leads" / "motor_renewal").mkdir(parents=True)
            (self.root / f"drop_{number}" / "events").mkdir()
        lead_header = "customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language\n"
        (self.root / "drop_1/leads/motor_renewal/batch_001.csv").write_text(lead_header, encoding="utf-8")
        (self.root / "drop_2/leads/motor_renewal/batch_003.csv").write_text(
            lead_header + "Private Person,9876543210,POL-1,31/02/2026,Rs. 123.456,\n",
            encoding="utf-8")
        lines = [event("e1", "call.initiated"),
                 event("e2", "disposition.set", code="callback_requested"),
                 event("e3", "disposition.override", phone_value=None, code="renewed"),
                 event("e4", "disposition.set", code="unknown_code"),
                 event("e1", "call.initiated"), "{bad json"]
        (self.root / "drop_1/events/events_0001.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        (self.root / "drop_2/events/events_1001.jsonl").write_text("", encoding="utf-8")

    def test_late_link_override_idempotency_and_pii(self):
        db_path = self.root / "first.sqlite"
        with closing(connect(db_path, SECRET)) as db:
            ingest(db, self.root / "drop_1", SECRET)
            report(db, self.root / "before")
            before = json.loads((self.root / "before/data_quality.json").read_text())
            self.assertEqual(before["orphan_events"], 4)
            ingest(db, self.root / "drop_2", SECRET)
            payload = json.loads(db.execute("SELECT payload_json FROM events WHERE event_id='e3'").fetchone()[0])
            self.assertEqual(payload, {"disposition_code": "renewed"})
            report(db, self.root / "after")
            names = ("batch_summary.csv", "daily_overview.csv", "lead_report.csv", "rejections.csv", "data_quality.json")
            original = {name: (self.root / "after" / name).read_bytes() for name in names}
            ingest(db, self.root / "drop_1", SECRET)
            ingest(db, self.root / "drop_2", SECRET)
            report(db, self.root / "after")
            self.assertEqual(original, {name: (self.root / "after" / name).read_bytes() for name in names})
        quality = json.loads((self.root / "after/data_quality.json").read_text())
        self.assertEqual((quality["events_total"], quality["malformed_events"],
                          quality["duplicate_events_dropped"], quality["orphan_events"],
                          quality["unknown_disposition_events"]), (6, 1, 1, 0, 1))
        self.assertEqual(quality["field_warnings"], 1)
        with (self.root / "after/lead_report.csv").open(newline="", encoding="utf-8") as stream:
            lead = next(csv.DictReader(stream))
        self.assertEqual((lead["bucket"], lead["current_disposition"], lead["ever_success"]),
                         ("completed", "renewed", "true"))
        self.assertEqual(lead["last_call_at"], "2026-10-06T00:00:00+05:30")
        self.assertEqual(lead["phone_masked"], "+91******3210")
        raw_db = db_path.read_bytes()
        self.assertNotIn(b"Private Person", raw_db)
        self.assertNotIn(b"9876543210", raw_db)
        with closing(connect(self.root / "all.sqlite", SECRET)) as db:
            ingest(db, self.root, SECRET)
            report(db, self.root / "all")
        self.assertEqual(original, {name: (self.root / "all" / name).read_bytes() for name in names})

    def test_rejection_order_and_duplicates(self):
        csv_path = self.root / "drop_2/leads/motor_renewal/batch_003.csv"
        csv_path.write_text("customer_name,phone,policy_number\n"
                            ",,\n"
                            ", , , \n"
                            ",5123456789,\n"
                            "Valid,5123456789,POL-2\n"
                            "Valid,9876543210,POL-3\n"
                            "Changed,09876543210,POL-4\n", encoding="utf-8")
        with closing(connect(self.root / "reject.sqlite", SECRET)) as db:
            ingest(db, self.root / "drop_2", SECRET)
            report(db, self.root / "reject")
        with (self.root / "reject/rejections.csv").open(newline="", encoding="utf-8") as stream:
            rejections = list(csv.DictReader(stream))
        self.assertEqual([(r["row_number"], r["reason"]) for r in rejections],
                         [("1", "missing_required:customer_name"), ("2", "invalid_phone")])
        quality = json.loads((self.root / "reject/data_quality.json").read_text())
        self.assertEqual(quality["lead_rows_total"], 4)
        self.assertEqual(quality["duplicate_lead_rows_merged"], 1)
        with (self.root / "reject/lead_report.csv").open(newline="", encoding="utf-8") as stream:
            lead = next(csv.DictReader(stream))
        self.assertEqual(lead["external_ref"], "POL-4")

    def test_bucket_precedence_and_distinct_daily_success(self):
        db_path = self.root / "facts.sqlite"
        with closing(connect(db_path, SECRET)) as db:
            leads = [("motor_renewal", f"token{i}", f"{i:04d}", "batch_001", f"REF-{i}", "{}") for i in range(4)]
            db.executemany("INSERT INTO leads VALUES (?, ?, ?, ?, ?, ?)", leads)
            rows = []
            def add(eid, index, call_id, kind, code=None, when="2026-10-05T04:00:00+00:00"):
                rows.append((eid, "motor_renewal", call_id, kind, when, f"token{index}", code))
            add("e1", 1, "fail", "call.initiated")
            add("e2", 1, "fail", "call.telephony_failed")
            add("e3", 2, "dial", "call.initiated")
            add("e4", 3, "success1", "call.initiated")
            add("e5", 3, "success1", "disposition.set", "renewed")
            add("e6", 3, "success2", "call.initiated", when="2026-10-05T05:00:00+00:00")
            add("e7", 3, "success2", "disposition.set", "renewed", when="2026-10-05T05:01:00+00:00")
            add("e8", 3, "success2", "call.telephony_failed")
            db.executemany("""INSERT INTO events
                (event_id, workflow_id, call_id, event_type, occurred_at, phone_token,
                 disposition_code) VALUES (?, ?, ?, ?, ?, ?, ?)""", rows)
            db.commit()
            report(db, self.root / "facts")
        with (self.root / "facts/lead_report.csv").open(newline="", encoding="utf-8") as stream:
            leads_out = {r["external_ref"]: r for r in csv.DictReader(stream)}
        self.assertEqual([leads_out[f"REF-{i}"]["bucket"] for i in range(4)],
                         ["yet_to_reach_out", "in_progress_telephony_failed", "in_progress_dialed", "completed"])
        with (self.root / "facts/daily_overview.csv").open(newline="", encoding="utf-8") as stream:
            daily = next(csv.DictReader(stream))
        self.assertEqual((daily["total_calls"], daily["success_leads"]), ("4", "1"))


if __name__ == "__main__":
    unittest.main()
