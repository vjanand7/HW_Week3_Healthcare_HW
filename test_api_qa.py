"""QA suite for the Diabetes Readmission Intervention Queue API.

Isolation: every test runs inside a throw-away PostgreSQL schema created from
schema.sql and dropped at the end, so the `public` schema (and any real data)
is never touched.

Needs a reachable PostgreSQL and DATABASE_URL (environment variable or .env).
Run from this folder:   python -m unittest test_api_qa -v
Skip the slow full-dataset tests:  set SKIP_REAL_DATA=1
"""
import contextlib
import io
import json
import os
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import psycopg
from fastapi.testclient import TestClient

import database
import service
from main import app

HERE = Path(__file__).resolve().parent
SCHEMA_SQL = (HERE / "schema.sql").read_text(encoding="utf-8")
CSV_PATH = HERE / "data" / "diabetic_data.csv"
BASE_URL = database.DATABASE_URL
SCHEMA = "qa_hc_" + uuid.uuid4().hex[:8]
SCOPED_URL = None
_patcher = None


def _scoped(url):
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}options=-csearch_path%3D{SCHEMA}"


def setUpModule():
    global SCOPED_URL, _patcher
    if not BASE_URL:
        raise unittest.SkipTest("DATABASE_URL is not set")
    try:
        with psycopg.connect(BASE_URL, autocommit=True) as conn:
            conn.execute(f"CREATE SCHEMA {SCHEMA}")
    except psycopg.Error as error:
        raise unittest.SkipTest(f"PostgreSQL not available: {error}")
    SCOPED_URL = _scoped(BASE_URL)
    with psycopg.connect(SCOPED_URL, autocommit=True) as conn:
        conn.execute(SCHEMA_SQL)
    _patcher = patch.object(database, "DATABASE_URL", SCOPED_URL)
    _patcher.start()


def tearDownModule():
    if _patcher:
        _patcher.stop()
    if SCOPED_URL:
        with psycopg.connect(BASE_URL, autocommit=True) as conn:
            conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")


def db():
    return psycopg.connect(SCOPED_URL, autocommit=True)


def reset_tables():
    with db() as conn:
        conn.execute("TRUNCATE care_interventions, patient_encounters RESTART IDENTITY CASCADE")


def add_encounter(encounter_id, level="LOW", score=0, readmitted="NO", **extra):
    row = {
        "encounter_id": str(encounter_id), "patient_nbr": f"P{encounter_id}", "age": "[50-60)",
        "time_in_hospital": 3, "number_inpatient": 0, "number_emergency": 0,
        "number_outpatient": 0, "num_medications": 5, "num_lab_procedures": 40,
        "discharge_disposition_id": 1, "admission_type_id": 1,
        "readmitted": readmitted, "risk_score": score, "risk_level": level,
    }
    row.update(extra)
    cols = ", ".join(row)
    marks = ", ".join(["%s"] * len(row))
    with db() as conn:
        conn.execute(f"INSERT INTO patient_encounters ({cols}) VALUES ({marks})", list(row.values()))


def add_intervention(encounter_id, status="PENDING_REVIEW", assigned_to=None, notes=None):
    with db() as conn:
        conn.execute(
            "INSERT INTO care_interventions (encounter_id, status, assigned_to, notes) "
            "VALUES (%s, %s, %s, %s)", (str(encounter_id), status, assigned_to, notes))


def count(table, where="TRUE"):
    with db() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        reset_tables()
        self.client = TestClient(app, raise_server_exceptions=False)


# --------------------------------------------------------------------------
# Risk scoring rules (service.calculate_risk)
# --------------------------------------------------------------------------
class RiskScoringTests(unittest.TestCase):
    def test_all_zero_is_low(self):
        self.assertEqual(service.calculate_risk({}), (0, "LOW"))

    def test_each_rule_boundary(self):
        cases = [
            ("number_inpatient", 2, 0), ("number_inpatient", 3, 3),
            ("number_emergency", 1, 0), ("number_emergency", 2, 2),
            ("number_outpatient", 2, 0), ("number_outpatient", 3, 1),
            ("time_in_hospital", 6, 0), ("time_in_hospital", 7, 1),
            ("num_medications", 19, 0), ("num_medications", 20, 1),
        ]
        for field, value, points in cases:
            with self.subTest(field=field, value=value):
                self.assertEqual(service.calculate_risk({field: value})[0], points)

    def test_rules_do_not_scale_above_threshold(self):
        self.assertEqual(service.calculate_risk({"number_inpatient": 50})[0], 3)
        self.assertEqual(service.calculate_risk({"number_emergency": 50})[0], 2)

    def test_level_boundaries(self):
        cases = {0: "LOW", 1: "LOW", 2: "LOW", 3: "MEDIUM", 4: "MEDIUM", 5: "HIGH", 8: "HIGH"}
        inputs = {
            0: {}, 1: {"time_in_hospital": 7}, 2: {"number_emergency": 2},
            3: {"number_inpatient": 3}, 4: {"number_inpatient": 3, "time_in_hospital": 7},
            5: {"number_inpatient": 3, "number_emergency": 2},
            8: {"number_inpatient": 3, "number_emergency": 2, "number_outpatient": 3,
                "time_in_hospital": 7, "num_medications": 20},
        }
        for score, level in cases.items():
            with self.subTest(score=score):
                self.assertEqual(service.calculate_risk(inputs[score]), (score, level))

    def test_maximum_score_is_eight(self):
        worst = {"number_inpatient": 9, "number_emergency": 9, "number_outpatient": 9,
                 "time_in_hospital": 14, "num_medications": 80}
        self.assertEqual(service.calculate_risk(worst), (8, "HIGH"))

    def test_unrelated_fields_ignored(self):
        self.assertEqual(service.calculate_risk({"age": "[90-100)", "readmitted": "<30"}), (0, "LOW"))

    def test_negative_values_score_nothing(self):
        self.assertEqual(service.calculate_risk({"number_inpatient": -5}), (0, "LOW"))

    def test_missing_values_do_not_crash(self):
        # A null from the database/CSV must not blow up scoring.
        self.assertEqual(service.calculate_risk({"number_inpatient": None})[1], "LOW")

    def test_scoring_code_never_references_the_outcome_field(self):
        import inspect
        self.assertNotIn("readmitted", inspect.getsource(service.calculate_risk))
        self.assertNotIn("readmitted", service.SCORING_FIELDS)

    def test_outcome_is_not_used_for_scoring(self):
        a = service.calculate_risk({"number_inpatient": 3, "readmitted": "<30"})
        b = service.calculate_risk({"number_inpatient": 3, "readmitted": "NO"})
        self.assertEqual(a, b)


