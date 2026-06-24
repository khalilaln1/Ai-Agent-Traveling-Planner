"""Small HTTP API for the travel planner.

Uses only the Python standard library so the backend can run before choosing a
web framework. Endpoints:

- GET /health
- GET /docs
- GET /openapi.json
- POST /plan-trip
- POST /export/pdf
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from .pdf_export import export_plan_pdf
from .workflow import plan_trip


class TravelPlannerHandler(BaseHTTPRequestHandler):
    server_version = "TravelPlannerAgent/0.1"

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._redirect("/docs")
            return
        if path == "/health":
            self._json(200, {"status": "ok"})
            return
        if path == "/openapi.json":
            self._openapi()
            return
        if path == "/docs":
            self._html(200, swagger_html())
            return
        plan_route = parse_plan_route(path)
        if plan_route and plan_route["kind"] == "json":
            self._handle_get_plan(plan_route["plan_id"])
            return
        if plan_route and plan_route["kind"] == "pdf":
            self._handle_get_plan_pdf(plan_route["plan_id"])
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/plan-trip":
            self._handle_plan_trip()
            return
        if path == "/export/pdf":
            self._handle_export_pdf()
            return
        self._json(404, {"error": "not_found"})

    def _handle_plan_trip(self) -> None:
        payload = self._read_json()
        if payload is None:
            return
        result = plan_trip(payload)
        if result.get("status") != "complete":
            self._json(200, result)
            return

        plan_id = create_plan_id()
        result["plan_id"] = plan_id
        plan_path = save_plan(plan_id, result)
        pdf_path = plan_pdf_path(plan_id)
        export_plan_pdf(result, pdf_path)
        self._json(
            200,
            compact_plan_response(
                result,
                plan_path=plan_path,
                pdf_path=pdf_path,
                include_full=bool(payload.get("include_full", False)),
            ),
        )

    def _handle_export_pdf(self) -> None:
        payload = self._read_json()
        if payload is None:
            return
        if "plan_id" in payload:
            plan = load_plan(str(payload["plan_id"]))
            if plan is None:
                self._json(404, {"error": "plan_not_found", "plan_id": payload["plan_id"]})
                return
        else:
            plan = payload.get("plan") if "plan" in payload else plan_trip(payload)
        output = payload.get("output_path") or plan_pdf_path(plan.get("plan_id", create_plan_id()))
        path = export_plan_pdf(plan, output)
        self._json(
            200,
            {
                "status": "complete",
                "plan_id": plan.get("plan_id"),
                "pdf_path": str(path),
                "pdf_url": f"/plans/{plan.get('plan_id')}/pdf" if plan.get("plan_id") else None,
            },
        )

    def _handle_get_plan(self, plan_id: str) -> None:
        plan = load_plan(plan_id)
        if plan is None:
            self._json(404, {"error": "plan_not_found", "plan_id": plan_id})
            return
        self._json(200, plan)

    def _handle_get_plan_pdf(self, plan_id: str) -> None:
        path = plan_pdf_path(plan_id)
        if not path.exists():
            plan = load_plan(plan_id)
            if plan is None:
                self._json(404, {"error": "plan_not_found", "plan_id": plan_id})
                return
            export_plan_pdf(plan, path)
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Disposition", f'attachment; filename="{plan_id}.pdf"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8")
            data = json.loads(raw) if raw else {}
            if not isinstance(data, dict):
                raise ValueError("payload must be an object")
            return data
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": "invalid_json", "detail": str(exc)})
            return None

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, status: int, body: str) -> None:
        rendered = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(rendered)))
        self.end_headers()
        self.wfile.write(rendered)

    def _openapi(self) -> None:
        path = Path("docs/openapi.json")
        if not path.exists():
            self._json(404, {"error": "openapi_spec_not_found"})
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        return


def run(host: str = "127.0.0.1", port: int = 8001) -> None:
    server = ThreadingHTTPServer((host, port), TravelPlannerHandler)
    print(f"Travel planner API running at http://{host}:{port}")
    server.serve_forever()


def create_plan_id() -> str:
    return f"plan_{uuid4().hex[:12]}"


def plan_dir() -> Path:
    path = Path("outputs/plans")
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_plan(plan_id: str, plan: dict[str, Any]) -> Path:
    path = plan_dir() / f"{plan_id}.json"
    path.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def load_plan(plan_id: str) -> dict[str, Any] | None:
    path = plan_dir() / f"{plan_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def plan_pdf_path(plan_id: str) -> Path:
    return plan_dir() / f"{plan_id}.pdf"


def compact_plan_response(
    plan: dict[str, Any],
    *,
    plan_path: Path,
    pdf_path: Path,
    include_full: bool,
) -> dict[str, Any]:
    plan_id = plan["plan_id"]
    response = {
        "status": "complete",
        "plan_id": plan_id,
        "summary": plan["trip_summary"],
        "ai_response": plan["ai_response"],
        "budget": plan["budget"],
        "validation": plan["validation"],
        "report": {
            "json_path": str(plan_path),
            "pdf_path": str(pdf_path),
            "json_url": f"/plans/{plan_id}",
            "pdf_url": f"/plans/{plan_id}/pdf",
        },
        "tool_availability": plan.get("tool_availability", {}),
    }
    if include_full:
        response["full_plan"] = plan
    return response


def parse_plan_route(path: str) -> dict[str, str] | None:
    parts = [part for part in path.split("/") if part]
    if len(parts) == 2 and parts[0] == "plans":
        return {"plan_id": parts[1], "kind": "json"}
    if len(parts) == 3 and parts[0] == "plans" and parts[2] == "pdf":
        return {"plan_id": parts[1], "kind": "pdf"}
    return None


def swagger_html() -> str:
    return """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Travel Planner Agent API</title>
    <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css">
    <style>
      body { margin: 0; background: #f7f7f7; }
      .swagger-ui .topbar { display: none; }
    </style>
  </head>
  <body>
    <div id="swagger-ui"></div>
    <script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
    <script>
      window.onload = () => {
        window.ui = SwaggerUIBundle({
          url: "/openapi.json",
          dom_id: "#swagger-ui",
          deepLinking: true,
          presets: [SwaggerUIBundle.presets.apis],
          layout: "BaseLayout"
        });
      };
    </script>
  </body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the travel planner API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    Path("outputs").mkdir(exist_ok=True)
    run(args.host, args.port)


if __name__ == "__main__":
    main()
