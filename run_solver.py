#!/usr/bin/env python
"""Run the CP-SAT scheduler with viewership optimization and shorter timeout."""

from pathlib import Path
from nfl_cp_sat import NFLSchedulerCPSAT

output_path = Path("solved_schedule_cp_viewership.txt")
print("Starting CP-SAT solver with TV viewership optimization...")

scheduler = NFLSchedulerCPSAT(
    "nfl_2025_2026_regular_season_team_abbreviations.csv",
    "nfl_2025_2026_regular_season_et_international_only.csv",
)
scheduler.build_model()

print("Model built successfully. Starting solver with 5-minute timeout...")
schedule = scheduler.solve(max_time_seconds=300.0, num_workers=8)

if schedule is None:
    print("UNSAT_OR_TIMEOUT")
else:
    rendered = scheduler.format_schedule(schedule)
    output_path.write_text(rendered, encoding="utf-8")
    print(f"Solution found! Wrote {output_path}")
    print("\nSchedule preview (first 30 lines):")
    print("\n".join(rendered.split("\n")[:30]))