# --------------------------------------------------------------------------
# schema.sql
# --------------------------------------------------------------------------
class SchemaTests(ApiTestCase):
    def test_defaults(self):
        with db() as conn:
            conn.execute("INSERT INTO patient_encounters (encounter_id, patient_nbr) VALUES ('1','p')")
            conn.execute("INSERT INTO care_interventions (encounter_id) VALUES ('1')")
            score, level = conn.execute("SELECT risk_score, risk_level FROM patient_encounters").fetchone()
            status, created, updated = conn.execute(
                "SELECT status, created_at, updated_at FROM care_interventions").fetchone()
        self.assertEqual((score, level, status), (0, "LOW", "PENDING_REVIEW"))
        self.assertIsNotNone(created)
        self.assertIsNotNone(updated)

    def test_primary_key_and_not_null(self):
        add_encounter(1)
        with self.assertRaises(psycopg.errors.UniqueViolation):
            add_encounter(1)
        with self.assertRaises(psycopg.errors.NotNullViolation):
            with db() as conn:
                conn.execute("INSERT INTO patient_encounters (encounter_id) VALUES ('2')")

    def test_foreign_key_enforced(self):
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            add_intervention("does-not-exist")

    def test_one_intervention_per_encounter_enforced_by_schema(self):
        add_encounter(1)
        add_intervention(1)
        with self.assertRaises(psycopg.errors.UniqueViolation):
            add_intervention(1)

    def test_risk_level_and_status_restricted_to_valid_values(self):
        with self.assertRaises(psycopg.errors.CheckViolation):
            add_encounter(1, level="BOGUS")
        add_encounter(2)
        with self.assertRaises(psycopg.errors.CheckViolation):
            add_intervention(2, status="BOGUS")


