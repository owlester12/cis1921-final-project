from __future__ import annotations

import csv
import math
import re
import argparse
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


def compute_timeslot_accuracy(
    solved: dict[tuple[str, str], ScheduledGame],
    reference: dict[tuple[str, str], ScheduledGame],
) -> tuple[int, int]:
    matches = 0

    for matchup, ref_game in reference.items():
        solved_game = solved.get(matchup)
        if solved_game is None:
            continue
        if solved_game.day == ref_game.day and solved_game.time_et == ref_game.time_et:
            matches += 1

    return matches, len(reference)


def time_window(time_et: str) -> str | None:
    if ":" not in time_et:
        return None
    hour_text, _minute_text = time_et.split(":", 1)
    hour = int(hour_text)
    if hour == 13:
        return "lunch"
    if 16 <= hour <= 17:
        return "afternoon"
    if hour == 20:
        return "night"
    return None


def compute_time_window_accuracy(
    solved: dict[tuple[str, str], ScheduledGame],
    reference: dict[tuple[str, str], ScheduledGame],
) -> tuple[int, int]:
    matches = 0
    total = 0

    for matchup, ref_game in reference.items():
        ref_window = time_window(ref_game.time_et)
        if ref_window is None:
            continue

        solved_game = solved.get(matchup)
        if solved_game is None:
            continue

        total += 1
        if time_window(solved_game.time_et) == ref_window:
            matches += 1

    return matches, total


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


def build_team_week_sequence(
    games: list[ScheduledGame],
) -> dict[str, list[tuple[str, str] | tuple[str]]]:
    teams = sorted({team for game in games for team in (game.away, game.home)})
    sequences = {
        team: [("BYE",) for _week in range(18)]
        for team in teams
    }

    for game in games:
        week_index = game.week - 1
        away_event = ("A", game.home)
        home_event = ("H", game.away)

        if sequences[game.away][week_index] != ("BYE",):
            raise ValueError(f"{game.away} has multiple games in week {game.week}.")
        if sequences[game.home][week_index] != ("BYE",):
            raise ValueError(f"{game.home} has multiple games in week {game.week}.")

        sequences[game.away][week_index] = away_event
        sequences[game.home][week_index] = home_event

    return sequences


def compute_consecutive_pair_accuracy(
    solved_games: list[ScheduledGame],
    reference_games: list[ScheduledGame],
) -> tuple[int, int, dict[str, tuple[int, int]]]:
    solved_sequences = build_team_week_sequence(solved_games)
    reference_sequences = build_team_week_sequence(reference_games)
    teams = sorted(set(solved_sequences) | set(reference_sequences))

    matches = 0
    total = 0
    by_team = {}
    bye_sequence = [("BYE",) for _week in range(18)]

    for team in teams:
        solved_sequence = solved_sequences.get(team, bye_sequence)
        reference_sequence = reference_sequences.get(team, bye_sequence)

        solved_pairs = Counter(
            (solved_sequence[index], solved_sequence[index + 1])
            for index in range(17)
        )
        reference_pairs = Counter(
            (reference_sequence[index], reference_sequence[index + 1])
            for index in range(17)
        )

        team_matches = sum((solved_pairs & reference_pairs).values())
        team_total = sum(reference_pairs.values())
        matches += team_matches
        total += team_total
        by_team[team] = (team_matches, team_total)

    return matches, total, by_team


def probability_at_least_one(total: int, successful: int, draws: int) -> float:
    if successful <= 0:
        return 0.0
    if draws > total - successful:
        return 1.0
    return 1.0 - math.comb(total - successful, draws) / math.comb(total, draws)


