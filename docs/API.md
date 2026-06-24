# Travel Planner Agent API

Base URL:

```text
http://127.0.0.1:8001
```

Swagger UI:

```text
http://127.0.0.1:8001/docs
```

## Main Flow

1. Call `POST /plan-trip`.
2. The API creates a `plan_id`.
3. The API stores the full JSON plan.
4. The API creates a PDF report.
5. The API returns a compact response with report links.

## Endpoints

```text
GET  /health
GET  /docs
GET  /openapi.json
POST /plan-trip
GET  /plans/{plan_id}
GET  /plans/{plan_id}/pdf
POST /export/pdf
```

## POST /plan-trip

Request:

```json
{
  "departure_city": "New York",
  "destination": "Lisbon",
  "start_date": "2026-09-10",
  "end_date": "2026-09-16",
  "travelers": 2,
  "budget": 2500,
  "currency": "USD",
  "interests": ["beaches", "photography", "local markets"],
  "trip_style": "budget",
  "pace": "balanced",
  "constraints": ["no car rental"]
}
```

Compact response:

```json
{
  "status": "complete",
  "plan_id": "plan_abc123def456",
  "summary": {},
  "ai_response": "Here is a budget plan...",
  "budget": {},
  "validation": {},
  "report": {
    "json_path": "outputs/plans/plan_abc123def456.json",
    "pdf_path": "outputs/plans/plan_abc123def456.pdf",
    "json_url": "/plans/plan_abc123def456",
    "pdf_url": "/plans/plan_abc123def456/pdf"
  },
  "tool_availability": {}
}
```

To force the old long response, add:

```json
{
  "include_full": true
}
```

## GET /plans/{plan_id}

Returns the full stored JSON plan.

Example:

```text
GET /plans/plan_abc123def456
```

## GET /plans/{plan_id}/pdf

Downloads the PDF report. In Swagger UI, open this endpoint, click **Try it out**,
paste the `plan_id`, and click **Execute**. The response is `application/pdf`
and the browser should offer the PDF as a download.

Example:

```text
GET /plans/plan_abc123def456/pdf
```

## POST /export/pdf

Export or regenerate a PDF from an existing `plan_id`.

Request:

```json
{
  "plan_id": "plan_abc123def456"
}
```

Response:

```json
{
  "status": "complete",
  "plan_id": "plan_abc123def456",
  "pdf_path": "outputs/plans/plan_abc123def456.pdf",
  "pdf_url": "/plans/plan_abc123def456/pdf"
}
```