# --------------------------------------------------------------------------
# GET /encounters and /encounters/{id}
# --------------------------------------------------------------------------
class EncounterEndpointTests(ApiTestCase):
    def test_empty_list(self):
        r = self.client.get("/encounters")
        self.assertEqual((r.status_code, r.json()), (200, []))

    def test_list_returns_all_fields(self):
        add_encounter(1, "HIGH", 6, "<30")
        row = self.client.get("/encounters").json()[0]
        for key in ("encounter_id", "patient_nbr", "age", "time_in_hospital", "risk_score",
                    "risk_level", "readmitted", "number_inpatient"):
            self.assertIn(key, row)
        self.assertEqual((row["risk_score"], row["risk_level"], row["readmitted"]), (6, "HIGH", "<30"))

    def test_default_page_size_is_100(self):
        for i in range(120):
            add_encounter(i)
        self.assertEqual(len(self.client.get("/encounters").json()), 100)

    def test_limit_and_offset(self):
        for i in range(10):
            add_encounter(i)
        self.assertEqual(len(self.client.get("/encounters?limit=3").json()), 3)
        self.assertEqual(len(self.client.get("/encounters?limit=100&offset=8").json()), 2)
        self.assertEqual(self.client.get("/encounters?offset=50").json(), [])

    def test_limit_zero_returns_empty(self):
        add_encounter(1)
        self.assertEqual(self.client.get("/encounters?limit=0").json(), [])

    def test_pages_do_not_overlap_and_cover_everything(self):
        for i in range(25):
            add_encounter(i)
        seen = []
        for offset in (0, 10, 20):
            seen += [r["encounter_id"] for r in self.client.get(f"/encounters?limit=10&offset={offset}").json()]
        self.assertEqual(sorted(seen), sorted(str(i) for i in range(25)))

    def test_non_integer_paging_params_rejected(self):
        for query in ("limit=abc", "offset=x", "limit=1.5"):
            with self.subTest(query=query):
                self.assertEqual(self.client.get(f"/encounters?{query}").status_code, 422)

    def test_negative_paging_params_are_client_errors_not_500(self):
        for query in ("limit=-1", "offset=-5"):
            with self.subTest(query=query):
                self.assertIn(self.client.get(f"/encounters?{query}").status_code, (400, 422))

    def test_limit_is_capped(self):
        for i in range(5):
            add_encounter(i)
        r = self.client.get("/encounters?limit=100000000")
        self.assertIn(r.status_code, (200, 422))
        if r.status_code == 200:
            self.assertLessEqual(len(r.json()), 1000)

    def test_huge_limit_value_does_not_crash(self):
        r = self.client.get("/encounters?limit=99999999999999999999")
        self.assertNotEqual(r.status_code, 500)

    def test_get_one(self):
        add_encounter(42, "MEDIUM", 3)
        r = self.client.get("/encounters/42")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["encounter_id"], "42")

    def test_get_one_not_found(self):
        r = self.client.get("/encounters/999")
        self.assertEqual(r.status_code, 404)
        self.assertIn("999", r.json()["detail"])

    def test_sql_injection_attempts_are_harmless(self):
        add_encounter(1)
        for payload in ("1' OR '1'='1", "1; DROP TABLE patient_encounters;--", "' UNION SELECT 1--"):
            with self.subTest(payload=payload):
                self.assertEqual(self.client.get("/encounters/" + payload).status_code, 404)
        self.assertEqual(count("patient_encounters"), 1)

    def test_unicode_and_long_ids(self):
        self.assertEqual(self.client.get("/encounters/é株式会社").status_code, 404)
        self.assertEqual(self.client.get("/encounters/" + "9" * 500).status_code, 404)

    def test_id_with_slash_is_not_found(self):
        self.assertEqual(self.client.get("/encounters/a/b").status_code, 404)

    def test_response_is_json_serialisable_with_nulls(self):
        add_encounter(1, age=None)
        self.assertIsNone(self.client.get("/encounters/1").json()["age"])


# --------------------------------------------------------------------------
# GET /care-queue and /care-queue/high
# --------------------------------------------------------------------------
class CareQueueTests(ApiTestCase):
    def seed(self):
        add_encounter(1, "LOW", 1)
        add_encounter(2, "MEDIUM", 3)
        add_encounter(3, "HIGH", 6)
        add_encounter(4, "MEDIUM", 4)
        add_encounter(5, "HIGH", 8)

    def test_queue_contains_only_medium_and_high(self):
        self.seed()
        ids = [r["encounter_id"] for r in self.client.get("/care-queue").json()]
        self.assertEqual(sorted(ids), ["2", "3", "4", "5"])

    def test_queue_sorted_by_score_descending(self):
        self.seed()
        scores = [r["risk_score"] for r in self.client.get("/care-queue").json()]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(scores[0], 8)

    def test_high_queue_only_high(self):
        self.seed()
        rows = self.client.get("/care-queue/high").json()
        self.assertEqual([r["encounter_id"] for r in rows], ["5", "3"])
        self.assertTrue(all(r["risk_level"] == "HIGH" for r in rows))

    def test_empty_queue(self):
        add_encounter(1, "LOW", 0)
        self.assertEqual(self.client.get("/care-queue").json(), [])
        self.assertEqual(self.client.get("/care-queue/high").json(), [])

    def test_queue_pagination(self):
        for i in range(12):
            add_encounter(i, "HIGH", 5 + i % 3)
        self.assertEqual(len(self.client.get("/care-queue?limit=5").json()), 5)
        self.assertEqual(len(self.client.get("/care-queue?limit=50&offset=10").json()), 2)
        self.assertEqual(len(self.client.get("/care-queue").json()), 12)

    def test_default_queue_page_size_is_50(self):
        for i in range(60):
            add_encounter(i, "HIGH", 5)
        self.assertEqual(len(self.client.get("/care-queue").json()), 50)

    def test_pagination_stable_with_tied_scores(self):
        for i in range(40):
            add_encounter(i, "HIGH", 5)  # all tie on score
        whole = [r["encounter_id"] for r in self.client.get("/care-queue?limit=40").json()]
        paged = []
        for offset in range(0, 40, 7):
            paged += [r["encounter_id"] for r in self.client.get(f"/care-queue?limit=7&offset={offset}").json()]
        self.assertEqual(len(set(paged)), 40)
        self.assertEqual(paged, whole)

    def test_queue_shows_intervention_details(self):
        add_encounter(3, "HIGH", 6)
        add_intervention(3, "ASSIGNED", "Nurse Joy", "call patient")
        row = self.client.get("/care-queue").json()[0]
        self.assertEqual((row["status"], row["assigned_to"], row["notes"]),
                         ("ASSIGNED", "Nurse Joy", "call patient"))

    def test_untouched_encounter_shows_pending_review_status(self):
        add_encounter(3, "HIGH", 6)
        self.assertEqual(self.client.get("/care-queue").json()[0]["status"], "PENDING_REVIEW")

    def test_each_encounter_appears_once(self):
        add_encounter(3, "HIGH", 6)
        add_intervention(3)
        self.assertEqual(len(self.client.get("/care-queue").json()), 1)

    def test_queue_includes_closed_items_flag_or_excludes_them(self):
        # A CLOSED case should not keep occupying the review queue.
        add_encounter(3, "HIGH", 6)
        add_intervention(3, "CLOSED")
        add_encounter(4, "HIGH", 6)
        ids = [r["encounter_id"] for r in self.client.get("/care-queue").json()]
        self.assertNotIn("3", ids)

    def test_invalid_paging_params(self):
        self.assertEqual(self.client.get("/care-queue?limit=abc").status_code, 422)
        self.assertIn(self.client.get("/care-queue?limit=-1").status_code, (400, 422))
        self.assertIn(self.client.get("/care-queue/high?offset=-1").status_code, (400, 422))

    def test_route_high_not_captured_as_id(self):
        self.assertEqual(self.client.get("/care-queue/high").status_code, 200)

    def test_high_queue_pagination(self):
        for i in range(8):
            add_encounter(i, "HIGH", 6)
        self.assertEqual(len(self.client.get("/care-queue/high?limit=3&offset=6").json()), 2)


