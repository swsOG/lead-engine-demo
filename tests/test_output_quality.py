import json
import time
import unittest
from difflib import SequenceMatcher

import app


REQUIRED_FIELDS = {
    "business_name",
    "category",
    "address",
    "website",
    "fit_score",
    "reason",
    "suggested_contact_role",
    "research_summary",
    "signals",
    "source_urls",
    "confidence",
    "email_subject",
    "email_body",
}

APPROVED_SOURCE_LABELS = {
    "Demo research profile",
    "Seeded local proximity signal",
    "Demo event-use-case assumption",
}


def similarity(left, right):
    return SequenceMatcher(None, left.lower(), right.lower()).ratio()


class OutputQualityTest(unittest.TestCase):
    def test_default_job_returns_distinct_seed_intelligence(self):
        client = app.app.test_client()
        response = client.post(
            "/",
            data={
                "brand": app.DEFAULT_CONTEXT["brand"],
                "location": app.DEFAULT_CONTEXT["location"],
                "offer": app.DEFAULT_CONTEXT["offer"],
                "icp": app.DEFAULT_CONTEXT["icp"],
                "category": app.DEFAULT_CONTEXT["category"],
                "lead_count": "8",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        job_id = response.headers["Location"].rstrip("/").split("/")[-1]

        payload = {}
        for _ in range(30):
            status_response = client.get(f"/api/status/{job_id}")
            payload = status_response.get_json()
            if payload["status"] in {"complete", "failed"}:
                break
            time.sleep(0.2)

        self.assertEqual(payload["status"], "complete", payload)
        results = payload["results"]
        self.assertEqual(len(results), 8)

        reasons = []
        emails = []
        angles = set()
        for lead in results:
            self.assertTrue(REQUIRED_FIELDS.issubset(lead))
            self.assertNotIn("Strong local hospitality fit", lead["reason"])
            self.assertNotIn("I noticed", lead["email_body"])
            self.assertNotIn("strong fit", lead["email_body"].lower())
            self.assertLessEqual(len(lead["email_body"].split()), 120)
            self.assertGreaterEqual(len(lead["email_body"].split()), 80)
            self.assertTrue(set(lead["source_urls"]).issubset(APPROVED_SOURCE_LABELS))
            self.assertIn("relevance_angle", lead)
            angles.add(lead["relevance_angle"])
            reasons.append(lead["reason"])
            emails.append(lead["email_body"])

        self.assertEqual(len(angles), len(results))
        for index, reason in enumerate(reasons):
            for other in reasons[index + 1 :]:
                self.assertLess(similarity(reason, other), 0.72)
        for index, email in enumerate(emails):
            for other in emails[index + 1 :]:
                self.assertLess(similarity(email, other), 0.68)


if __name__ == "__main__":
    unittest.main()
