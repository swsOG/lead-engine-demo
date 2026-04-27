import json
import os
import tempfile
import time
import unittest
from pathlib import Path
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


class LiveModeTest(unittest.TestCase):
    def test_load_local_env_loads_keys_when_process_env_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text("SERPAPI_API_KEY=from-env-file\n", encoding="utf-8")
            with patch.object(app, "BASE_DIR", Path(tmpdir)):
                with patch.dict(os.environ, {"SERPAPI_API_KEY": ""}, clear=False):
                    os.environ.pop("SERPAPI_API_KEY", None)
                    app.load_local_env()
                    self.assertEqual(os.environ["SERPAPI_API_KEY"], "from-env-file")

    def test_load_local_env_loads_keys_when_process_env_blank(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text("SERPAPI_API_KEY=from-env-file\n", encoding="utf-8")
            with patch.object(app, "BASE_DIR", Path(tmpdir)):
                with patch.dict(os.environ, {"SERPAPI_API_KEY": "   "}, clear=False):
                    app.load_local_env()
                    self.assertEqual(os.environ["SERPAPI_API_KEY"], "from-env-file")

    def test_load_local_env_handles_bom_and_quoted_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text('\ufeffSERPAPI_API_KEY="quoted-serpapi"\nTAVILY_API_KEY=\'quoted-tavily\'\n', encoding="utf-8")
            with patch.object(app, "BASE_DIR", Path(tmpdir)):
                with patch.dict(os.environ, {"SERPAPI_API_KEY": "", "TAVILY_API_KEY": ""}, clear=False):
                    app.load_local_env()
                    self.assertEqual(os.environ["SERPAPI_API_KEY"], "quoted-serpapi")
                    self.assertEqual(os.environ["TAVILY_API_KEY"], "quoted-tavily")

    def test_live_readiness_includes_safe_env_diagnostics_without_secret_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text("SERPAPI_API_KEY=secret-from-file\nGEMINI_API_KEY=gemini-secret\n", encoding="utf-8")
            with patch.object(app, "BASE_DIR", Path(tmpdir)):
                with patch.dict(os.environ, {"SERPAPI_API_KEY": "", "GEMINI_API_KEY": ""}, clear=False):
                    app.load_local_env()
                    payload = app.app.test_client().get("/api/live-readiness").get_json()

        self.assertTrue(payload["has_env_file"])
        self.assertTrue(payload["env_file_readable"])
        self.assertIn(str(env_path), payload["env_file_path"])
        self.assertEqual(payload["base_dir"], str(Path(tmpdir)))
        self.assertTrue(payload["env_keys_present"]["SERPAPI_API_KEY"])
        self.assertTrue(payload["env_keys_present"]["GEMINI_API_KEY"])
        self.assertTrue(payload["process_keys_present"]["SERPAPI_API_KEY"])
        self.assertTrue(payload["process_keys_present"]["GEMINI_API_KEY"])
        serialized = json.dumps(payload)
        self.assertNotIn("secret-from-file", serialized)
        self.assertNotIn("gemini-secret", serialized)

    def test_parse_mode_accepts_live_required(self):
        self.assertEqual(app.parse_mode("live_required"), "live_required")

    def test_run_mode_routes_live_required_to_live_only_path(self):
        params = dict(app.DEFAULT_CONTEXT, mode="live_required", lead_count=1)
        with patch("app.run_live_required", return_value=([], "live", False)) as live_required:
            results, result_mode, fallback_used = app.run_mode(params)

        live_required.assert_called_once_with(params)
        self.assertEqual(results, [])
        self.assertEqual(result_mode, "live")
        self.assertFalse(fallback_used)

    def test_live_required_without_serpapi_fails_instead_of_returning_seed(self):
        with patch.dict(
            os.environ,
            {
                "SERPAPI_API_KEY": "",
                "TAVILY_API_KEY": "",
                "GEMINI_API_KEY": "",
                "OPENAI_API_KEY": "",
                "ANTHROPIC_API_KEY": "",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "Live search failed"):
                app.run_mode(dict(app.DEFAULT_CONTEXT, mode="live_required", lead_count=3))

    def test_live_required_with_mocked_serpapi_returns_live_results(self):
        serpapi_payload = {
            "local_results": [
                {
                    "title": "Live Search Co",
                    "type": "Marketing agency",
                    "address": "1 Live Street, London",
                    "website": "https://live-search.example",
                    "rating": 4.5,
                }
            ]
        }
        with patch.dict(
            os.environ,
            {
                "SERPAPI_API_KEY": "test-serpapi-key",
                "TAVILY_API_KEY": "",
                "GEMINI_API_KEY": "",
                "OPENAI_API_KEY": "",
                "ANTHROPIC_API_KEY": "",
            },
            clear=False,
        ):
            with patch("app.request_json", return_value=serpapi_payload):
                results, result_mode, fallback_used = app.run_mode(
                    dict(app.DEFAULT_CONTEXT, mode="live_required", lead_count=1)
                )

        self.assertEqual(result_mode, "live")
        self.assertTrue(results)
        self.assertEqual(results[0]["business_name"], "Live Search Co")
        self.assertEqual(results[0]["lead_source"], "SerpAPI Google Maps")
        self.assertTrue(fallback_used)

    def test_failed_live_required_job_status_includes_clear_error_and_progress(self):
        with app.get_db() as conn:
            conn.execute("DELETE FROM jobs")
            conn.execute("DELETE FROM leads")
        with patch.dict(
            os.environ,
            {
                "SERPAPI_API_KEY": "",
                "TAVILY_API_KEY": "",
                "GEMINI_API_KEY": "",
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
                    "mode": "live_required",
                },
                follow_redirects=False,
            )
            job_id = response.headers["Location"].rstrip("/").split("/")[-1]
            payload = wait_for_job(client, job_id)

        self.assertEqual(payload["status"], "failed", payload)
        self.assertEqual(payload["requested_mode"], "live_required")
        self.assertIn("Live search failed", payload["error"])
        self.assertEqual(payload["current_step"], "drafting")
        self.assertEqual(len(payload["steps"]), 7)
        self.assertTrue(any(step["state"] == "failed" for step in payload["steps"]))

    def test_live_readiness_returns_safe_booleans_without_secret_values(self):
        with patch.dict(
            os.environ,
            {
                "SERPAPI_API_KEY": "serp-secret",
                "TAVILY_API_KEY": "tavily-secret",
                "GEMINI_API_KEY": "gemini-secret",
                "OPENAI_API_KEY": "",
                "ANTHROPIC_API_KEY": "",
            },
            clear=False,
        ):
            response = app.app.test_client().get("/api/live-readiness")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        expected_keys = {
            "has_serpapi",
            "has_tavily",
            "has_gemini",
            "has_openai",
            "has_anthropic",
            "has_any_llm",
            "live_sourcing_ready",
            "live_research_ready",
            "live_drafting_ready",
            "likely_behavior",
            "has_env_file",
            "env_file_path",
            "env_file_readable",
            "env_keys_present",
            "process_keys_present",
            "base_dir",
        }
        self.assertEqual(set(payload), expected_keys)
        for key in {
            "has_serpapi",
            "has_tavily",
            "has_gemini",
            "has_openai",
            "has_anthropic",
            "has_any_llm",
            "live_sourcing_ready",
            "live_research_ready",
            "live_drafting_ready",
            "has_env_file",
            "env_file_readable",
        }:
            self.assertIsInstance(payload[key], bool)
        serialized = json.dumps(payload)
        self.assertNotIn("serp-secret", serialized)
        self.assertNotIn("tavily-secret", serialized)
        self.assertNotIn("gemini-secret", serialized)

    def test_live_readiness_missing_sourcing_key_reports_seed_fallback(self):
        with patch.dict(
            os.environ,
            {
                "SERPAPI_API_KEY": "",
                "TAVILY_API_KEY": "",
                "GEMINI_API_KEY": "",
                "OPENAI_API_KEY": "",
                "ANTHROPIC_API_KEY": "",
            },
            clear=False,
        ):
            payload = app.app.test_client().get("/api/live-readiness").get_json()

        self.assertFalse(payload["live_sourcing_ready"])
        self.assertFalse(payload["has_any_llm"])
        self.assertEqual(
            payload["likely_behavior"],
            "Live mode will fall back to seed demo because SERPAPI_API_KEY is missing.",
        )

    def test_live_readiness_with_serpapi_and_llm_reports_partial_live_capability(self):
        with patch.dict(
            os.environ,
            {
                "SERPAPI_API_KEY": "serp-secret",
                "TAVILY_API_KEY": "",
                "GEMINI_API_KEY": "gemini-secret",
                "OPENAI_API_KEY": "",
                "ANTHROPIC_API_KEY": "",
            },
            clear=False,
        ):
            payload = app.app.test_client().get("/api/live-readiness").get_json()

        self.assertTrue(payload["has_serpapi"])
        self.assertTrue(payload["has_gemini"])
        self.assertTrue(payload["has_any_llm"])
        self.assertTrue(payload["live_sourcing_ready"])
        self.assertFalse(payload["live_research_ready"])
        self.assertTrue(payload["live_drafting_ready"])
        self.assertEqual(
            payload["likely_behavior"],
            "Live sourcing is available, but research/drafting may use fallback logic.",
        )

    def test_live_mode_without_keys_falls_back_to_seed(self):
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
                    "lead_count": "5",
                    "mode": "live_if_available",
                },
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 302)
            job_id = response.headers["Location"].rstrip("/").split("/")[-1]
            payload = wait_for_job(client, job_id)

        self.assertEqual(payload["status"], "complete", payload)
        self.assertEqual(payload["requested_mode"], "live_if_available")
        self.assertEqual(payload["result_mode"], "seed")
        self.assertTrue(payload["fallback_used"])
        self.assertIn("Fallback used", payload["badges"])
        self.assertEqual(payload["current_step"], "complete")
        self.assertEqual(len(payload["steps"]), 7)
        self.assertEqual(len(payload["results"]), 5)

    def test_serpapi_results_are_normalized(self):
        serpapi_payload = {
            "local_results": [
                {
                    "title": "Example Legal LLP",
                    "type": "Law firm",
                    "address": "1 Example Street, London",
                    "phone": "020 0000 0000",
                    "rating": 4.7,
                    "website": "https://example-legal.test",
                    "reviews_link": "https://serpapi.test/reviews",
                    "place_id_search": "https://serpapi.test/place",
                }
            ]
        }
        with patch.dict(os.environ, {"SERPAPI_API_KEY": "test-key"}, clear=False):
            with patch("app.request_json", return_value=serpapi_payload):
                params = dict(app.DEFAULT_CONTEXT, category="law firms", location="Farringdon London")
                leads = app.source_live_leads(params)

        self.assertEqual(leads[0]["name"], "Example Legal LLP")
        self.assertEqual(leads[0]["source"], "SerpAPI Google Maps")
        self.assertIn("https://example-legal.test", leads[0]["source_urls"])

    def test_cached_live_mode_loads_cached_results(self):
        cached = {
            "results": [
                {
                    "business_name": "Cached Live Co",
                    "category": "Technology",
                    "address": "London",
                    "website": "https://cached-live.test",
                    "fit_score": 72,
                    "reason": "Cached live result.",
                    "suggested_contact_role": "Office Manager",
                    "research_summary": "Cached live research.",
                    "signals": ["Cached signal"],
                    "source_urls": ["https://cached-live.test/about"],
                    "confidence": "medium",
                    "email_subject": "Cached subject",
                    "email_body": "Cached body",
                    "lead_source": "SerpAPI Google Maps",
                    "research_mode": "live",
                    "badges": ["Live", "Cached"],
                }
            ]
        }
        cache_path = app.BASE_DIR / "data" / "_test_cached_live_results.json"
        try:
            cache_path.write_text(json.dumps(cached), encoding="utf-8")
            with patch.object(app, "CACHED_LIVE_RESULTS_PATH", cache_path):
                client = app.app.test_client()
                response = client.post(
                    "/",
                    data={
                        "brand": app.DEFAULT_CONTEXT["brand"],
                        "location": app.DEFAULT_CONTEXT["location"],
                        "offer": app.DEFAULT_CONTEXT["offer"],
                        "icp": app.DEFAULT_CONTEXT["icp"],
                        "category": app.DEFAULT_CONTEXT["category"],
                        "lead_count": "5",
                        "load_cached_live": "1",
                    },
                    follow_redirects=False,
                )
                job_id = response.headers["Location"].rstrip("/").split("/")[-1]
                payload = wait_for_job(client, job_id)

            self.assertEqual(payload["status"], "complete", payload)
            self.assertEqual(payload["result_mode"], "cached")
            self.assertEqual(payload["results"][0]["business_name"], "Cached Live Co")
        finally:
            cache_path.unlink(missing_ok=True)

    def test_gemini_output_is_used_before_other_llms(self):
        lead = {
            "name": "Example Legal LLP",
            "category": "Law firm",
            "address": "1 Example Street, London",
            "website": "https://example-legal.test",
            "source_urls": ["https://example-legal.test"],
            "source": "SerpAPI Google Maps",
            "lead_source": "SerpAPI Google Maps",
        }
        research = {
            "summary": "Example Legal LLP advises corporate clients and publishes partner updates.",
            "signals": ["The firm advises corporate clients and has partner-led services."],
            "source_urls": ["https://example-legal.test/about"],
            "confidence": "high",
            "research_mode": "live",
        }
        score_payload = {
            "score": 8,
            "reason": "Corporate legal work creates credible private dining moments.",
            "contact_role": "Practice Manager",
        }
        draft_payload = {
            "subject": "Private client dinner idea",
            "body": (
                "Hi Practice Manager,\n\n"
                "Example Legal LLP's corporate client work suggests partner-led hosting moments where privacy matters. "
                "For Humble Grape / Vivat Bacchus, the useful angle is a private dining option for client dinners after meetings, "
                "rather than a broad events pitch. It gives partners somewhere central and considered for a small group conversation, "
                "with enough polish for clients but without the formality of a hotel dining room.\n\n"
                "Would a short private dining note with two suitable formats and sample timings be useful?\n\n"
                "Best,\n"
                "Events Team — on behalf of Humble Grape / Vivat Bacchus"
            ),
            "angle": "private client dinner",
        }

        with patch.dict(
            os.environ,
            {"GEMINI_API_KEY": "test-gemini-key", "OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": ""},
            clear=False,
        ):
            with patch("app.call_gemini_score_lead", return_value=score_payload) as gemini_score:
                with patch("app.call_gemini_draft_email", return_value=draft_payload) as gemini_draft:
                    with patch("app.call_openai_llm") as openai_call:
                        result = app.live_lead_result(lead, research, app.DEFAULT_CONTEXT)

        self.assertTrue(gemini_score.called)
        self.assertTrue(gemini_draft.called)
        self.assertFalse(openai_call.called)
        self.assertEqual(result["provider_used"], "Gemini")
        self.assertIn("Gemini", result["badges"])
        self.assertEqual(result["fit_score"], 80)
        self.assertEqual(result["suggested_contact_role"], "Practice Manager")

    def test_gemini_failure_falls_back_safely(self):
        lead = {
            "name": "Fallback Co",
            "category": "Marketing agency",
            "address": "London",
            "website": "https://fallback.test",
            "source_urls": ["https://fallback.test"],
            "source": "SerpAPI Google Maps",
            "lead_source": "SerpAPI Google Maps",
        }
        research = {
            "summary": "Fallback Co is a marketing agency.",
            "signals": ["Inference: marketing agencies may host clients after workshops."],
            "source_urls": ["https://fallback.test"],
            "confidence": "medium",
            "research_mode": "live",
        }

        with patch.dict(
            os.environ,
            {
                "GEMINI_API_KEY": "test-gemini-key",
                "OPENAI_API_KEY": "",
                "ANTHROPIC_API_KEY": "",
            },
            clear=False,
        ):
            with patch("app.call_gemini_score_lead", side_effect=RuntimeError("Gemini failed")):
                result = app.live_lead_result(lead, research, app.DEFAULT_CONTEXT)

        self.assertEqual(result["provider_used"], "Fallback")
        self.assertIn("Fallback", result["badges"])
        self.assertTrue(result["fallback_used"])


if __name__ == "__main__":
    unittest.main()
