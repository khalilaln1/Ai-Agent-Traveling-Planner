"""Task-based travel planning workflow.

This module keeps the workflow explicit: every function represents one agent
task and returns plain dictionaries/lists that can be logged, tested, or exposed
through an API endpoint later.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
from math import ceil
from typing import Any

from .data import DESTINATIONS, INTEREST_ALIASES
from .live_tools import apply_live_context, collect_live_context, summarize_tool_availability

REQUIRED_FIELDS = [
    "departure_city",
    "destination",
    "start_date",
    "end_date",
    "travelers",
    "budget",
    "currency",
    "interests",
    "pace",
    "trip_style",
]

PACE_LIMITS = {
    "relaxed": 4.5,
    "balanced": 6.0,
    "packed": 8.0,
}


def plan_trip(raw_request: dict[str, Any]) -> dict[str, Any]:
    """Run the full planning workflow and return a structured JSON-ready result."""

    trip_request = parse_trip_request(raw_request)
    missing = check_missing_fields(trip_request)
    if missing:
        return {
            "status": "needs_clarification",
            "missing_fields": missing,
            "questions": clarification_questions(missing),
        }

    destination_profile = create_destination_profile(trip_request)
    interest_profile = match_interests(trip_request["interests"])
    activity_pool = generate_activity_pool(destination_profile, interest_profile)
    live_context: dict[str, Any] = {}
    if trip_request.get("use_live_data", True):
        live_context = collect_live_context(trip_request, destination_profile, activity_pool)
        destination_profile, activity_pool = apply_live_context(
            destination_profile, activity_pool, live_context
        )
    itinerary = build_itinerary(trip_request, activity_pool)

    validation = validate_itinerary(trip_request, itinerary)
    revision_count = 0
    while validation["status"] == "needs_revision" and revision_count < 2:
        itinerary = revise_itinerary(trip_request, itinerary, validation)
        validation = validate_itinerary(trip_request, itinerary)
        revision_count += 1

    budget = estimate_budget(trip_request, itinerary, destination_profile)
    optimizations: list[str] = []
    if budget["budget_status"] == "over_budget":
        itinerary, optimizations = optimize_if_needed(trip_request, itinerary, budget)
        validation = validate_itinerary(trip_request, itinerary)
        budget = estimate_budget(trip_request, itinerary, destination_profile)

    return generate_final_plan(
        trip_request=trip_request,
        destination_profile=destination_profile,
        interest_profile=interest_profile,
        activity_pool=activity_pool,
        itinerary=itinerary,
        validation=validation,
        budget=budget,
        optimizations=optimizations,
        live_context=live_context,
    )


def parse_trip_request(raw_request: dict[str, Any]) -> dict[str, Any]:
    """Normalize user input while preserving unknown fields for future use."""

    request = deepcopy(raw_request)
    for field in ["departure_city", "destination", "currency", "pace"]:
        if isinstance(request.get(field), str):
            request[field] = request[field].strip()

    if isinstance(request.get("interests"), str):
        request["interests"] = [
            item.strip() for item in request["interests"].split(",") if item.strip()
        ]

    if "travelers" in request and request["travelers"] is not None:
        request["travelers"] = int(request["travelers"])

    if "budget" in request and request["budget"] is not None:
        request["budget"] = float(request["budget"])

    request["pace"] = request.get("pace") or "balanced"
    request["currency"] = request.get("currency") or "USD"
    if isinstance(request.get("trip_style"), str):
        request["trip_style"] = _normalize_trip_style(request["trip_style"])
    return request


def check_missing_fields(trip_request: dict[str, Any]) -> list[str]:
    missing = []
    for field in REQUIRED_FIELDS:
        value = trip_request.get(field)
        if value is None or value == "" or value == []:
            missing.append(field)

    if trip_request.get("start_date") and not _parse_date(trip_request["start_date"]):
        missing.append("valid_start_date")
    if trip_request.get("end_date") and not _parse_date(trip_request["end_date"]):
        missing.append("valid_end_date")
    return missing


def clarification_questions(missing_fields: list[str]) -> list[str]:
    questions = {
        "departure_city": "What city are you departing from?",
        "destination": "What destination should I plan for?",
        "start_date": "What is the trip start date? Use YYYY-MM-DD.",
        "end_date": "What is the trip end date? Use YYYY-MM-DD.",
        "valid_start_date": "Please provide a valid start date in YYYY-MM-DD format.",
        "valid_end_date": "Please provide a valid end date in YYYY-MM-DD format.",
        "travelers": "How many travelers are going?",
        "budget": "What is the approximate total trip budget?",
        "currency": "What currency should the budget use?",
        "interests": "What are the main interests for the trip?",
        "pace": "What pace do you prefer: relaxed, balanced, or packed?",
        "trip_style": "What trip style should I plan: budget, solo_backpacking, or family?",
    }
    return [questions[field] for field in missing_fields if field in questions]


def create_destination_profile(trip_request: dict[str, Any]) -> dict[str, Any]:
    destination_key = _destination_key(trip_request["destination"])
    destination = DESTINATIONS.get(destination_key)

    if destination is None:
        profile = {
            "name": trip_request["destination"],
            "country": None,
            "source": "fallback_profile",
            "cost_level": "medium",
            "daily_food_per_person": 50,
            "daily_lodging_total": 150,
            "local_transport_total": 75,
            "estimated_flight_total": 800,
            "best_areas_to_stay": [
                {
                    "name": "Central transit-friendly area",
                    "fit": "first-time visitors",
                    "tradeoff": "likely higher prices",
                }
            ],
            "weather_notes": ["Check weather close to departure before packing."],
            "transport_notes": ["Prefer public transport or short taxi rides where possible."],
            "must_know": ["Confirm opening days and booking requirements before departure."],
        }
        return _apply_trip_style_to_profile(profile, trip_request["trip_style"])

    departure_key = str(trip_request["departure_city"]).lower()
    estimated_flight_total = destination["flight_estimates"].get(departure_key, 800)
    profile = {
        "name": destination["display_name"],
        "country": destination["country"],
        "source": "built_in_profile",
        "cost_level": destination["cost_level"],
        "daily_food_per_person": destination["daily_food_per_person"],
        "daily_lodging_total": destination["daily_lodging_total"],
        "local_transport_total": destination["local_transport_total"],
        "estimated_flight_total": estimated_flight_total,
        "best_areas_to_stay": deepcopy(destination["areas"]),
        "weather_notes": list(destination["weather_notes"]),
        "transport_notes": list(destination["transport_notes"]),
        "must_know": list(destination["must_know"]),
    }
    return _apply_trip_style_to_profile(profile, trip_request["trip_style"])


def match_interests(interests: list[str]) -> dict[str, list[str]]:
    matched: dict[str, list[str]] = {}
    for interest in interests:
        normalized = interest.lower().strip()
        category = normalized
        for alias_category, aliases in INTEREST_ALIASES.items():
            if normalized in aliases or alias_category in normalized:
                category = alias_category
                break
        matched.setdefault(category, []).append(interest)
    return matched


def generate_activity_pool(
    destination_profile: dict[str, Any], interest_profile: dict[str, list[str]]
) -> list[dict[str, Any]]:
    destination_key = _destination_key(destination_profile["name"])
    destination = DESTINATIONS.get(destination_key)
    wanted_categories = set(interest_profile.keys())

    if destination is None:
        return _fallback_activities(wanted_categories)

    activities = []
    for activity in destination["activities"]:
        if activity["category"] in wanted_categories:
            activities.append(_activity(activity))

    if not activities:
        return _fallback_activities(wanted_categories)

    return sorted(activities, key=lambda item: item["priority"], reverse=True)


def build_itinerary(
    trip_request: dict[str, Any], activity_pool: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    days = trip_days(trip_request)
    pace_limit = PACE_LIMITS.get(str(trip_request["pace"]).lower(), PACE_LIMITS["balanced"])
    remaining = deepcopy(activity_pool)
    itinerary = []

    for day_number in range(1, days + 1):
        day_limit = min(pace_limit, 3.5) if day_number in {1, days} else pace_limit
        selected = _select_day_activities(remaining, day_limit)
        used_names = {activity["name"] for activity in selected}
        remaining = [activity for activity in remaining if activity["name"] not in used_names]

        itinerary.append(
            {
                "day": day_number,
                "date": _date_for_day(trip_request["start_date"], day_number),
                "theme": _day_theme(day_number, days, selected),
                "morning": _slot(selected, "morning"),
                "afternoon": _slot(selected, "afternoon"),
                "evening": _slot(selected, "evening"),
                "notes": _day_notes(day_number, days),
            }
        )

    return itinerary


def validate_itinerary(
    trip_request: dict[str, Any], itinerary: list[dict[str, Any]]
) -> dict[str, Any]:
    pace_limit = PACE_LIMITS.get(str(trip_request["pace"]).lower(), PACE_LIMITS["balanced"])
    issues = []

    for day in itinerary:
        activities = _day_activities(day)
        total_hours = sum(activity["duration_hours"] for activity in activities)
        major_count = sum(1 for activity in activities if activity["duration_hours"] >= 1.5)
        areas = {activity["area"] for activity in activities if activity["area"]}
        is_single_full_day = (
            len(activities) == 1
            and activities[0]["best_time"] == "full_day"
            and day["day"] not in {1, len(itinerary)}
        )

        limit = min(pace_limit, 3.5) if day["day"] in {1, len(itinerary)} else pace_limit
        if total_hours > limit and not is_single_full_day:
            issues.append(
                {
                    "day": day["day"],
                    "type": "too_packed",
                    "message": f"Day {day['day']} has {total_hours:.1f} planned hours for a {trip_request['pace']} pace.",
                }
            )
        if major_count > 3:
            issues.append(
                {
                    "day": day["day"],
                    "type": "too_many_major_activities",
                    "message": f"Day {day['day']} has more than three major activities.",
                }
            )
        if len(areas) > 2:
            issues.append(
                {
                    "day": day["day"],
                    "type": "geography",
                    "message": f"Day {day['day']} jumps across too many areas: {', '.join(sorted(areas))}.",
                }
            )

    return {
        "status": "ok" if not issues else "needs_revision",
        "issues": issues,
    }


def revise_itinerary(
    trip_request: dict[str, Any],
    itinerary: list[dict[str, Any]],
    validation: dict[str, Any],
) -> list[dict[str, Any]]:
    revised = deepcopy(itinerary)
    issue_days = {issue["day"] for issue in validation.get("issues", [])}

    for day in revised:
        if day["day"] not in issue_days:
            continue
        activities = sorted(_day_activities(day), key=lambda item: item["priority"])
        if not activities:
            continue
        to_remove = activities[0]["name"]
        for slot_name in ["evening", "afternoon", "morning"]:
            day[slot_name] = [
                activity for activity in day[slot_name] if activity["name"] != to_remove
            ]
        day["notes"].append(f"Removed lower-priority activity to keep the day realistic: {to_remove}.")

    return revised


def estimate_budget(
    trip_request: dict[str, Any],
    itinerary: list[dict[str, Any]],
    destination_profile: dict[str, Any],
) -> dict[str, Any]:
    days = len(itinerary)
    nights = max(days - 1, 1)
    travelers = trip_request["travelers"]
    activity_total = sum(
        activity["cost"] * travelers for day in itinerary for activity in _day_activities(day)
    )
    flights = destination_profile["estimated_flight_total"] * travelers
    lodging = destination_profile["daily_lodging_total"] * nights
    food = destination_profile["daily_food_per_person"] * travelers * days
    transport = destination_profile["local_transport_total"]
    buffer = ceil((flights + lodging + food + transport + activity_total) * 0.1)
    estimated_total = flights + lodging + food + transport + activity_total + buffer
    budget = trip_request["budget"]

    if estimated_total <= budget:
        status = "within_budget"
    elif estimated_total <= budget * 1.12:
        status = "near_budget"
    else:
        status = "over_budget"

    return {
        "currency": trip_request["currency"],
        "flights": round(flights, 2),
        "lodging": round(lodging, 2),
        "food": round(food, 2),
        "local_transport": round(transport, 2),
        "activities": round(activity_total, 2),
        "buffer": round(buffer, 2),
        "estimated_total": round(estimated_total, 2),
        "target_budget": round(budget, 2),
        "budget_status": status,
    }


def optimize_if_needed(
    trip_request: dict[str, Any],
    itinerary: list[dict[str, Any]],
    budget: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    optimized = deepcopy(itinerary)
    optimizations = []
    paid_activities = sorted(
        [
            (day, activity)
            for day in optimized
            for activity in _day_activities(day)
            if activity["cost"] > 0 and activity["priority"] <= 7
        ],
        key=lambda pair: (pair[1]["priority"], -pair[1]["cost"]),
    )

    for day, activity in paid_activities[:2]:
        for slot_name in ["morning", "afternoon", "evening"]:
            before = len(day[slot_name])
            day[slot_name] = [
                item for item in day[slot_name] if item["name"] != activity["name"]
            ]
            if len(day[slot_name]) != before:
                break
        replacement = {
            "name": f"Free self-guided walk in {activity['area']}",
            "category": "walkable neighborhoods",
            "duration_hours": min(activity["duration_hours"], 1.5),
            "best_time": activity["best_time"],
            "area": activity["area"],
            "cost": 0,
            "booking_needed": False,
            "priority": activity["priority"],
        }
        day["afternoon"].append(replacement)
        optimizations.append(
            f"Replaced {activity['name']} with a free self-guided option in {activity['area']}."
        )

    if budget["budget_status"] == "over_budget":
        optimizations.append("Consider a cheaper stay area or shifting dates if live prices are high.")

    return optimized, optimizations


def generate_final_plan(
    *,
    trip_request: dict[str, Any],
    destination_profile: dict[str, Any],
    interest_profile: dict[str, list[str]],
    activity_pool: list[dict[str, Any]],
    itinerary: list[dict[str, Any]],
    validation: dict[str, Any],
    budget: dict[str, Any],
    optimizations: list[str],
    live_context: dict[str, Any],
) -> dict[str, Any]:
    ai_research = live_context.get("ai_research", {})
    return {
        "status": "complete",
        "trip_summary": {
            "destination": destination_profile["name"],
            "country": destination_profile.get("country"),
            "dates": {
                "start": trip_request["start_date"],
                "end": trip_request["end_date"],
                "days": len(itinerary),
            },
            "travelers": trip_request["travelers"],
            "pace": trip_request["pace"],
            "interests": trip_request["interests"],
            "trip_style": trip_request["trip_style"],
        },
        "destination_profile": {
            "cost_level": destination_profile["cost_level"],
            "best_areas_to_stay": destination_profile["best_areas_to_stay"],
            "weather_notes": destination_profile["weather_notes"],
            "transport_notes": destination_profile["transport_notes"],
            "must_know": destination_profile["must_know"],
        },
        "interest_profile": interest_profile,
        "activity_pool": activity_pool,
        "itinerary": itinerary,
        "validation": validation,
        "budget": budget,
        "live_context": live_context,
        "tool_availability": summarize_tool_availability(live_context) if live_context else {},
        "ai_reasoning": ai_research.get("result") if ai_research.get("available") else None,
        "ai_response": build_ai_response(
            trip_request, destination_profile, itinerary, budget, validation, live_context
        ),
        "neighborhood_recommendation": destination_profile["best_areas_to_stay"][0],
        "packing_list": packing_list(destination_profile),
        "booking_checklist": booking_checklist(itinerary),
        "optimizations": optimizations,
        "warnings": warnings(validation, budget),
    }


def trip_days(trip_request: dict[str, Any]) -> int:
    start = _parse_date(trip_request["start_date"])
    end = _parse_date(trip_request["end_date"])
    if start is None or end is None:
        return 1
    return max((end - start).days + 1, 1)


def packing_list(destination_profile: dict[str, Any]) -> list[str]:
    items = [
        "Passport or government ID",
        "Travel insurance details",
        "Phone charger and power bank",
        "Comfortable walking shoes",
        "Reusable water bottle",
    ]
    notes = " ".join(destination_profile.get("weather_notes", [])).lower()
    if "warm" in notes or "hot" in notes:
        items.extend(["Sun protection", "Light breathable clothing"])
    if "cooler" in notes:
        items.append("Light jacket for evenings")
    return items


def booking_checklist(itinerary: list[dict[str, Any]]) -> list[str]:
    checklist = ["Book accommodation", "Confirm airport transfer plan"]
    for activity in [activity for day in itinerary for activity in _day_activities(day)]:
        if activity["booking_needed"]:
            checklist.append(f"Reserve: {activity['name']}")
    return list(dict.fromkeys(checklist))


def warnings(validation: dict[str, Any], budget: dict[str, Any]) -> list[str]:
    result = []
    if validation["status"] != "ok":
        result.append("Some itinerary realism issues remain after revision.")
    if budget["budget_status"] == "over_budget":
        result.append("Estimated cost is still over the target budget.")
    elif budget["budget_status"] == "near_budget":
        result.append("Estimated cost is close to the target budget; live prices may push it over.")
    return result


def build_ai_response(
    trip_request: dict[str, Any],
    destination_profile: dict[str, Any],
    itinerary: list[dict[str, Any]],
    budget: dict[str, Any],
    validation: dict[str, Any],
    live_context: dict[str, Any],
) -> str:
    style = trip_request["trip_style"].replace("_", " ")
    lines = [
        f"Here is a {style} plan for {destination_profile['name']} from {trip_request['start_date']} to {trip_request['end_date']} for {trip_request['travelers']} traveler(s).",
        f"The plan uses a {trip_request['pace']} pace and focuses on {', '.join(trip_request['interests'])}.",
        "",
        f"Best base: {destination_profile['best_areas_to_stay'][0]['name']} - {destination_profile['best_areas_to_stay'][0]['fit']}.",
        f"Weather note: {' '.join(destination_profile.get('weather_notes', []))}",
        f"Budget estimate: {budget['estimated_total']} {budget['currency']} against a target of {budget['target_budget']} {budget['currency']} ({budget['budget_status']}).",
        "",
        "Day-by-day plan:",
    ]

    for day in itinerary:
        activities = _day_activities(day)
        if activities:
            names = ", ".join(activity["name"] for activity in activities)
        else:
            names = "open buffer day for rest, laundry, errands, or optional local finds"
        lines.append(f"Day {day['day']} ({day['date']}): {day['theme']}. {names}.")

    lines.extend(["", "Practical advice:"])
    if trip_request["trip_style"] == "budget":
        lines.append("Prioritize public transport, free walks, markets, and casual food spots.")
    elif trip_request["trip_style"] == "solo_backpacking":
        lines.append("Keep luggage light, choose social central lodging, and avoid late isolated transfers.")
    elif trip_request["trip_style"] == "family":
        lines.append("Keep mornings structured, afternoons flexible, and add rest time between major activities.")

    ai_research = live_context.get("ai_research", {})
    if ai_research.get("available"):
        for warning in _as_list(ai_research.get("result", {}).get("warnings", [])):
            lines.append(f"Note: {warning}")

    if validation["status"] != "ok":
        lines.append("Some itinerary realism issues remain; review validation details before booking.")
    if budget["budget_status"] == "over_budget":
        lines.append("The current estimate is over budget, so reduce flight/lodging costs or remove paid activities.")

    pdf_path = "outputs\\travel_plan.pdf"
    lines.append(f"A PDF export can be generated with the API or CLI; default path example: {pdf_path}.")
    return "\n".join(lines)


def _apply_trip_style_to_profile(profile: dict[str, Any], trip_style: str) -> dict[str, Any]:
    adjusted = dict(profile)
    if trip_style == "budget":
        adjusted["daily_food_per_person"] = round(adjusted["daily_food_per_person"] * 0.75, 2)
        adjusted["daily_lodging_total"] = round(adjusted["daily_lodging_total"] * 0.75, 2)
        adjusted["must_know"] = adjusted["must_know"] + [
            "Budget mode favors public transport, free sights, and casual food."
        ]
    elif trip_style == "solo_backpacking":
        adjusted["daily_food_per_person"] = round(adjusted["daily_food_per_person"] * 0.65, 2)
        adjusted["daily_lodging_total"] = round(adjusted["daily_lodging_total"] * 0.45, 2)
        adjusted["must_know"] = adjusted["must_know"] + [
            "Solo backpacking mode assumes hostel-style lodging and flexible meals."
        ]
    elif trip_style == "family":
        adjusted["daily_food_per_person"] = round(adjusted["daily_food_per_person"] * 1.1, 2)
        adjusted["daily_lodging_total"] = round(adjusted["daily_lodging_total"] * 1.25, 2)
        adjusted["must_know"] = adjusted["must_know"] + [
            "Family mode keeps extra rest time and assumes more comfortable lodging."
        ]
    return adjusted


def _destination_key(destination: str) -> str:
    return destination.lower().strip()


def _normalize_trip_style(value: str) -> str:
    normalized = value.lower().strip().replace(" ", "_").replace("-", "_")
    aliases = {
        "budget_trip": "budget",
        "cheap": "budget",
        "backpacking": "solo_backpacking",
        "solo": "solo_backpacking",
        "solo_backpacker": "solo_backpacking",
        "family_trip": "family",
        "kids": "family",
    }
    return aliases.get(normalized, normalized)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _parse_date(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _date_for_day(start_date: str, day_number: int) -> str:
    start = _parse_date(start_date)
    if start is None:
        return ""
    return (start + timedelta(days=day_number - 1)).isoformat()


def _select_day_activities(
    remaining: list[dict[str, Any]], day_limit: float
) -> list[dict[str, Any]]:
    if not remaining:
        return []

    first = remaining[0]
    preferred_area = first["area"]
    selected = [first]
    total = first["duration_hours"]

    for activity in remaining[1:]:
        if activity["area"] != preferred_area and len(selected) >= 2:
            continue
        if total + activity["duration_hours"] <= day_limit and len(selected) < 3:
            selected.append(activity)
            total += activity["duration_hours"]

    return selected


def _slot(activities: list[dict[str, Any]], slot_name: str) -> list[dict[str, Any]]:
    if slot_name == "morning":
        return [
            activity
            for activity in activities
            if activity["best_time"] in {"morning", "full_day"}
        ]
    return [activity for activity in activities if activity["best_time"] == slot_name]


def _day_theme(day_number: int, total_days: int, activities: list[dict[str, Any]]) -> str:
    if day_number == 1:
        return "Arrival and easy orientation"
    if day_number == total_days:
        return "Final walk and departure buffer"
    if activities:
        areas = list(dict.fromkeys(activity["area"] for activity in activities))
        return f"{areas[0]} focused day"
    return "Open day"


def _day_notes(day_number: int, total_days: int) -> list[str]:
    if day_number == 1:
        return ["Keep this day lighter in case of flight delays or jet lag."]
    if day_number == total_days:
        return ["Leave extra time for checkout, bags, and airport transfer."]
    return ["Keep meal and transit times flexible."]


def _day_activities(day: dict[str, Any]) -> list[dict[str, Any]]:
    return day["morning"] + day["afternoon"] + day["evening"]


def _activity(activity: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": activity["name"],
        "category": activity["category"],
        "duration_hours": float(activity["duration_hours"]),
        "best_time": activity["best_time"],
        "area": activity["area"],
        "cost": float(activity["cost"]),
        "booking_needed": bool(activity["booking_needed"]),
        "priority": int(activity["priority"]),
    }


def _fallback_activities(wanted_categories: set[str]) -> list[dict[str, Any]]:
    base = [
        {
            "name": "Central neighborhood walk",
            "category": "walkable neighborhoods",
            "duration_hours": 2.0,
            "best_time": "morning",
            "area": "Central area",
            "cost": 0,
            "booking_needed": False,
            "priority": 8,
        },
        {
            "name": "Main history museum or landmark",
            "category": "history",
            "duration_hours": 2.0,
            "best_time": "afternoon",
            "area": "Central area",
            "cost": 20,
            "booking_needed": False,
            "priority": 8,
        },
        {
            "name": "Local food market",
            "category": "food",
            "duration_hours": 1.5,
            "best_time": "afternoon",
            "area": "Market district",
            "cost": 25,
            "booking_needed": False,
            "priority": 7,
        },
    ]
    filtered = [item for item in base if item["category"] in wanted_categories]
    return filtered or base