# --------------------------------------------------------------------------
# PATCH /interventions/{encounter_id}
# --------------------------------------------------------------------------
class InterventionTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        add_encounter(1, "HIGH", 6)

    def patch(self, body, encounter_id="1"):
        return self.client.patch(f"/interventions/{encounter_id}", json=body)

    def test_creates_intervention(self):
        r = self.patch({"status": "ASSIGNED", "assigned_to": "Nurse Joy", "notes": "call"})
        self.assertEqual(r.status_code, 200)
        data = r.json()["data"]
        self.assertEqual((data["status"], data["assigned_to"], data["notes"], data["encounter_id"]),
                         ("ASSIGNED", "Nurse Joy", "call", "1"))
        self.assertEqual(count("care_interventions"), 1)

    def test_update_keeps_a_single_row(self):
        self.patch({"status": "ASSIGNED"})
        self.patch({"status": "CONTACTED"})
        self.patch({"status": "CLOSED"})
        self.assertEqual(count("care_interventions"), 1)
        self.assertEqual(count("care_interventions", "status='CLOSED'"), 1)

    def test_optional_fields_can_be_omitted(self):
        r = self.patch({"status": "PENDING_REVIEW"})
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json()["data"]["assigned_to"])

    def test_full_workflow_sequence(self):
        for status in ("PENDING_REVIEW", "ASSIGNED", "CONTACTED", "FOLLOW_UP_SCHEDULED", "CLOSED"):
            with self.subTest(status=status):
                self.assertEqual(self.patch({"status": status}).status_code, 200)

    def test_unknown_encounter_404_and_nothing_created(self):
        r = self.patch({"status": "ASSIGNED"}, encounter_id="nope")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(count("care_interventions"), 0)

    def test_missing_status_422(self):
        self.assertEqual(self.patch({"assigned_to": "x"}).status_code, 422)
        self.assertEqual(self.patch({}).status_code, 422)

    def test_wrong_types_422(self):
        self.assertEqual(self.patch({"status": ["x"]}).status_code, 422)
        self.assertEqual(self.patch({"status": "ASSIGNED", "notes": {"a": 1}}).status_code, 422)

    def test_non_json_body_422(self):
        r = self.client.patch("/interventions/1", content="oops", headers={"content-type": "application/json"})
        self.assertEqual(r.status_code, 422)

    def test_invalid_status_value_rejected(self):
        r = self.patch({"status": "BOGUS"})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(count("care_interventions"), 0)

    def test_status_is_case_sensitive_or_normalised(self):
        r = self.patch({"status": "assigned"})
        self.assertTrue(r.status_code == 422 or r.json()["data"]["status"] == "ASSIGNED")

    def test_partial_update_keeps_existing_owner_and_notes(self):
        self.patch({"status": "ASSIGNED", "assigned_to": "Nurse Joy", "notes": "call"})
        data = self.patch({"status": "CONTACTED"}).json()["data"]
        self.assertEqual((data["assigned_to"], data["notes"]), ("Nurse Joy", "call"))

    def test_updated_at_changes_and_created_at_does_not(self):
        first = self.patch({"status": "ASSIGNED"}).json()["data"]
        time.sleep(0.05)
        second = self.patch({"status": "CONTACTED"}).json()["data"]
        self.assertEqual(first["created_at"], second["created_at"])
        self.assertGreater(second["updated_at"], first["updated_at"])

    def test_workflow_cannot_go_backwards_from_closed(self):
        self.patch({"status": "CLOSED"})
        self.assertIn(self.patch({"status": "PENDING_REVIEW"}).status_code, (400, 409, 422))

    def test_special_characters_and_unicode_in_notes(self):
        note = "O'Brien; DROP TABLE x;-- 患者 ✓ \"quoted\""
        data = self.patch({"status": "ASSIGNED", "notes": note}).json()["data"]
        self.assertEqual(data["notes"], note)
        self.assertEqual(count("patient_encounters"), 1)

    def test_very_long_assigned_to_is_client_error(self):
        r = self.patch({"status": "ASSIGNED", "assigned_to": "x" * 500})  # column is VARCHAR(100)
        self.assertEqual(r.status_code, 422)

    def test_very_long_notes_accepted(self):
        self.assertEqual(self.patch({"status": "ASSIGNED", "notes": "n" * 100000}).status_code, 200)

    def test_intervention_visible_in_queue_and_summary(self):
        self.patch({"status": "ASSIGNED"})
        self.assertEqual(self.client.get("/care-queue").json()[0]["status"], "ASSIGNED")
        self.patch({"status": "CLOSED"})
        self.assertEqual(self.client.get("/operations/summary").json()["completed_interventions"], 1)

    def test_same_status_update_allowed_on_closed_case(self):
        self.patch({"status": "CLOSED"})
        r = self.patch({"status": "CLOSED", "notes": "final note"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["data"]["notes"], "final note")

    def test_explicit_null_clears_a_field(self):
        self.patch({"status": "ASSIGNED", "assigned_to": "Nurse Joy", "notes": "call"})
        data = self.patch({"status": "ASSIGNED", "notes": None}).json()["data"]
        self.assertEqual((data["assigned_to"], data["notes"]), ("Nurse Joy", None))

    def test_lowercase_status_rejected(self):
        r = self.patch({"status": "assigned"})
        self.assertEqual(r.status_code, 422)

    def test_assigned_to_at_100_characters_accepted(self):
        self.assertEqual(self.patch({"status": "ASSIGNED", "assigned_to": "x" * 100}).status_code, 200)

    def test_repeated_patch_leaves_one_row(self):
        for _ in range(3):
            self.patch({"status": "ASSIGNED"})
        self.assertEqual(count("care_interventions"), 1)

    def test_other_encounters_unaffected(self):
        add_encounter(2, "HIGH", 7)
        self.patch({"status": "ASSIGNED"})
        self.assertEqual(count("care_interventions", "encounter_id='2'"), 0)

    def test_get_on_interventions_not_allowed(self):
        self.assertEqual(self.client.get("/interventions/1").status_code, 405)


# --------------------------------------------------------------------------
# Operations and management endpoints
# --------------------------------------------------------------------------
class OperationsTests(ApiTestCase):
    def test_empty_database_all_zero(self):
        s = self.client.get("/operations/summary").json()
        self.assertEqual(s, {"total_encounters": 0, "high_risk": 0, "medium_risk": 0, "low_risk": 0,
                             "pending_interventions": 0, "completed_interventions": 0})

    def test_counts(self):
        for i in range(3):
            add_encounter(f"h{i}", "HIGH", 6)
        for i in range(2):
            add_encounter(f"m{i}", "MEDIUM", 3)
        add_encounter("l0", "LOW", 0)
        add_intervention("h0", "PENDING_REVIEW")
        add_intervention("h1", "CLOSED")
        add_intervention("h2", "CLOSED")
        s = self.client.get("/operations/summary").json()
        self.assertEqual((s["total_encounters"], s["high_risk"], s["medium_risk"], s["low_risk"]), (6, 3, 2, 1))
        self.assertEqual(s["completed_interventions"], 2)
        # h0 (explicitly pending) + m0, m1 (queue cases nobody has started on)
        self.assertEqual(s["pending_interventions"], 3)

    def test_risk_counts_add_up_to_total(self):
        for i, level in enumerate(["HIGH", "MEDIUM", "LOW", "LOW"]):
            add_encounter(i, level)
        s = self.client.get("/operations/summary").json()
        self.assertEqual(s["high_risk"] + s["medium_risk"] + s["low_risk"], s["total_encounters"])

    def test_pending_includes_queue_cases_nobody_has_touched(self):
        add_encounter(1, "HIGH", 6)  # in the queue, no intervention record yet
        add_encounter(2, "MEDIUM", 3)
        add_encounter(3, "LOW", 0)
        self.assertEqual(self.client.get("/operations/summary").json()["pending_interventions"], 2)

    def test_in_progress_statuses_not_counted_as_pending_or_completed(self):
        add_encounter(1, "HIGH", 6)
        add_intervention(1, "ASSIGNED")
        s = self.client.get("/operations/summary").json()
        self.assertEqual((s["pending_interventions"], s["completed_interventions"]), (0, 0))

    def test_summary_reflects_new_data_immediately(self):
        add_encounter(1, "HIGH", 6)
        self.assertEqual(self.client.get("/operations/summary").json()["total_encounters"], 1)
        add_encounter(2, "HIGH", 6)
        self.assertEqual(self.client.get("/operations/summary").json()["total_encounters"], 2)

    def test_readmission_analysis_empty_is_zero_not_error(self):
        r = self.client.get("/operations/readmission-analysis")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["high_risk_readmission_rate"], 0.0)

    def test_readmission_rate(self):
        for i, outcome in enumerate(["<30", "<30", ">30", "NO"]):
            add_encounter(f"h{i}", "HIGH", 6, outcome)
        add_encounter("m", "MEDIUM", 3, "<30")  # must not count as high risk
        r = self.client.get("/operations/readmission-analysis").json()
        self.assertEqual((r["high_risk_patients"], r["high_risk_readmitted_under_30_days"]), (4, 2))
        self.assertEqual(r["high_risk_readmission_rate"], 50.0)

    def test_readmission_rate_rounding(self):
        for i, outcome in enumerate(["<30", "NO", "NO"]):
            add_encounter(i, "HIGH", 6, outcome)
        self.assertEqual(self.client.get("/operations/readmission-analysis").json()["high_risk_readmission_rate"], 33.33)

    def test_only_under_30_counts_as_readmitted(self):
        add_encounter(1, "HIGH", 6, ">30")
        add_encounter(2, "HIGH", 6, "NO")
        add_encounter(3, "HIGH", 6, None)
        r = self.client.get("/operations/readmission-analysis").json()
        self.assertEqual(r["high_risk_readmitted_under_30_days"], 0)

    def test_readmission_analysis_compares_all_risk_groups(self):
        # README: "Compare risk groups against historical <30-day readmissions".
        add_encounter(1, "HIGH", 6, "<30")
        add_encounter(2, "MEDIUM", 3, "NO")
        add_encounter(3, "LOW", 0, "NO")
        text = json.dumps(self.client.get("/operations/readmission-analysis").json()).lower()
        self.assertIn("medium", text)
        self.assertIn("low", text)

    def test_dashboard_combines_both_reports(self):
        add_encounter(1, "HIGH", 6, "<30")
        dash = self.client.get("/management/dashboard").json()
        self.assertEqual(dash["operations"], self.client.get("/operations/summary").json())
        self.assertEqual(dash["clinical_validation"], self.client.get("/operations/readmission-analysis").json())

    def test_dashboard_empty(self):
        r = self.client.get("/management/dashboard")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["operations"]["total_encounters"], 0)

    def test_read_only_endpoints_do_not_change_data(self):
        add_encounter(1, "HIGH", 6)
        before = count("patient_encounters"), count("care_interventions")
        for path in ("/encounters", "/care-queue", "/operations/summary",
                     "/operations/readmission-analysis", "/management/dashboard"):
            self.client.get(path)
        self.assertEqual((count("patient_encounters"), count("care_interventions")), before)


