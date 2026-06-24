"""Command-line runner for the travel planning workflow."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .pdf_export import export_plan_pdf
from .workflow import plan_trip


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Run the travel planner workflow.")
    parser.add_argument(
        "input",
        nargs="?",
        default="examples/lisbon_trip.json",
        help="Path to a JSON trip request.",
    )
    parser.add_argument(
        "--output",
        help="Optional path where the final JSON plan should be written.",
    )
    parser.add_argument(
        "--pdf",
        help="Optional path where a PDF version of the final plan should be written.",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    with input_path.open("r", encoding="utf-8") as handle:
        request = json.load(handle)

    result = plan_trip(request)
    rendered = json.dumps(result, indent=2, ensure_ascii=False)
    print(rendered)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")

    if args.pdf:
        export_plan_pdf(result, args.pdf)


if __name__ == "__main__":
    main()
