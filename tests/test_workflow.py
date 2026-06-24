import unittest

from travel_agent.workflow import plan_trip


class TravelPlannerWorkflowTests(unittest.TestCase):
    def test_complete_lisbon_plan(self):
        result = plan_trip(
            {
                "departure_city": "New York",
                "destination": "Lisbon",
                "start_date": "2026-09-10",
                "end_date": "2026-09-16",
                "travelers": 2,
                "budget": 2500,
                "currency": "USD",
                "interests": ["food", "history", "walkable neighborhoods"],
                "trip_style": "budget",
                "pace": "balanced",
                "use_live_data": False,
            }
        )

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["trip_summary"]["dates"]["days"], 7)
        self.assertEqual(result["validation"]["status"], "ok")
        self.assertTrue(result["itinerary"])
        self.assertIn("budget_status", result["budget"])
        self.assertIn("booking_checklist", result)

    def test_missing_fields_return_questions(self):
        result = plan_trip({"destination": "Lisbon"})

        self.assertEqual(result["status"], "needs_clarification")
        self.assertIn("departure_city", result["missing_fields"])
        self.assertIn("trip_style", result["missing_fields"])
        self.assertTrue(result["questions"])


if __name__ == "__main__":
    unittest.main()