# --------------------------------------------------------------------------
# Robustness
# --------------------------------------------------------------------------
class RobustnessTests(ApiTestCase):
    def test_database_down_returns_clean_error_not_500(self):
        with patch.object(database, "DATABASE_URL", "postgresql://nobody:x@localhost:1/none"):
            r = self.client.get("/encounters")
        self.assertEqual(r.status_code, 503)
        self.assertNotIn("nobody", r.text)  # no connection details leaked

    def test_unreachable_database_fails_fast(self):
        started = time.time()
        with patch.object(database, "DATABASE_URL", "postgresql://nobody:x@localhost:1/none"):
            self.client.get("/encounters")
        self.assertLess(time.time() - started, 15)

    def test_missing_database_url_returns_503(self):
        with patch.object(database, "DATABASE_URL", None):
            for path in ("/encounters", "/care-queue", "/operations/summary"):
                self.assertEqual(self.client.get(path).status_code, 503)

    def test_page_size_limit_boundaries(self):
        self.assertEqual(self.client.get("/encounters?limit=1000").status_code, 200)
        self.assertEqual(self.client.get("/encounters?limit=1001").status_code, 422)
        self.assertEqual(self.client.get("/care-queue?limit=1001").status_code, 422)
        self.assertEqual(self.client.get("/encounters?offset=2147483648").status_code, 422)

    def test_readmission_analysis_group_structure(self):
        add_encounter(1, "HIGH", 6, "<30")
        add_encounter(2, "HIGH", 6, "NO")
        add_encounter(3, "MEDIUM", 3, "<30")
        groups = self.client.get("/operations/readmission-analysis").json()["risk_groups"]
        self.assertEqual(list(groups), ["HIGH", "MEDIUM", "LOW"])
        self.assertEqual(groups["HIGH"], {"patients": 2, "readmitted_under_30_days": 1, "readmission_rate": 50.0})
        self.assertEqual(groups["MEDIUM"]["readmission_rate"], 100.0)
        self.assertEqual(groups["LOW"], {"patients": 0, "readmitted_under_30_days": 0, "readmission_rate": 0.0})

    def test_unknown_routes_and_methods(self):
        self.assertEqual(self.client.get("/nope").status_code, 404)
        self.assertEqual(self.client.post("/encounters", json={}).status_code, 405)
        self.assertEqual(self.client.delete("/encounters/1").status_code, 405)

    def test_openapi_and_docs_available(self):
        self.assertEqual(self.client.get("/openapi.json").status_code, 200)
        self.assertEqual(self.client.get("/docs").status_code, 200)
        self.assertEqual(self.client.get("/openapi.json").json()["info"]["title"],
                         "Diabetes Readmission Intervention Queue MVP")


