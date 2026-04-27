import csv
import io
import os
import time
import unittest
from unittest.mock import MagicMock, patch

import app


def wait_for_job(client, job_id):
    payload = {}
    for _ in range(30):
        response = client.get(f"/api/status/{job_id}")
        payload = response.get_json()
        if payload["status"] in {"complete", "failed"}:
            break
        time.sleep(0.2)
    return payload


class ApprovalWorkflowTest(unittest.TestCase):
    def setUp(self):
        app.init_db()
        with app.get_db() as conn:
            conn.execute("DELETE FROM audit_log")
            conn.execute("DELETE FROM lead_events")
            conn.execute("DELETE FROM exports")
            conn.execute("DELETE FROM suppression_list")
            conn.execute("DELETE FROM leads")

    def create_seed_job(self, lead_count="2"):
        client = app.app.test_client()
        response = client.post(
            "/",
            data={
                "brand": app.DEFAULT_CONTEXT["brand"],
                "location": app.DEFAULT_CONTEXT["location"],
                "offer": app.DEFAULT_CONTEXT["offer"],
                "icp": app.DEFAULT_CONTEXT["icp"],
                "category": app.DEFAULT_CONTEXT["category"],
                "lead_count": lead_count,
                "mode": "seed_demo",
            },
            follow_redirects=False,
        )
        job_id = response.headers["Location"].rstrip("/").split("/")[-1]
        payload = wait_for_job(client, job_id)
        self.assertEqual(payload["status"], "complete", payload)
        return client, payload

    def table_count(self, table_name):
        with app.get_db() as conn:
            return conn.execute(f"SELECT COUNT(*) AS count FROM {table_name}").fetchone()["count"]

    def test_local_env_loader_fills_blank_environment_values(self):
        with patch.dict(os.environ, {"HUNTER_API_KEY": ""}, clear=False):
            app.load_local_env()
            self.assertTrue(os.environ["HUNTER_API_KEY"])

    def test_completed_job_persists_leads_with_generated_status(self):
        _client, payload = self.create_seed_job()
        lead_ids = [lead.get("id") for lead in payload["results"]]

        self.assertEqual(len(lead_ids), 2)
        self.assertTrue(all(lead_ids))
        with app.get_db() as conn:
            statuses = [
                row["status"]
                for row in conn.execute(
                    "SELECT status FROM leads WHERE job_id = ? ORDER BY position",
                    (payload["job_id"],),
                )
            ]
        self.assertEqual(statuses, ["generated", "generated"])

    def test_lead_status_and_email_can_be_updated(self):
        client, payload = self.create_seed_job("1")
        lead_id = payload["results"][0]["id"]

        status_response = client.post(f"/api/leads/{lead_id}/status", json={"status": "approved"})
        self.assertEqual(status_response.status_code, 200)
        self.assertEqual(status_response.get_json()["lead"]["status"], "approved")

        email_response = client.post(
            f"/api/leads/{lead_id}/email",
            json={"email_subject": "Edited subject", "email_body": "Edited body"},
        )
        self.assertEqual(email_response.status_code, 200)
        lead = email_response.get_json()["lead"]
        self.assertEqual(lead["email_subject"], "Edited subject")
        self.assertEqual(lead["email_body"], "Edited body")

    def test_editing_generated_lead_moves_it_to_in_review(self):
        client, payload = self.create_seed_job("1")
        lead_id = payload["results"][0]["id"]

        response = client.post(
            f"/api/leads/{lead_id}/edit-email",
            json={"email_subject": "Edited subject", "email_body": "Edited body"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["lead"]["status"], "in_review")

    def test_review_queue_excludes_terminal_workflow_states(self):
        client, payload = self.create_seed_job("2")
        first_id = payload["results"][0]["id"]
        second_id = payload["results"][1]["id"]

        client.post(f"/api/leads/{first_id}/approve")
        client.post(f"/api/leads/{second_id}/do-not-contact")
        review = client.get("/api/leads?queue=review").get_json()

        self.assertEqual(review["leads"], [])
        self.assertEqual(review["counts"]["approved"], 1)
        self.assertEqual(review["counts"]["do_not_contact"], 1)
        self.assertEqual(review["counts"]["review"], 0)

    def test_do_not_contact_adds_suppression_record(self):
        client, payload = self.create_seed_job("1")
        lead_id = payload["results"][0]["id"]
        response = client.post(f"/api/leads/{lead_id}/do-not-contact")
        self.assertEqual(response.status_code, 200)

        with app.get_db() as conn:
            count = conn.execute("SELECT COUNT(*) AS count FROM suppression_list").fetchone()["count"]
        self.assertGreaterEqual(count, 1)

    def test_rejected_and_suppressed_leads_can_be_restored_to_review(self):
        client, payload = self.create_seed_job("2")
        rejected_id = payload["results"][0]["id"]
        suppressed_id = payload["results"][1]["id"]

        client.post(f"/api/leads/{rejected_id}/reject")
        client.post(f"/api/leads/{suppressed_id}/do-not-contact")

        rejected_restore = client.post(f"/api/leads/{rejected_id}/restore")
        suppressed_restore = client.post(f"/api/leads/{suppressed_id}/restore")
        review = client.get("/api/leads?queue=review").get_json()["leads"]

        self.assertEqual(rejected_restore.get_json()["lead"]["status"], "in_review")
        self.assertEqual(suppressed_restore.get_json()["lead"]["status"], "in_review")
        self.assertEqual({lead["id"] for lead in review}, {rejected_id, suppressed_id})

    def test_export_approved_leads_to_csv_marks_exported(self):
        client, payload = self.create_seed_job("1")
        lead_id = payload["results"][0]["id"]
        client.post(f"/api/leads/{lead_id}/status", json={"status": "approved"})

        response = client.get("/export/approved")
        self.assertEqual(response.status_code, 200)
        rows = list(csv.DictReader(io.StringIO(response.data.decode("utf-8"))))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "approved")
        self.assertEqual(
            list(rows[0].keys()),
            [
                "company_name",
                "category",
                "address",
                "website",
                "suggested_contact_role",
                "recipient_email",
                "fit_score",
                "fit_reason",
                "outreach_angle",
                "email_subject",
                "email_body",
                "source_urls",
                "confidence",
                "status",
            ],
        )
        self.assertIn("email_body", rows[0])

        with app.get_db() as conn:
            row = conn.execute("SELECT status, exported_at FROM leads WHERE id = ?", (lead_id,)).fetchone()
            status = row["status"]
        self.assertEqual(status, "exported")
        self.assertTrue(row["exported_at"])

    def test_export_excludes_rejected_and_do_not_contact_leads(self):
        client, payload = self.create_seed_job("3")
        approved_id = payload["results"][0]["id"]
        rejected_id = payload["results"][1]["id"]
        suppressed_id = payload["results"][2]["id"]
        client.post(f"/api/leads/{approved_id}/approve")
        client.post(f"/api/leads/{rejected_id}/reject")
        client.post(f"/api/leads/{suppressed_id}/do-not-contact")

        response = client.get("/export/approved")
        rows = list(csv.DictReader(io.StringIO(response.data.decode("utf-8"))))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "approved")
        with app.get_db() as conn:
            statuses = {
                row["id"]: row["status"]
                for row in conn.execute(
                    "SELECT id, status FROM leads WHERE id IN (?, ?, ?)",
                    (approved_id, rejected_id, suppressed_id),
                )
            }
        self.assertEqual(statuses[approved_id], "exported")
        self.assertEqual(statuses[rejected_id], "rejected")
        self.assertEqual(statuses[suppressed_id], "do_not_contact")

    def test_approvals_route_and_filter_work(self):
        client, payload = self.create_seed_job("1")
        lead_id = payload["results"][0]["id"]
        client.post(f"/api/leads/{lead_id}/status", json={"status": "approved"})

        page = client.get("/approvals")
        self.assertEqual(page.status_code, 200)
        approved = client.get("/api/leads?status=approved").get_json()["leads"]

        self.assertEqual(len(approved), 1)
        self.assertEqual(approved[0]["id"], lead_id)

    def test_approved_queue_includes_outreach_readiness(self):
        client, payload = self.create_seed_job("1")
        lead_id = payload["results"][0]["id"]
        client.post(f"/api/leads/{lead_id}/approve")

        data = client.get("/api/leads?status=approved").get_json()

        self.assertIn("outreach", data)
        self.assertEqual(data["outreach"]["counts"]["approved"], 1)
        self.assertEqual(data["outreach"]["counts"]["missing_emails"], 1)
        self.assertIn("warnings", data["outreach"])

    def test_approved_queue_returns_all_approved_leads_when_count_is_nonzero(self):
        client, payload = self.create_seed_job("5")
        lead_ids = [lead["id"] for lead in payload["results"]]
        for lead_id in lead_ids[:3]:
            client.post(f"/api/leads/{lead_id}/approve")

        data = client.get("/api/leads?status=approved").get_json()

        self.assertEqual(data["counts"]["approved"], 3)
        self.assertEqual(len(data["leads"]), 3)
        self.assertEqual({lead["id"] for lead in data["leads"]}, set(lead_ids[:3]))
        self.assertTrue(all(lead["status"] == "approved" for lead in data["leads"]))

    def test_audit_log_records_status_change(self):
        client, payload = self.create_seed_job("1")
        lead_id = payload["results"][0]["id"]
        client.post(f"/api/leads/{lead_id}/status", json={"status": "approved"})

        with app.get_db() as conn:
            row = conn.execute(
                "SELECT action, old_status, new_status FROM audit_log WHERE lead_id = ? AND action = 'status_changed'",
                (lead_id,),
            ).fetchone()

        self.assertEqual(row["action"], "status_changed")
        self.assertEqual(row["old_status"], "generated")
        self.assertEqual(row["new_status"], "approved")

    def test_manual_contact_can_be_saved_for_approved_lead(self):
        client, payload = self.create_seed_job("1")
        lead_id = payload["results"][0]["id"]
        client.post(f"/api/leads/{lead_id}/approve")

        response = client.post(
            f"/api/leads/{lead_id}/save-contact",
            json={
                "recipient_email": "events@example.com",
                "recipient_name": "Sam Taylor",
                "recipient_role": "Events Manager",
            },
        )

        self.assertEqual(response.status_code, 200)
        lead = response.get_json()["lead"]
        self.assertEqual(lead["recipient_email"], "events@example.com")
        self.assertEqual(lead["recipient_name"], "Sam Taylor")
        self.assertEqual(lead["recipient_role"], "Events Manager")
        self.assertEqual(lead["contact_status"], "manual")
        self.assertEqual(lead["email_verification_status"], "unverified")

    def test_website_contact_discovery_finds_preferred_email(self):
        client, payload = self.create_seed_job("1")
        lead_id = payload["results"][0]["id"]
        client.post(f"/api/leads/{lead_id}/approve")

        with patch("app.extract_emails_from_website", return_value=["events@example.com", "info@example.com"]):
            response = client.post(f"/api/leads/{lead_id}/discover-contact")

        self.assertEqual(response.status_code, 200)
        lead = response.get_json()["lead"]
        self.assertEqual(lead["recipient_email"], "events@example.com")
        self.assertEqual(lead["contact_source"], "website")
        self.assertEqual(lead["contact_status"], "found")

    def test_verify_without_hunter_keeps_unverified_warning(self):
        client, payload = self.create_seed_job("1")
        lead_id = payload["results"][0]["id"]
        client.post(f"/api/leads/{lead_id}/approve")
        client.post(f"/api/leads/{lead_id}/save-contact", json={"recipient_email": "events@example.com"})

        with patch.dict(os.environ, {"HUNTER_API_KEY": ""}, clear=False):
            response = client.post(f"/api/leads/{lead_id}/verify-email")

        self.assertEqual(response.status_code, 200)
        lead = response.get_json()["lead"]
        self.assertEqual(lead["email_verification_status"], "unverified")
        self.assertIn("Hunter is not configured", lead["email_verification_reason"])

    def test_verify_with_hunter_marks_valid_ready(self):
        client, payload = self.create_seed_job("1")
        lead_id = payload["results"][0]["id"]
        client.post(f"/api/leads/{lead_id}/approve")
        client.post(f"/api/leads/{lead_id}/save-contact", json={"recipient_email": "events@example.com"})
        hunter_response = {"data": {"status": "valid", "score": 100}}

        with patch.dict(os.environ, {"HUNTER_API_KEY": "hunter-key"}, clear=False):
            with patch("app.request_json", return_value=hunter_response):
                response = client.post(f"/api/leads/{lead_id}/verify-email")

        self.assertEqual(response.status_code, 200)
        lead = response.get_json()["lead"]
        self.assertEqual(lead["email_verification_status"], "valid")
        self.assertEqual(lead["instantly_status"], "ready")

    def test_push_without_instantly_key_shows_setup_warning(self):
        client, payload = self.create_seed_job("1")
        lead_id = payload["results"][0]["id"]
        client.post(f"/api/leads/{lead_id}/approve")
        client.post(f"/api/leads/{lead_id}/save-contact", json={"recipient_email": "events@example.com"})

        with patch.dict(os.environ, {"INSTANTLY_API_KEY": ""}, clear=False):
            response = client.post("/api/instantly/push-approved", json={"confirm_unverified": True})

        self.assertEqual(response.status_code, 400)
        self.assertIn("Instantly API key is not configured", response.get_json()["error"])

    def test_push_only_approved_ready_leads_to_instantly(self):
        client, payload = self.create_seed_job("4")
        approved_id = payload["results"][0]["id"]
        rejected_id = payload["results"][1]["id"]
        suppressed_id = payload["results"][2]["id"]
        missing_id = payload["results"][3]["id"]

        client.post(f"/api/leads/{approved_id}/approve")
        client.post(f"/api/leads/{rejected_id}/reject")
        client.post(f"/api/leads/{suppressed_id}/do-not-contact")
        client.post(f"/api/leads/{missing_id}/approve")
        client.post(f"/api/leads/{approved_id}/save-contact", json={"recipient_email": "events@example.com"})

        env = {
            "INSTANTLY_API_KEY": "instantly-key",
            "INSTANTLY_CAMPAIGN_ID": "campaign-123",
            "INSTANTLY_LIST_ID": "",
        }
        with patch.dict(os.environ, env, clear=False):
            with patch("app.request_json", return_value={"ok": True}) as request_json:
                response = client.post("/api/instantly/push-approved", json={"confirm_unverified": True})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["pushed"], [approved_id])
        sent_payload = request_json.call_args.kwargs["payload"]
        self.assertEqual(sent_payload["campaign_id"], "campaign-123")
        self.assertFalse(sent_payload["verify_leads_on_import"])
        self.assertTrue(sent_payload["skip_if_in_workspace"])
        self.assertEqual(len(sent_payload["leads"]), 1)
        self.assertEqual(sent_payload["leads"][0]["email"], "events@example.com")
        self.assertEqual(request_json.call_args.kwargs["headers"]["Authorization"], "Bearer instantly-key")

        with app.get_db() as conn:
            statuses = {
                row["id"]: row["instantly_status"]
                for row in conn.execute(
                    "SELECT id, instantly_status FROM leads WHERE id IN (?, ?, ?, ?)",
                    (approved_id, rejected_id, suppressed_id, missing_id),
                )
            }
        self.assertEqual(statuses[approved_id], "pushed")
        self.assertNotEqual(statuses[rejected_id], "pushed")
        self.assertNotEqual(statuses[suppressed_id], "pushed")
        self.assertNotEqual(statuses[missing_id], "pushed")

    def test_invalid_email_is_not_pushed_to_instantly(self):
        client, payload = self.create_seed_job("1")
        lead_id = payload["results"][0]["id"]
        client.post(f"/api/leads/{lead_id}/approve")
        client.post(f"/api/leads/{lead_id}/save-contact", json={"recipient_email": "events@example.com"})
        with app.get_db() as conn:
            conn.execute(
                "UPDATE leads SET email_verification_status = 'invalid', instantly_status = 'not_ready' WHERE id = ?",
                (lead_id,),
            )

        env = {"INSTANTLY_API_KEY": "instantly-key", "INSTANTLY_CAMPAIGN_ID": "campaign-123"}
        with patch.dict(os.environ, env, clear=False):
            response = client.post("/api/instantly/push-approved", json={"confirm_unverified": True})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["pushed"], [])

    def test_request_json_sends_default_api_client_headers(self):
        fake_response = MagicMock()
        fake_response.__enter__.return_value.read.return_value = b'{"ok": true}'
        with patch("app.urlopen", return_value=fake_response) as urlopen:
            app.request_json("https://api.example.test", method="POST", payload={"x": 1}, headers={"Authorization": "Bearer test"})

        request = urlopen.call_args.args[0]
        self.assertEqual(request.headers["User-agent"], "LocalHospitalityLeadEngine/1.0")
        self.assertEqual(request.headers["Accept"], "application/json")
        self.assertEqual(request.headers["Authorization"], "Bearer test")
        self.assertEqual(request.headers["Content-type"], "application/json")

    def test_instantly_failure_stores_clear_gateway_error(self):
        client, payload = self.create_seed_job("1")
        lead_id = payload["results"][0]["id"]
        client.post(f"/api/leads/{lead_id}/approve")
        client.post(f"/api/leads/{lead_id}/save-contact", json={"recipient_email": "events@example.com"})
        error = RuntimeError("HTTP 403: error code: 1010")

        env = {"INSTANTLY_API_KEY": "instantly-key", "INSTANTLY_CAMPAIGN_ID": "campaign-123"}
        with patch.dict(os.environ, env, clear=False):
            with patch("app.request_json", side_effect=error):
                response = client.post("/api/instantly/push-approved", json={"confirm_unverified": True})

        self.assertEqual(response.status_code, 400)
        self.assertIn("Instantly rejected the request before processing", response.get_json()["error"])
        with app.get_db() as conn:
            row = conn.execute("SELECT instantly_status, instantly_error FROM leads WHERE id = ?", (lead_id,)).fetchone()
        self.assertEqual(row["instantly_status"], "failed")
        self.assertIn("Original error: HTTP 403", row["instantly_error"])

    def test_instantly_config_endpoint_hides_secret_values(self):
        client, _payload = self.create_seed_job("1")
        env = {"INSTANTLY_API_KEY": "secret-key", "INSTANTLY_CAMPAIGN_ID": "campaign-123", "INSTANTLY_LIST_ID": ""}
        with patch.dict(os.environ, env, clear=False):
            response = client.get("/api/instantly/config")

        data = response.get_json()
        self.assertTrue(data["has_api_key"])
        self.assertTrue(data["has_campaign_id"])
        self.assertEqual(data["target"], "campaign")
        self.assertTrue(data["ready"])
        self.assertNotIn("secret-key", str(data))

    def test_approved_order_stays_stable_after_contact_actions(self):
        client, payload = self.create_seed_job("3")
        lead_ids = [lead["id"] for lead in payload["results"]]
        for lead_id in lead_ids:
            client.post(f"/api/leads/{lead_id}/approve")

        before = [lead["id"] for lead in client.get("/api/leads?status=approved").get_json()["leads"]]
        client.post(f"/api/leads/{lead_ids[-1]}/save-contact", json={"recipient_email": "events@example.com"})
        with patch("app.extract_emails_from_website", return_value=["bookings@example.com"]):
            client.post(f"/api/leads/{lead_ids[-1]}/discover-contact")
        after = [lead["id"] for lead in client.get("/api/leads?status=approved").get_json()["leads"]]

        self.assertEqual(after, before)

    def test_reset_demo_data_removes_local_records_and_returns_zero_counts(self):
        client, payload = self.create_seed_job("2")
        approved_id = payload["results"][0]["id"]
        suppressed_id = payload["results"][1]["id"]
        client.post(f"/api/leads/{approved_id}/approve")
        client.post(f"/api/leads/{suppressed_id}/do-not-contact")
        client.get("/export/approved")

        response = client.post("/api/demo/reset")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["message"], "Demo data reset.")
        self.assertEqual(
            data["counts"],
            {
                "generated": 0,
                "in_review": 0,
                "approved": 0,
                "rejected": 0,
                "do_not_contact": 0,
                "exported": 0,
                "review": 0,
                "export_ready": 0,
            },
        )
        for table_name in ["leads", "lead_events", "audit_log", "exports", "suppression_list", "jobs"]:
            self.assertEqual(self.table_count(table_name), 0, table_name)

    def test_reset_demo_data_preserves_seed_cache_and_env_files(self):
        client, _payload = self.create_seed_job("1")
        paths = [
            app.SEED_PATH,
            app.CACHED_RESULTS_PATH,
            app.CACHED_LIVE_RESULTS_PATH,
            app.BASE_DIR / ".env",
        ]
        before = {path: path.read_bytes() if path.exists() else None for path in paths}

        response = client.post("/api/demo/reset")

        self.assertEqual(response.status_code, 200)
        after = {path: path.read_bytes() if path.exists() else None for path in paths}
        self.assertEqual(after, before)

    def test_seed_demo_creates_eight_generated_leads_after_reset_without_api_keys(self):
        client, _payload = self.create_seed_job("1")
        with patch.dict(
            os.environ,
            {
                "SERPAPI_API_KEY": "",
                "TAVILY_API_KEY": "",
                "GEMINI_API_KEY": "",
                "OPENAI_API_KEY": "",
                "ANTHROPIC_API_KEY": "",
                "HUNTER_API_KEY": "",
                "INSTANTLY_API_KEY": "",
            },
            clear=False,
        ):
            reset_response = client.post("/api/demo/reset")
            self.assertEqual(reset_response.status_code, 200)
            response = client.post(
                "/",
                data={
                    "brand": app.DEFAULT_CONTEXT["brand"],
                    "location": app.DEFAULT_CONTEXT["location"],
                    "offer": app.DEFAULT_CONTEXT["offer"],
                    "icp": app.DEFAULT_CONTEXT["icp"],
                    "category": app.DEFAULT_CONTEXT["category"],
                    "lead_count": "8",
                    "mode": "seed_demo",
                },
                follow_redirects=False,
            )
            job_id = response.headers["Location"].rstrip("/").split("/")[-1]
            payload = wait_for_job(client, job_id)

        self.assertEqual(payload["status"], "complete", payload)
        self.assertEqual(len(payload["results"]), 8)
        with app.get_db() as conn:
            statuses = [
                row["status"]
                for row in conn.execute(
                    "SELECT status FROM leads WHERE job_id = ? ORDER BY position",
                    (payload["job_id"],),
                )
            ]
        self.assertEqual(statuses, ["generated"] * 8)


if __name__ == "__main__":
    unittest.main()