def compute_random_expectations(
    reference_games: list[ScheduledGame],
    slot_source_games: list[ScheduledGame],
) -> dict[str, float]:
    total_slots = len(slot_source_games)
    week_counts = Counter(game.week for game in slot_source_games)
    exact_slot_counts = Counter(
        (game.week, game.day, game.time_et) for game in slot_source_games
    )
    timeslot_counts = Counter(
        (game.day, game.time_et) for game in slot_source_games
    )
    window_counts = Counter(
        window
        for game in slot_source_games
        if (window := time_window(game.time_et)) is not None
    )
    home_game_counts = Counter(game.home for game in reference_games)

    week_expected = sum(
        week_counts[game.week] / total_slots
        for game in reference_games
    ) / len(reference_games)
    exact_slot_expected = sum(
        exact_slot_counts[(game.week, game.day, game.time_et)] / total_slots
        for game in reference_games
    ) / len(reference_games)
    timeslot_expected = sum(
        timeslot_counts[(game.day, game.time_et)] / total_slots
        for game in reference_games
    ) / len(reference_games)

    window_reference_games = [
        game for game in reference_games if time_window(game.time_et) is not None
    ]
    time_window_expected = sum(
        window_counts[time_window(game.time_et)] / total_slots
        for game in window_reference_games
    ) / len(window_reference_games)

    reference_home_weeks = Counter((game.home, game.week) for game in reference_games)
    home_week_expected_matches = 0.0
    for (home, week), reference_count in reference_home_weeks.items():
        probability = probability_at_least_one(
            total_slots,
            week_counts[week],
            home_game_counts[home],
        )
        home_week_expected_matches += reference_count * probability

    reference_home_slots = Counter(
        (game.home, game.week, game.day, game.time_et)
        for game in reference_games
    )
    home_slot_expected_matches = 0.0
    for (home, week, day, time_et), reference_count in reference_home_slots.items():
        probability = probability_at_least_one(
            total_slots,
            exact_slot_counts[(week, day, time_et)],
            home_game_counts[home],
        )
        home_slot_expected_matches += reference_count * probability

    legal_bye_weeks = set(range(5, 15))
    team_weeks = defaultdict(set)
    for game in reference_games:
        team_weeks[game.away].add(game.week)
        team_weeks[game.home].add(game.week)
    reference_bye_weeks = [
        next(iter(set(range(1, 19)) - weeks_played))
        for weeks_played in team_weeks.values()
        if len(set(range(1, 19)) - weeks_played) == 1
    ]
    bye_week_expected = sum(
        1 / len(legal_bye_weeks)
        for bye_week in reference_bye_weeks
        if bye_week in legal_bye_weeks
    ) / len(reference_bye_weeks)

    return {
        "week": week_expected,
        "exact_slot": exact_slot_expected,
        "timeslot": timeslot_expected,
        "time_window": time_window_expected,
        "home_week": home_week_expected_matches / len(reference_games),
        "home_slot": home_slot_expected_matches / len(reference_games),
        "bye_week": bye_week_expected,
        "consecutive_pairs": 1 / 18,
    }


def format_metric(matches: int, total: int, random_rate: float) -> str:
    return f"{matches}/{total} ({matches / total:.2%}; random ~{random_rate:.2%})"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare a generated NFL schedule against the official schedule."
    )
    parser.add_argument(
        "schedule_path",
        nargs="?",
        type=Path,
        default=SOLVED_SCHEDULE_PATH,
        help=f"Generated schedule text file to evaluate. Defaults to {SOLVED_SCHEDULE_PATH}.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    reference_games = read_reference_games(REFERENCE_SCHEDULE_PATH)
    solved_games = read_solved_games(args.schedule_path)
    random_expectations = compute_random_expectations(reference_games, solved_games)
    reference = build_matchup_map(reference_games)
    solved = build_matchup_map(solved_games)
    week_matches, exact_slot_matches, total_games = compute_accuracy(solved, reference)
    timeslot_matches, total_timeslot_games = compute_timeslot_accuracy(solved, reference)
    time_window_matches, total_time_window_games = compute_time_window_accuracy(
        solved, reference
    )
    home_week_matches, total_home_week_games = compute_home_week_accuracy(
        solved_games, reference_games
    )
    home_slot_matches, total_slot_games = compute_home_slot_accuracy(
        solved_games, reference_games
    )
    bye_week_matches, total_teams, _solved_byes, _reference_byes = compute_bye_week_accuracy(
        solved_games, reference_games
    )
    consecutive_pair_matches, total_consecutive_pairs, _consecutive_pairs_by_team = (
        compute_consecutive_pair_accuracy(solved_games, reference_games)
    )

    print(f"Compared {total_games} games.")
    print(
        "Correct week overall: "
        f"{format_metric(week_matches, total_games, random_expectations['week'])}"
    )
    print(
        "Correct week and time slot: "
        f"{format_metric(exact_slot_matches, total_games, random_expectations['exact_slot'])}"
    )
    print(
        "Correct time slot regardless of week: "
        f"{format_metric(timeslot_matches, total_timeslot_games, random_expectations['timeslot'])}"
    )
    print(
        f"Correct time window regardless of week/day: "
        f"{format_metric(time_window_matches, total_time_window_games, random_expectations['time_window'])}"
    )
    print(
        "Correct home team in correct week: "
        f"{format_metric(home_week_matches, total_home_week_games, random_expectations['home_week'])}"
    )
    print(
        "Correct home team in exact slot: "
        f"{format_metric(home_slot_matches, total_slot_games, random_expectations['home_slot'])}"
    )
    print(
        "Correct bye week by team: "
        f"{format_metric(bye_week_matches, total_teams, random_expectations['bye_week'])}"
    )
    print(
        f"Correct consecutive team game pairs: "
        f"{format_metric(consecutive_pair_matches, total_consecutive_pairs, random_expectations['consecutive_pairs'])}"
    )


if __name__ == "__main__":
    main()