# --------------------------------------------------------------------------
# data_loader.py with small CSVs
# --------------------------------------------------------------------------
CSV_HEADER = ("encounter_id,patient_nbr,age,time_in_hospital,number_inpatient,number_emergency,"
              "number_outpatient,num_medications,num_lab_procedures,discharge_disposition_id,"
              "admission_type_id,readmitted\n")


class DataLoaderTests(ApiTestCase):
    def load(self, rows, header=CSV_HEADER):
        import data_loader
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.csv"
            path.write_text(header + "".join(rows), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                data_loader.load_data(str(path))

    def fetch(self, encounter_id):
        with db() as conn:
            cur = conn.execute("SELECT risk_score, risk_level, age, patient_nbr FROM patient_encounters "
                               "WHERE encounter_id=%s", (str(encounter_id),))
            return cur.fetchone()

    def test_loads_and_scores_rows(self):
        self.load(["1,100,[50-60),8,3,2,3,25,40,1,1,<30\n",
                   "2,101,[10-20),1,0,0,0,2,10,1,1,NO\n"])
        self.assertEqual(self.fetch(1)[:2], (8, "HIGH"))
        self.assertEqual(self.fetch(2)[:2], (0, "LOW"))

    def test_loaded_score_does_not_depend_on_readmitted(self):
        row = "{id},100,[50-60),8,3,2,3,25,40,1,1,{outcome}\n"
        self.load([row.format(id=1, outcome="<30"), row.format(id=2, outcome="NO"),
                   row.format(id=3, outcome=">30")])
        self.assertEqual({self.fetch(i)[:2] for i in (1, 2, 3)}, {(8, "HIGH")})

    def test_loading_twice_does_not_duplicate(self):
        rows = ["1,100,[50-60),8,3,2,3,25,40,1,1,<30\n"]
        self.load(rows)
        self.load(rows)
        self.assertEqual(count("patient_encounters"), 1)

    def test_duplicate_ids_in_file_keep_first(self):
        self.load(["1,100,[50-60),8,3,2,3,25,40,1,1,<30\n", "1,999,[10-20),1,0,0,0,2,10,1,1,NO\n"])
        self.assertEqual(count("patient_encounters"), 1)
        self.assertEqual(self.fetch(1)[3], "100")

    def test_reload_refreshes_changed_rows(self):
        self.load(["1,100,[50-60),1,0,0,0,2,10,1,1,NO\n"])
        self.load(["1,100,[50-60),8,3,2,3,25,40,1,1,<30\n"])
        self.assertEqual(self.fetch(1)[1], "HIGH")  # new scores should replace old ones

    def test_missing_numbers_become_zero(self):
        self.load(["1,100,[50-60),,,,,,,,,NO\n"])
        self.assertEqual(self.fetch(1)[:2], (0, "LOW"))

    def test_empty_file_loads_nothing(self):
        self.load([])
        self.assertEqual(count("patient_encounters"), 0)

    def test_extra_columns_ignored(self):
        self.load(["1,100,Caucasian,[50-60),8,3,2,3,25,40,1,1,<30\n"],
                  header=CSV_HEADER.replace("patient_nbr,", "patient_nbr,race,"))
        self.assertEqual(count("patient_encounters"), 1)

    def test_missing_required_column_gives_clear_error(self):
        with self.assertRaises(Exception) as ctx:
            self.load(["1,[50-60),8\n"], header="encounter_id,age,time_in_hospital\n")
        self.assertNotIsInstance(ctx.exception, (AttributeError, TypeError))

    def test_blank_patient_number_is_not_stored_as_text_nan(self):
        try:
            self.load(["1,,[50-60),8,3,2,3,25,40,1,1,<30\n"])
        except Exception:
            return  # rejecting the row is fine
        stored = self.fetch(1)
        self.assertTrue(stored is None or stored[3] not in ("nan", "None", ""))

    def test_missing_file_gives_file_error(self):
        import data_loader
        with self.assertRaises(FileNotFoundError):
            data_loader.load_data(str(HERE / "data" / "no_such_file.csv"))

    def test_rows_without_patient_number_are_skipped_others_loaded(self):
        self.load(["2,101,[10-20),1,0,0,0,2,10,1,1,NO\n", "3,,[10-20),1,0,0,0,2,10,1,1,NO\n"])
        self.assertIsNotNone(self.fetch(2))
        self.assertIsNone(self.fetch(3))

    def test_rows_without_encounter_id_are_skipped(self):
        self.load([",100,[50-60),8,3,2,3,25,40,1,1,<30\n"])
        self.assertEqual(count("patient_encounters"), 0)

    def test_load_reports_loaded_and_skipped_counts(self):
        import data_loader
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.csv"
            path.write_text(CSV_HEADER + "1,100,[50-60),1,0,0,0,2,10,1,1,NO\n,5,[50-60),1,0,0,0,2,10,1,1,NO\n",
                            encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                result = data_loader.load_data(str(path))
        self.assertEqual(result, {"loaded": 1, "skipped": 1})

    def test_reload_keeps_care_interventions(self):
        self.load(["1,100,[50-60),8,3,2,3,25,40,1,1,<30\n"])
        add_intervention(1, "ASSIGNED", "Nurse Joy")
        self.load(["1,100,[50-60),8,3,2,3,25,40,1,1,<30\n"])
        self.assertEqual(count("care_interventions", "status='ASSIGNED'"), 1)

    def test_numeric_ids_keep_their_exact_text(self):
        self.load(["0012,0099,[50-60),1,0,0,0,2,10,1,1,NO\n"])
        self.assertEqual(self.fetch("0012")[3], "0099")


# --------------------------------------------------------------------------
# Full real dataset (101,766 rows)
# --------------------------------------------------------------------------
@unittest.skipUnless(CSV_PATH.exists() and not os.environ.get("SKIP_REAL_DATA"),
                     "dataset missing or SKIP_REAL_DATA set")
class RealDatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import pandas as pd
        import data_loader
        reset_tables()
        cls.df = pd.read_csv(CSV_PATH).drop_duplicates("encounter_id")
        started = time.time()
        with contextlib.redirect_stdout(io.StringIO()):
            data_loader.load_data(str(CSV_PATH))
        cls.load_seconds = time.time() - started
        cls.client = TestClient(app, raise_server_exceptions=False)
        d = cls.df
        score = ((d.number_inpatient >= 3) * 3 + (d.number_emergency >= 2) * 2 +
                 (d.number_outpatient >= 3) + (d.time_in_hospital >= 7) + (d.num_medications >= 20))
        cls.score = score
        cls.level = score.map(lambda s: "HIGH" if s >= 5 else "MEDIUM" if s >= 3 else "LOW")

    @classmethod
    def tearDownClass(cls):
        reset_tables()

    def test_all_rows_loaded(self):
        self.assertEqual(count("patient_encounters"), len(self.df))

    def test_scores_match_independent_calculation(self):
        with db() as conn:
            rows = dict(conn.execute("SELECT encounter_id, risk_score FROM patient_encounters").fetchall())
        expected = dict(zip(self.df.encounter_id.astype(str), self.score.astype(int)))
        self.assertEqual(rows, expected)

    def test_level_counts_match_summary_endpoint(self):
        s = self.client.get("/operations/summary").json()
        counts = self.level.value_counts()
        self.assertEqual(s["total_encounters"], len(self.df))
        self.assertEqual(s["high_risk"], int(counts.get("HIGH", 0)))
        self.assertEqual(s["medium_risk"], int(counts.get("MEDIUM", 0)))
        self.assertEqual(s["low_risk"], int(counts.get("LOW", 0)))

    def test_readmission_analysis_matches_independent_calculation(self):
        high = self.level == "HIGH"
        readmitted = int((high & (self.df.readmitted == "<30")).sum())
        r = self.client.get("/operations/readmission-analysis").json()
        self.assertEqual(r["high_risk_patients"], int(high.sum()))
        self.assertEqual(r["high_risk_readmitted_under_30_days"], readmitted)
        self.assertAlmostEqual(r["high_risk_readmission_rate"], round(readmitted / int(high.sum()) * 100, 2))

    def test_high_risk_has_higher_readmission_rate_than_overall(self):
        overall = (self.df.readmitted == "<30").mean() * 100
        rate = self.client.get("/operations/readmission-analysis").json()["high_risk_readmission_rate"]
        self.assertGreater(rate, overall)

    def test_queue_is_sorted_and_only_medium_high(self):
        rows = self.client.get("/care-queue?limit=500").json()
        scores = [r["risk_score"] for r in rows]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertTrue(all(r["risk_level"] in ("MEDIUM", "HIGH") for r in rows))
        self.assertEqual(scores[0], int(self.score.max()))

    def test_queue_paging_consistent_across_page_sizes(self):
        whole = [r["encounter_id"] for r in self.client.get("/care-queue?limit=200").json()]
        paged = []
        for offset in range(0, 200, 50):
            paged += [r["encounter_id"] for r in self.client.get(f"/care-queue?limit=50&offset={offset}").json()]
        self.assertEqual(len(set(paged)), len(paged), "an encounter appears on two pages")
        self.assertEqual(paged, whole)

    def test_encounter_paging_has_no_duplicates(self):
        ids = []
        for offset in range(0, 1000, 250):
            ids += [r["encounter_id"] for r in self.client.get(f"/encounters?limit=250&offset={offset}").json()]
        self.assertEqual(len(set(ids)), len(ids))

    def test_lookup_every_sampled_encounter(self):
        for encounter_id in self.df.encounter_id.sample(20, random_state=1):
            self.assertEqual(self.client.get(f"/encounters/{encounter_id}").status_code, 200)

    def test_dashboard_responds_within_two_seconds(self):
        started = time.time()
        self.assertEqual(self.client.get("/management/dashboard").status_code, 200)
        self.assertLess(time.time() - started, 2)

    def test_summary_pending_matches_unreviewed_queue_size(self):
        queue = int((self.level != "LOW").sum())
        self.assertEqual(self.client.get("/operations/summary").json()["pending_interventions"], queue)

    def test_unbounded_limit_does_not_dump_whole_table(self):
        r = self.client.get("/encounters?limit=1000000")
        if r.status_code == 200:
            self.assertLessEqual(len(r.json()), 1000)


if __name__ == "__main__":
    unittest.main()
