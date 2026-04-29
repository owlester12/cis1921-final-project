#!/usr/bin/env python
"""Analyze the optimized schedule to show prime-time quality."""

from pathlib import Path
from nfl_cp_sat import TEAM_MARKET_VALUES, KEY_RIVALRIES

schedule_file = Path("outputs/solved_schedule_cp_viewership.txt")

if not schedule_file.exists():
    print("Schedule not found. Run run_solver.py first.")
    exit(1)

# Parse the schedule
schedule_text = schedule_file.read_text()
lines = schedule_text.strip().split("\n")

prime_time_games = []

current_week = 0
for line in lines:
    line = line.strip()
    if line.startswith("Week "):
        current_week = int(line.split()[-1])
    elif line.startswith("2025-") or line.startswith("2026-"):
        # Parse game line
        parts = line.split(": ")
        if len(parts) == 2:
            time_info = parts[0]   #"2025-09-04 Thursday 20:20"
            matchup = parts[1]     #"LAR at PHI"
            
            # Extract components
            time_parts = time_info.split()
            day = time_parts[1]
            time = time_parts[2]
            
            # Check if it's prime time
            is_prime = (day == "Thursday" and time in ("20:20", "20:15")) or \
                       (day == "Sunday" and time == "20:20") or \
                       (day == "Sunday" and time in ("20:15", "20:30", "20:25")) or \
                       (day == "Monday" and time in ("20:15", "20:30", "19:00", "19:15", "22:00"))
            
            if is_prime:
                away, home = matchup.split(" at ")
                prime_time_games.append({
                    "week": current_week,
                    "day": day,
                    "time": time,
                    "away": away,
                    "home": home,
                    "matchup": matchup,
                    "time_info": time_info,
                    "away_market": TEAM_MARKET_VALUES.get(away, 0),
                    "home_market": TEAM_MARKET_VALUES.get(home, 0),
                    "avg_market": (TEAM_MARKET_VALUES.get(away, 0) + TEAM_MARKET_VALUES.get(home, 0)) // 2,
                    "is_rivalry": frozenset([away, home]) in KEY_RIVALRIES,
                })

# Sort by quality
prime_time_games.sort(key=lambda x: (x["avg_market"], x["is_rivalry"]), reverse=True)

print("=" * 100)
print("TOP 30 PRIME-TIME MATCHUPS BY VIEWERSHIP QUALITY")
print("=" * 100)
print()

for i, game in enumerate(prime_time_games[:30], 1):
    rivalry_marker = "⭐ RIVALRY" if game["is_rivalry"] else ""
    print(f"{i:2d}. Wk{game['week']:2d} {game['day']:9s} {game['time']} | "
          f"{game['away']:3s}({game['away_market']:3d}) @ {game['home']:3s}({game['home_market']:3d}) "
          f"Avg:{game['avg_market']:3d} {rivalry_marker}")

print()
print("=" * 100)
print("PRIME-TIME SCHEDULE SUMMARY")
print("=" * 100)
print(f"Total prime-time games: {len(prime_time_games)}")
print(f"Games in rivalries: {sum(1 for g in prime_time_games if g['is_rivalry'])}")
print(f"Average market value in prime time: {sum(g['avg_market'] for g in prime_time_games) // len(prime_time_games):.0f}")
print()

# Count by day
from collections import Counter
day_counts = Counter(g['day'] for g in prime_time_games)
print("Games by day:")
for day, count in sorted(day_counts.items()):
    print(f"  {day}: {count} games")
print()

# Top rivalries scheduled in prime time
rivalries_in_prime = [g for g in prime_time_games if g['is_rivalry']]
rivalry_teams = {}
for game in rivalries_in_prime:
    pair = frozenset([game['away'], game['home']])
    if pair not in rivalry_teams:
        rivalry_teams[pair] = 0
    rivalry_teams[pair] += 1

print("Most frequent rival matchups in prime time:")
for pair, count in sorted(rivalry_teams.items(), key=lambda x: x[1], reverse=True)[:10]:
    teams = sorted(list(pair))
    print(f"  {teams[0]} vs {teams[1]}: {count} appearance{'s' if count > 1 else ''}")
