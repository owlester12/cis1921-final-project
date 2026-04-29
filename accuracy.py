from __future__ import annotations

import argparse
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from nfl_sat import NFLSchedulerSAT


SEASON_START = date(2025, 9, 4)
SOLVED_SCHEDULE_PATH = Path("outputs/solved_schedule.txt")
REFERENCE_SCHEDULE_PATH = Path("csvs/nfl_2025_regular_season_schedule_abbrev.csv")
TEAM_NORMALIZATION = {"WSH": "WAS"}

#Dataclass to hold info for each scheduled game
@dataclass(frozen=True)
class ScheduledGame:
    away: str
    home: str
    week: int
    day: str
    time_et: str


class ScheduleAccuracyEvaluator:
    # Load both schedules and precompute all shared lookup structures.
    def __init__(
        self,
        solved_schedule_path: Path,
        reference_schedule_path: Path = REFERENCE_SCHEDULE_PATH,
    ):
        self.solved_games = self.read_solved_games(solved_schedule_path)
        self.reference_games = self.read_reference_games(reference_schedule_path)
        self.load_counters()

    # Normalize team abbreviations that differ between source files.
    @staticmethod
    def normalize_team(team: str) -> str:
        return TEAM_NORMALIZATION.get(team.strip(), team.strip())

    # Convert a calendar date into the NFL week number used by the solver.
    @staticmethod
    def compute_week(game_date: date) -> int:
        return ((game_date - SEASON_START).days // 7) + 1

    # Bucket kickoff times into broad TV windows.
    @staticmethod
    def time_window(time_et: str) -> str | None:
        if ":" not in time_et:
            return None
        hour = int(time_et.split(":", 1)[0])
        if hour == 13:
            return "lunch"
        if 16 <= hour <= 17:
            return "afternoon"
        if hour == 20:
            return "night"
        return None

    # Computes chance that if you randomly choose some number of slots without replacement, you get at least one item from a target group
    # P(at least one) = 1 - P(none) = 1 - (non-target slots choose draws) / (combinations of total slots choose draws)
    @staticmethod
    def probability_at_least_one(total: int, successful: int, draws: int) -> float:
        if successful <= 0:
            return 0.0
        if draws > total - successful:
            return 1.0
        return 1.0 - math.comb(total - successful, draws) / math.comb(total, draws)

    # Key games by ordered away/home matchup.
    @staticmethod
    def build_matchup_map(games: list[ScheduledGame]) -> dict[tuple[str, str], ScheduledGame]:
        return {(game.away, game.home): game for game in games}

    # Format a metric with its random baseline.
    @staticmethod
    def format_metric(matches: int, total: int, random_rate: float) -> str:
        return f"{matches}/{total} ({matches / total:.2%}; random ~{random_rate:.2%})"

    # Read the official NFL schedule CSV into ScheduledGame objects.
    @staticmethod
    def read_reference_games(path: Path) -> list[ScheduledGame]:
        games = []
        for row in NFLSchedulerSAT.read_simple_csv(path):
            date_text = row["Date"].split(" or ", 1)[0].strip()
            game_date = NFLSchedulerSAT.parse_csv_date(date_text)
            games.append(
                ScheduledGame(
                    away=ScheduleAccuracyEvaluator.normalize_team(row["Away"]),
                    home=ScheduleAccuracyEvaluator.normalize_team(row["Home"]),
                    week=ScheduleAccuracyEvaluator.compute_week(game_date),
                    day=row["Day"].strip(),
                    time_et=NFLSchedulerSAT.parse_csv_time(row["Time (ET)"]),
                )
            )
        return games

    # Parse a generated text schedule such as outputs/solved_schedule.txt.
    @staticmethod
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
            if current_week is None:
                raise ValueError("Encountered game line before any week header.")

            _game_date, day, time_et, away, home = match.groups()
            games.append(
                ScheduledGame(
                    away=ScheduleAccuracyEvaluator.normalize_team(away),
                    home=ScheduleAccuracyEvaluator.normalize_team(home),
                    week=current_week,
                    day=day,
                    time_et=time_et,
                )
            )
        return games

    # Precompute all repeated lookup maps and counters.
    def load_counters(self):
        self.total_slots = len(self.solved_games)
        self.reference_matchups = self.build_matchup_map(self.reference_games)
        self.solved_matchups = self.build_matchup_map(self.solved_games)

        self.solved_week_counts = Counter(game.week for game in self.solved_games)
        self.solved_exact_slot_counts = Counter(
            (game.week, game.day, game.time_et) for game in self.solved_games
        )
        self.solved_timeslot_counts = Counter(
            (game.day, game.time_et) for game in self.solved_games
        )
        self.solved_window_counts = Counter(
            window
            for game in self.solved_games
            if (window := self.time_window(game.time_et)) is not None
        )

        self.solved_home_weeks = Counter(
            (game.home, game.week) for game in self.solved_games
        )
        self.reference_home_weeks = Counter(
            (game.home, game.week) for game in self.reference_games
        )
        self.solved_home_slots = Counter(
            (game.home, game.week, game.day, game.time_et)
            for game in self.solved_games
        )
        self.reference_home_slots = Counter(
            (game.home, game.week, game.day, game.time_et)
            for game in self.reference_games
        )
        self.reference_home_game_counts = Counter(
            game.home for game in self.reference_games
        )

        self.solved_weeks_by_team = self.build_team_week_sets(self.solved_games)
        self.reference_weeks_by_team = self.build_team_week_sets(self.reference_games)
        self.solved_team_sequences = self.build_team_week_sequences(self.solved_games)
        self.reference_team_sequences = self.build_team_week_sequences(self.reference_games)

    # Build the set of weeks played by each team.
    @staticmethod
    def build_team_week_sets(games: list[ScheduledGame]) -> dict[str, set[int]]:
        weeks_by_team = defaultdict(set)
        for game in games:
            weeks_by_team[game.away].add(game.week)
            weeks_by_team[game.home].add(game.week)
        return weeks_by_team

    # Build each team's 18-week sequence, including BYE as an event.
    @staticmethod
    def build_team_week_sequences(
        games: list[ScheduledGame],
    ) -> dict[str, list[tuple[str, str] | tuple[str]]]:
        teams = sorted({team for game in games for team in (game.away, game.home)})
        sequences = {team: [("BYE",) for _week in range(18)] for team in teams}

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

    # Compare whether the solved matchup lands in same week as the reference/actual 
    def matchup_week_accuracy(self) -> tuple[int, int, float]:
        matches = 0
        for matchup, reference_game in self.reference_matchups.items():
            solved_game = self.solved_matchups.get(matchup)
            if solved_game and solved_game.week == reference_game.week:
                matches += 1

        #18 weeks in a season, so approx 1/18 chance of getting it right (ignoring some weeks have slightly different number of games)
        random_rate = 1/18
        return matches, len(self.reference_matchups), random_rate

    # Compare whether each matchup lands in the same week/day/time.
    def exact_slot_accuracy(self) -> tuple[int, int, float]:
        matches = 0
        for matchup, reference_game in self.reference_matchups.items():
            solved_game = self.solved_matchups.get(matchup)
            if (
                solved_game
                and solved_game.week == reference_game.week
                and solved_game.day == reference_game.day
                and solved_game.time_et == reference_game.time_et
            ):
                matches += 1

        # Shared kickoff windows mean a random game can match any slot with the
        # same week/day/time, so the chance is count(matching slots) / total slots.
        random_rate = sum(
            self.solved_exact_slot_counts[(game.week, game.day, game.time_et)]
            / self.total_slots
            for game in self.reference_matchups.values()
        ) / len(self.reference_matchups)
        return matches, len(self.reference_matchups), random_rate

    # Compare whether each matchup lands in the same day/time, ignoring week.
    def timeslot_accuracy(self) -> tuple[int, int, float]:
        matches = 0
        for matchup, reference_game in self.reference_matchups.items():
            solved_game = self.solved_matchups.get(matchup)
            if (
                solved_game
                and solved_game.day == reference_game.day
                and solved_game.time_et == reference_game.time_et
            ):
                matches += 1
        
        #P(randomly got correct slot) = (sum of num_slots_in_week/total_slots)
        random_rate = sum(
            self.solved_timeslot_counts[(game.day, game.time_et)] / self.total_slots
            for game in self.reference_matchups.values()
        ) / len(self.reference_matchups)
        return matches, len(self.reference_matchups), random_rate

    # Compare whether each matchup lands in the same broad time window (ignoring day and week).
    def time_window_accuracy(self) -> tuple[int, int, float]:
        matches = 0
        total = 0
        for matchup, reference_game in self.reference_matchups.items():
            reference_window = self.time_window(reference_game.time_et)
            solved_game = self.solved_matchups.get(matchup)
            if reference_window is None or solved_game is None:
                continue
            total += 1
            if self.time_window(solved_game.time_et) == reference_window:
                matches += 1

        #P(randomly got correct slot) = (sum of num_slots_in_week/total_slots)
        random_rate = sum(
            self.solved_window_counts[self.time_window(game.time_et)] / self.total_slots
            for game in self.reference_matchups.values()
            if self.time_window(game.time_et) is not None
        ) / total
        return matches, total, random_rate

    # Compare whether each home team appears in the same week.
    def home_week_accuracy(self) -> tuple[int, int, float]:
        matches = sum((self.solved_home_weeks & self.reference_home_weeks).values())
        random_matches = 0.0
        # A home-week match only needs one of that team's home games to land in
        # the target week, so use P(at least one home game hits that week).
        for (home, week), reference_count in self.reference_home_weeks.items():
            probability = self.probability_at_least_one(
                self.total_slots,
                self.solved_week_counts[week],
                self.reference_home_game_counts[home],
            )
            random_matches += reference_count * probability
        return matches, len(self.reference_games), random_matches / len(self.reference_games)

    # Compare whether each home team appears in the same exact slot.
    def home_slot_accuracy(self) -> tuple[int, int, float]:
        matches = sum((self.solved_home_slots & self.reference_home_slots).values())
        random_matches = 0.0
        # Same idea as home-week, but the target bucket is the exact
        # week/day/time slot instead of the whole week.
        for (home, week, day, time_et), reference_count in self.reference_home_slots.items():
            probability = self.probability_at_least_one(
                self.total_slots,
                self.solved_exact_slot_counts[(week, day, time_et)],
                self.reference_home_game_counts[home],
            )
            random_matches += reference_count * probability
        return matches, len(self.reference_games), random_matches / len(self.reference_games)

    # Compare each team's inferred bye week.
    def bye_week_accuracy(self) -> tuple[int, int, float]:
        teams = sorted(set(self.solved_weeks_by_team) | set(self.reference_weeks_by_team))
        matches = 0
        for team in teams:
            solved_bye = sorted(set(range(1, 19)) - self.solved_weeks_by_team[team])
            reference_bye = sorted(set(range(1, 19)) - self.reference_weeks_by_team[team])
            if solved_bye == reference_bye:
                matches += 1

        # NFL byes are constrained to weeks 5-14 in this project.
        return matches, len(teams), 1 / 10

    # Compare adjacent team schedule pairs without requiring the same weeks.
    def consecutive_pair_accuracy(self) -> tuple[int, int, float]:
        teams = sorted(set(self.solved_team_sequences) | set(self.reference_team_sequences))
        matches = 0
        total = 0
        bye_sequence = [("BYE",) for _week in range(18)]

        for team in teams:
            solved_sequence = self.solved_team_sequences.get(team, bye_sequence)
            reference_sequence = self.reference_team_sequences.get(team, bye_sequence)
            solved_pairs = Counter(
                (solved_sequence[index], solved_sequence[index + 1])
                for index in range(17)
            )
            reference_pairs = Counter(
                (reference_sequence[index], reference_sequence[index + 1])
                for index in range(17)
            )
            matches += sum((solved_pairs & reference_pairs).values())
            total += sum(reference_pairs.values())

        # In a random ordering of 18 events, an ordered adjacent pair has probability 1/18.
        return matches, total, 1 / 18

    # Return all reportable metrics in display order.
    def metrics(self) -> list[tuple[str, int, int, float]]:
        return [
            ("Correct week overall", *self.matchup_week_accuracy()),
            ("Correct week and time slot", *self.exact_slot_accuracy()),
            ("Correct time slot regardless of week", *self.timeslot_accuracy()),
            ("Correct time window regardless of week/day", *self.time_window_accuracy()),
            ("Correct home team in correct week", *self.home_week_accuracy()),
            ("Correct home team in exact slot", *self.home_slot_accuracy()),
            ("Correct bye week by team", *self.bye_week_accuracy()),
            ("Correct consecutive team game pairs", *self.consecutive_pair_accuracy()),
        ]

    # Print the human-readable evaluation report.
    def print_report(self):
        print(f"Compared {len(self.reference_games)} games.")
        for label, matches, total, random_rate in self.metrics():
            print(f"{label}: {self.format_metric(matches, total, random_rate)}")


# Parse the optional generated schedule path.
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
    evaluator = ScheduleAccuracyEvaluator(args.schedule_path)
    evaluator.print_report()


if __name__ == "__main__":
    main()
