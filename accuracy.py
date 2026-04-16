from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path


SEASON_START = date(2025, 9, 4)
SOLVED_SCHEDULE_PATH = Path("solved_schedule.txt")
REFERENCE_SCHEDULE_PATH = Path("nfl_2025_regular_season_schedule_abbrev.csv")
TEAM_NORMALIZATION = {
    "WSH": "WAS",
}


@dataclass(frozen=True)
class ScheduledGame:
    away: str
    home: str
    week: int
    day: str
    time_et: str


def normalize_team(team: str) -> str:
    return TEAM_NORMALIZATION.get(team.strip(), team.strip())


def parse_reference_date(value: str) -> date:
    value = value.strip()
    if " or " in value:
        value = value.split(" or ", 1)[0].strip()
    return datetime.strptime(value, "%B %d, %Y").date()


def parse_time(value: str) -> str:
    value = value.strip().lower().replace(".", "")
    if value == "tbd":
        return "TBD"
    if value.endswith("a") or value.endswith("p"):
        value = value + "m"
    for fmt in ("%I:%M%p", "%I:%M %p", "%H:%M"):
        try:
            return datetime.strptime(value, fmt).strftime("%H:%M")
        except ValueError:
            continue
    raise ValueError(f"Unsupported time format: {value}")


def compute_week(game_date: date) -> int:
    return ((game_date - SEASON_START).days // 7) + 1


def read_reference_games(path: Path) -> list[ScheduledGame]:
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)

    games = []
    for row in rows:
        game_date = parse_reference_date(row["Date"])
        game = ScheduledGame(
            away=normalize_team(row["Away"]),
            home=normalize_team(row["Home"]),
            week=compute_week(game_date),
            day=row["Day"].strip(),
            time_et=parse_time(row["Time (ET)"]),
        )
        games.append(game)
    return games


def read_solved_games(path: Path) -> list[ScheduledGame]:
    content = path.read_text(encoding="utf-8")
    if not content.strip():
        raise ValueError(f"{path} is empty.")

    games = []
    current_week = None
    game_pattern = re.compile(
        r"^\s*(\d{4}-\d{2}-\d{2})\s+([A-Za-z]+)\s+(\d{2}:\d{2})(?:\s+INTL)?:\s+([A-Z]+)\s+at\s+([A-Z]+)\s*$"
    )

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("Week "):
            current_week = int(line.split()[1])
            continue

        match = game_pattern.match(raw_line)
        if not match:
            raise ValueError(f"Could not parse solved schedule line: {raw_line}")

        _game_date, day, time_et, away, home = match.groups()
        if current_week is None:
            raise ValueError("Encountered game line before any week header.")

        game = ScheduledGame(
            away=normalize_team(away),
            home=normalize_team(home),
            week=current_week,
            day=day,
            time_et=time_et,
        )
        games.append(game)

    return games


def build_matchup_map(games: list[ScheduledGame]) -> dict[tuple[str, str], ScheduledGame]:
    result = {}
    for game in games:
        result[(game.away, game.home)] = game
    return result


def compute_accuracy(
    solved: dict[tuple[str, str], ScheduledGame],
    reference: dict[tuple[str, str], ScheduledGame],
) -> tuple[int, int, int]:
    week_matches = 0
    exact_slot_matches = 0

    for matchup, ref_game in reference.items():
        solved_game = solved.get(matchup)
        if solved_game is None:
            continue
        if solved_game.week == ref_game.week:
            week_matches += 1
        if (
            solved_game.week == ref_game.week
            and solved_game.day == ref_game.day
            and solved_game.time_et == ref_game.time_et
        ):
            exact_slot_matches += 1

    return week_matches, exact_slot_matches, len(reference)


def compute_home_slot_accuracy(
    solved_games: list[ScheduledGame],
    reference_games: list[ScheduledGame],
) -> tuple[int, int]:
    solved_slots = Counter(
        (game.home, game.week, game.day, game.time_et) for game in solved_games
    )
    reference_slots = Counter(
        (game.home, game.week, game.day, game.time_et) for game in reference_games
    )
    matches = sum((solved_slots & reference_slots).values())
    return matches, len(reference_games)


def compute_home_week_accuracy(
    solved_games: list[ScheduledGame],
    reference_games: list[ScheduledGame],
) -> tuple[int, int]:
    solved_home_weeks = Counter((game.home, game.week) for game in solved_games)
    reference_home_weeks = Counter((game.home, game.week) for game in reference_games)
    matches = sum((solved_home_weeks & reference_home_weeks).values())
    return matches, len(reference_games)


def compute_bye_week_accuracy(
    solved_games: list[ScheduledGame],
    reference_games: list[ScheduledGame],
) -> tuple[int, int, dict[str, list[int]], dict[str, list[int]]]:
    solved_weeks = defaultdict(set)
    reference_weeks = defaultdict(set)
    teams = set()

    for game in solved_games:
        teams.update((game.away, game.home))
        solved_weeks[game.away].add(game.week)
        solved_weeks[game.home].add(game.week)
    for game in reference_games:
        teams.update((game.away, game.home))
        reference_weeks[game.away].add(game.week)
        reference_weeks[game.home].add(game.week)

    solved_byes = {}
    reference_byes = {}
    matches = 0
    for team in sorted(teams):
        solved_bye = sorted(set(range(1, 19)) - solved_weeks[team])
        reference_bye = sorted(set(range(1, 19)) - reference_weeks[team])
        solved_byes[team] = solved_bye
        reference_byes[team] = reference_bye
        if solved_bye == reference_bye:
            matches += 1

    return matches, len(teams), solved_byes, reference_byes


def main():
    reference_games = read_reference_games(REFERENCE_SCHEDULE_PATH)
    solved_games = read_solved_games(SOLVED_SCHEDULE_PATH)
    reference = build_matchup_map(reference_games)
    solved = build_matchup_map(solved_games)
    week_matches, exact_slot_matches, total_games = compute_accuracy(solved, reference)
    home_week_matches, total_home_week_games = compute_home_week_accuracy(
        solved_games, reference_games
    )
    home_slot_matches, total_slot_games = compute_home_slot_accuracy(
        solved_games, reference_games
    )
    bye_week_matches, total_teams, solved_byes, reference_byes = compute_bye_week_accuracy(
        solved_games, reference_games
    )

    print(f"Compared {total_games} games.")
    print(
        f"Correct week overall: {week_matches}/{total_games} "
        f"({week_matches / total_games:.2%})"
    )
    print(
        f"Correct week and time slot: {exact_slot_matches}/{total_games} "
        f"({exact_slot_matches / total_games:.2%})"
    )
    print(
        f"Correct home team in correct week: {home_week_matches}/{total_home_week_games} "
        f"({home_week_matches / total_home_week_games:.2%})"
    )
    print(
        f"Correct home team in exact slot: {home_slot_matches}/{total_slot_games} "
        f"({home_slot_matches / total_slot_games:.2%})"
    )
    print(
        f"Correct bye week by team: {bye_week_matches}/{total_teams} "
        f"({bye_week_matches / total_teams:.2%})"
    )

    mismatched_byes = [
        team
        for team in sorted(reference_byes)
        if solved_byes[team] != reference_byes[team]
    ]
    if mismatched_byes:
        print("Bye week mismatches:")
        for team in mismatched_byes:
            print(
                f"  {team}: solved {solved_byes[team]} vs reference {reference_byes[team]}"
            )


if __name__ == "__main__":
    main()
