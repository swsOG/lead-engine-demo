import json
import os
import time
import unittest
from unittest.mock import patch

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


class ProgressMetadataTest(unittest.TestCase):
    def setUp(self):
        app.init_db()
        with app.get_db() as conn:
            conn.execute("DELETE FROM lead_events")
            conn.execute("DELETE FROM audit_log")
            conn.execute("DELETE FROM exports")
            conn.execute("DELETE FROM suppression_list")
            conn.execute("DELETE FROM leads")
            conn.execute("DELETE FROM jobs")

    def test_queued_job_status_includes_progress_steps(self):
        params = dict(app.DEFAULT_CONTEXT, mode="seed_demo", current_step="queued")
        with app.get_db() as conn:
            conn.execute(
                """
                INSERT INTO jobs (id, status, progress, message, params, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("queued-test", "queued", 5, "Queued seed demo job", json.dumps(params), app.now_iso(), app.now_iso()),
            )

        data = app.app.test_client().get("/api/status/queued-test").get_json()

        self.assertEqual(data["current_step"], "queued")
        self.assertEqual([step["key"] for step in data["steps"]], ["queued", "preparing", "sourcing", "research", "drafting", "saving", "complete"])
        self.assertEqual(data["steps"][0]["state"], "current")
        self.assertEqual(data["steps"][2]["detail"], "Loading demo leads.")

    def test_completed_seed_job_includes_ready_for_review_step(self):
        client = app.app.test_client()
        response = client.post(
            "/",
            data={
                "brand": app.DEFAULT_CONTEXT["brand"],
                "location": app.DEFAULT_CONTEXT["location"],
                "offer": app.DEFAULT_CONTEXT["offer"],
                "icp": app.DEFAULT_CONTEXT["icp"],
                "category": app.DEFAULT_CONTEXT["category"],
                "lead_count": "3",
                "mode": "seed_demo",
            },
            follow_redirects=False,
        )
        job_id = response.headers["Location"].rstrip("/").split("/")[-1]
        payload = wait_for_job(client, job_id)

        self.assertEqual(payload["status"], "complete", payload)
        self.assertEqual(payload["current_step"], "complete")
        self.assertEqual(payload["steps"][-1]["label"], "Ready for review")
        self.assertEqual(payload["steps"][-1]["detail"], "Leads are ready.")
        self.assertTrue(all(step["state"] == "complete" for step in payload["steps"]))

    def test_live_fallback_status_preserves_badges_and_includes_steps(self):
        with patch.dict(
            os.environ,
            {
                "SERPAPI_API_KEY": "",
                "TAVILY_API_KEY": "",
                "OPENAI_API_KEY": "",
                "ANTHROPIC_API_KEY": "",
            },
            clear=False,
        ):
            client = app.app.test_client()
            response = client.post(
                "/",
                data={
                    "brand": app.DEFAULT_CONTEXT["brand"],
                    "location": app.DEFAULT_CONTEXT["location"],
                    "offer": app.DEFAULT_CONTEXT["offer"],
                    "icp": app.DEFAULT_CONTEXT["icp"],
                    "category": app.DEFAULT_CONTEXT["category"],
                    "lead_count": "3",
                    "mode": "live_if_available",
                },
                follow_redirects=False,
            )
            job_id = response.headers["Location"].rstrip("/").split("/")[-1]
            payload = wait_for_job(client, job_id)

        self.assertEqual(payload["status"], "complete", payload)
        self.assertEqual(payload["result_mode"], "seed")
        self.assertTrue(payload["fallback_used"])
        self.assertIn("Fallback used", payload["badges"])
        self.assertEqual(payload["current_step"], "complete")
        self.assertEqual(payload["steps"][2]["detail"], "Searching for local businesses.")


if __name__ == "__main__":
    unittest.main()
