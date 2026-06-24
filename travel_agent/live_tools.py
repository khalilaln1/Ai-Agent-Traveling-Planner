"""Optional live-data and AI reasoning tools for the planner."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .config import Settings, load_settings
from .http_utils import get_json, post_json


def collect_live_context(
    trip_request: dict[str, Any],
    destination_profile: dict[str, Any],
    activity_pool: list[dict[str, Any]],
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or load_settings()
    destination = destination_profile["name"]

    geocoding = geocode_destination(destination, settings)
    coordinates = geocoding.get("coordinates")

    weather = fetch_weather(
        destination,
        trip_request,
        coordinates,
        destination_profile.get("weather_notes", []),
        settings,
    )
    places = fetch_places(
        destination,
        trip_request,
        coordinates,
        geocoding.get("country") or destination_profile.get("country"),
        settings,
    )
    flights = fetch_flights(trip_request, destination_profile, settings)
    hotels = fetch_hotels(trip_request, destination_profile, settings)
    research = ai_destination_research(
        trip_request,
        destination_profile,
        activity_pool,
        weather,
        places,
        flights,
        hotels,
        settings,
    )

    return {
        "geocoding": geocoding,
        "weather": weather,
        "places": places,
        "flights": flights,
        "hotels": hotels,
        "ai_research": research,
    }


def apply_live_context(
    destination_profile: dict[str, Any],
    activity_pool: list[dict[str, Any]],
    live_context: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    updated_profile = dict(destination_profile)
    updated_activities = list(activity_pool)

    weather = live_context.get("weather", {})
    if weather.get("available") and weather.get("summary"):
        updated_profile["weather_notes"] = [weather["summary"]]

    hotels = live_context.get("hotels", {})
    nightly_rate = hotels.get("estimated_nightly_total")
    if hotels.get("available") and nightly_rate:
        updated_profile["daily_lodging_total"] = nightly_rate

    flights = live_context.get("flights", {})
    flight_total = flights.get("estimated_roundtrip_per_person")
    if flights.get("available") and flight_total:
        updated_profile["estimated_flight_total"] = flight_total

    places = live_context.get("places", {})
    for place in places.get("items", [])[:6]:
        candidate = {
            "name": place["name"],
            "category": place.get("category", "place"),
            "duration_hours": 1.5,
            "best_time": "afternoon",
            "area": place.get("area") or updated_profile["name"],
            "cost": 0.0,
            "booking_needed": False,
            "priority": 7,
            "source": place.get("source", "places"),
            "rating": place.get("rating"),
            "address": place.get("address"),
        }
        if not any(item["name"].lower() == candidate["name"].lower() for item in updated_activities):
            updated_activities.append(candidate)

    return updated_profile, updated_activities


def geocode_destination(destination: str, settings: Settings) -> dict[str, Any]:
    try:
        data = get_json(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": destination, "count": 1, "language": "en", "format": "json"},
            timeout=settings.request_timeout_seconds,
        )
        results = data.get("results") or []
        if not results:
            return _unavailable("open_meteo_geocoding", "destination was not found")
        first = results[0]
        return {
            "available": True,
            "source": "open_meteo_geocoding",
            "coordinates": {
                "latitude": first["latitude"],
                "longitude": first["longitude"],
            },
            "name": first.get("name"),
            "country": first.get("country"),
            "timezone": first.get("timezone"),
        }
    except RuntimeError as exc:
        return _unavailable("open_meteo_geocoding", str(exc))


def fetch_weather(
    destination: str,
    trip_request: dict[str, Any],
    coordinates: dict[str, float] | None,
    seasonal_notes: list[str],
    settings: Settings,
) -> dict[str, Any]:
    if not coordinates:
        return _seasonal_weather(destination, seasonal_notes, "coordinates are unavailable")

    try:
        data = get_json(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": coordinates["latitude"],
                "longitude": coordinates["longitude"],
                "start_date": trip_request["start_date"],
                "end_date": trip_request["end_date"],
                "daily": [
                    "temperature_2m_max",
                    "temperature_2m_min",
                    "precipitation_probability_max",
                ],
                "timezone": "auto",
            },
            timeout=settings.request_timeout_seconds,
        )
        daily = data.get("daily", {})
        max_temps = daily.get("temperature_2m_max", [])
        min_temps = daily.get("temperature_2m_min", [])
        rain = daily.get("precipitation_probability_max", [])
        if not max_temps:
            return _unavailable("open_meteo_forecast", "forecast range unavailable")

        avg_high = round(sum(max_temps) / len(max_temps), 1)
        avg_low = round(sum(min_temps) / len(min_temps), 1)
        avg_rain = round(sum(rain) / len(rain), 1) if rain else None
        summary = f"{destination}: average highs around {avg_high}C and lows around {avg_low}C"
        if avg_rain is not None:
            summary += f", with average daily precipitation probability near {avg_rain}%"
        summary += "."

        return {
            "available": True,
            "source": "open_meteo_forecast",
            "summary": summary,
            "daily": daily,
            "retrieved_at": datetime.utcnow().isoformat() + "Z",
        }
    except RuntimeError as exc:
        return _seasonal_weather(destination, seasonal_notes, str(exc))


def fetch_places(
    destination: str,
    trip_request: dict[str, Any],
    coordinates: dict[str, float] | None,
    country: str | None,
    settings: Settings,
) -> dict[str, Any]:
    if not settings.google_maps_api_key:
        return _fetch_osm_places(destination, trip_request, coordinates, country, settings)
    if not coordinates:
        return _unavailable("google_places", "coordinates are unavailable")

    try:
        interest_text = " ".join(trip_request.get("interests", []))
        query = f"{interest_text} in {destination}"
        data = post_json(
            "https://places.googleapis.com/v1/places:searchText",
            payload={
                "textQuery": query,
                "maxResultCount": 10,
                "locationBias": {
                    "circle": {
                        "center": {
                            "latitude": coordinates["latitude"],
                            "longitude": coordinates["longitude"],
                        },
                        "radius": 12000.0,
                    }
                },
            },
            headers={
                "X-Goog-Api-Key": settings.google_maps_api_key,
                "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.rating,places.types",
            },
            timeout=settings.request_timeout_seconds,
        )
        items = []
        for place in data.get("places", []):
            items.append(
                {
                    "name": (place.get("displayName") or {}).get("text", "Unknown place"),
                    "address": place.get("formattedAddress"),
                    "rating": place.get("rating"),
                    "category": _place_category(place.get("types", [])),
                    "area": destination,
                    "source": "google_places",
                }
            )
        return {
            "available": True,
            "source": "google_places",
            "items": items,
            "retrieved_at": datetime.utcnow().isoformat() + "Z",
        }
    except RuntimeError as exc:
        return _unavailable("google_places", str(exc))


def fetch_flights(
    trip_request: dict[str, Any], destination_profile: dict[str, Any], settings: Settings
) -> dict[str, Any]:
    if not settings.flight_search_api_url:
        return {
            "available": True,
            "source": "built_in_flight_estimate",
            "live_check_available": False,
            "reason": "free-only mode uses built-in estimates; live flight prices are not enabled",
            "estimated_roundtrip_per_person": destination_profile.get("estimated_flight_total"),
        }
    headers = {}
    if settings.flight_search_api_key:
        headers["Authorization"] = f"Bearer {settings.flight_search_api_key}"
    try:
        data = post_json(
            settings.flight_search_api_url,
            payload={
                "departure_city": trip_request["departure_city"],
                "destination": trip_request["destination"],
                "start_date": trip_request["start_date"],
                "end_date": trip_request["end_date"],
                "travelers": trip_request["travelers"],
                "currency": trip_request["currency"],
            },
            headers=headers,
            timeout=settings.request_timeout_seconds,
        )
        return {
            "available": True,
            "source": "flight_search",
            "live_check_available": True,
            "estimated_roundtrip_per_person": _first_number(
                data, ["estimated_roundtrip_per_person", "price", "min_price"]
            ),
            "raw": data,
            "retrieved_at": datetime.utcnow().isoformat() + "Z",
        }
    except RuntimeError as exc:
        return _unavailable("flight_search", str(exc))


def fetch_hotels(
    trip_request: dict[str, Any], destination_profile: dict[str, Any], settings: Settings
) -> dict[str, Any]:
    if not settings.hotel_search_api_url:
        return {
            "available": True,
            "source": "built_in_hotel_estimate",
            "live_check_available": False,
            "reason": "free-only mode uses built-in estimates; live hotel prices are not enabled",
            "estimated_nightly_total": destination_profile.get("daily_lodging_total"),
        }
    headers = {}
    if settings.hotel_search_api_key:
        headers["Authorization"] = f"Bearer {settings.hotel_search_api_key}"
    try:
        data = post_json(
            settings.hotel_search_api_url,
            payload={
                "destination": trip_request["destination"],
                "check_in": trip_request["start_date"],
                "check_out": trip_request["end_date"],
                "travelers": trip_request["travelers"],
                "currency": trip_request["currency"],
            },
            headers=headers,
            timeout=settings.request_timeout_seconds,
        )
        return {
            "available": True,
            "source": "hotel_search",
            "live_check_available": True,
            "estimated_nightly_total": _first_number(
                data, ["estimated_nightly_total", "nightly_total", "min_nightly_price"]
            ),
            "raw": data,
            "retrieved_at": datetime.utcnow().isoformat() + "Z",
        }
    except RuntimeError as exc:
        return _unavailable("hotel_search", str(exc))


def ai_destination_research(
    trip_request: dict[str, Any],
    destination_profile: dict[str, Any],
    activity_pool: list[dict[str, Any]],
    weather: dict[str, Any],
    places: dict[str, Any],
    flights: dict[str, Any],
    hotels: dict[str, Any],
    settings: Settings,
) -> dict[str, Any]:
    if settings.openrouter_api_key:
        if not settings.openrouter_model:
            return _unavailable(
                "openrouter_free",
                "set OPENROUTER_FREE_MODEL to openrouter/free or a model id ending with :free",
            )
        if settings.openrouter_model != "openrouter/free" and not settings.openrouter_model.endswith(":free"):
            return _unavailable(
                "openrouter_free",
                "OPENROUTER_FREE_MODEL must be openrouter/free or end with :free to prevent paid model usage",
            )
        hosted = _hosted_ai_research(
            base_url=settings.openrouter_base_url,
            api_key=settings.openrouter_api_key,
            model=settings.openrouter_model,
            source="openrouter_free",
            trip_request=trip_request,
            destination_profile=destination_profile,
            activity_pool=activity_pool,
            weather=weather,
            places=places,
            flights=flights,
            hotels=hotels,
            timeout=settings.request_timeout_seconds,
        )
        if hosted.get("available"):
            return hosted
        fallback = _local_reasoning_response(
            trip_request, destination_profile, weather, places, flights, hotels
        )
        fallback["hosted_ai_error"] = hosted.get("reason")
        return fallback

    if not settings.openai_api_key:
        return _local_reasoning_response(
            trip_request, destination_profile, weather, places, flights, hotels
        )

    return _hosted_ai_research(
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        source="openai",
        trip_request=trip_request,
        destination_profile=destination_profile,
        activity_pool=activity_pool,
        weather=weather,
        places=places,
        flights=flights,
        hotels=hotels,
        timeout=settings.request_timeout_seconds,
    )


def _hosted_ai_research(
    *,
    base_url: str,
    api_key: str,
    model: str,
    source: str,
    trip_request: dict[str, Any],
    destination_profile: dict[str, Any],
    activity_pool: list[dict[str, Any]],
    weather: dict[str, Any],
    places: dict[str, Any],
    flights: dict[str, Any],
    hotels: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    prompt = {
        "trip_request": trip_request,
        "destination_profile": destination_profile,
        "activity_pool": activity_pool[:12],
        "weather": weather,
        "places": places,
        "flights": _strip_raw(flights),
        "hotels": _strip_raw(hotels),
    }
    try:
        data = post_json(
            f"{base_url}/chat/completions",
            payload={
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a pragmatic travel planning analyst. "
                            "Return concise JSON only with keys: reasoning, "
                            "destination_research, itinerary_advice, budget_advice, warnings."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(prompt, ensure_ascii=False),
                    },
                ],
                "temperature": 0.3,
                "max_tokens": 900,
                "response_format": {"type": "json_object"},
            },
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        return {
            "available": True,
            "source": source,
            "model": model,
            "result": parsed,
            "retrieved_at": datetime.utcnow().isoformat() + "Z",
        }
    except (KeyError, json.JSONDecodeError, RuntimeError) as exc:
        return _unavailable(source, str(exc))


def summarize_tool_availability(live_context: dict[str, Any]) -> dict[str, Any]:
    return {
        name: {
            "available": value.get("available", False),
            "source": value.get("source"),
            "live_check_available": value.get("live_check_available"),
            "live_forecast_available": value.get("live_forecast_available"),
            "reason": value.get("reason"),
        }
        for name, value in live_context.items()
    }


def _seasonal_weather(destination: str, seasonal_notes: list[str], reason: str) -> dict[str, Any]:
    notes = seasonal_notes or ["Check the forecast closer to departure."]
    return {
        "available": True,
        "source": "seasonal_weather_fallback",
        "live_forecast_available": False,
        "forecast_reason": reason,
        "summary": f"{destination}: {' '.join(notes)}",
    }


def _fetch_osm_places(
    destination: str,
    trip_request: dict[str, Any],
    coordinates: dict[str, float] | None,
    country: str | None,
    settings: Settings,
) -> dict[str, Any]:
    if not coordinates:
        return _unavailable("openstreetmap_nominatim", "coordinates are unavailable")
    lat = coordinates["latitude"]
    lon = coordinates["longitude"]
    viewbox = f"{lon - 0.18},{lat + 0.18},{lon + 0.18},{lat - 0.18}"
    country_part = f" {country}" if country else ""
    interests = trip_request.get("interests", [])
    queries = [
        f"{interest} {destination}{country_part}" for interest in interests[:4]
    ] or [f"things to do {destination}{country_part}"]
    items = []
    try:
        for query in queries:
            data = get_json(
                "https://nominatim.openstreetmap.org/search",
                params={
                    "q": query,
                    "format": "jsonv2",
                    "limit": 5,
                    "viewbox": viewbox,
                    "bounded": 1,
                },
                headers={"User-Agent": "travel-planner-agent/0.1"},
                timeout=settings.request_timeout_seconds,
            )
            for place in data if isinstance(data, list) else []:
                name = place.get("name") or place.get("display_name", "").split(",")[0]
                if not name:
                    continue
                items.append(
                    {
                        "name": name,
                        "address": place.get("display_name"),
                        "rating": None,
                        "category": _interest_category(query),
                        "area": destination,
                        "source": "openstreetmap_nominatim",
                    }
                )
        deduped = []
        seen = set()
        for item in items:
            key = item["name"].lower()
            if key not in seen:
                deduped.append(item)
                seen.add(key)
        return {
            "available": bool(deduped),
            "source": "openstreetmap_nominatim",
            "items": deduped[:8],
            "reason": None if deduped else "no places returned",
            "retrieved_at": datetime.utcnow().isoformat() + "Z",
        }
    except RuntimeError as exc:
        return _unavailable("openstreetmap_nominatim", str(exc))


def _rule_based_research(
    trip_request: dict[str, Any],
    destination_profile: dict[str, Any],
    weather: dict[str, Any],
    places: dict[str, Any],
    flights: dict[str, Any],
    hotels: dict[str, Any],
) -> dict[str, Any]:
    warnings = []
    if not flights.get("available"):
        warnings.append("Live flight checking is not configured; budget uses estimates.")
    if not hotels.get("available"):
        warnings.append("Live hotel checking is not configured; lodging uses estimates.")
    if weather.get("source") == "seasonal_weather_fallback":
        warnings.append("Live forecast is unavailable for these dates; seasonal notes are used.")

    return {
        "reasoning": [
            "Keep arrival and departure days light.",
            "Group activities by area to reduce transit time.",
            "Prefer high-priority interests before adding optional filler.",
        ],
        "destination_research": {
            "best_base": (destination_profile.get("best_areas_to_stay") or [{}])[0],
            "weather_summary": weather.get("summary"),
            "places_found": len(places.get("items", [])),
        },
        "itinerary_advice": [
            f"Use a {trip_request.get('pace', 'balanced')} pace and leave flexible meal time.",
            "Confirm opening hours and reservations before final booking.",
        ],
        "budget_advice": [
            "Treat flight and hotel prices as estimates until live providers are configured."
        ],
        "warnings": warnings,
    }


def _interest_category(query: str) -> str:
    normalized = query.lower()
    if any(word in normalized for word in ["food", "restaurant", "cafe", "market"]):
        return "food"
    if any(word in normalized for word in ["history", "museum", "castle", "heritage"]):
        return "history"
    if any(word in normalized for word in ["beach", "hike", "nature", "park"]):
        return "nature"
    if any(word in normalized for word in ["nightlife", "club", "bar", "music"]):
        return "nightlife"
    if any(word in normalized for word in ["shopping", "mall", "souvenir"]):
        return "shopping"
    return "interest"


def _local_reasoning_response(
    trip_request: dict[str, Any],
    destination_profile: dict[str, Any],
    weather: dict[str, Any],
    places: dict[str, Any],
    flights: dict[str, Any],
    hotels: dict[str, Any],
) -> dict[str, Any]:
    return {
        "available": True,
        "source": "rule_based_reasoning",
        "reason": "hosted free AI is not available, so local reasoning was used",
        "result": _rule_based_research(
            trip_request, destination_profile, weather, places, flights, hotels
        ),
    }


def _unavailable(source: str, reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "source": source,
        "reason": reason,
    }


def _place_category(types: list[str]) -> str:
    if "restaurant" in types or "food" in types:
        return "food"
    if "museum" in types or "tourist_attraction" in types:
        return "history"
    if "park" in types:
        return "nature"
    return "place"


def _first_number(data: dict[str, Any], keys: list[str]) -> float | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                pass
    return None


def _strip_raw(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "raw"}
