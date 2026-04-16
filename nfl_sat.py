from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from itertools import product
from pathlib import Path
import random

try:
    from pysat.solvers import Cadical195, Glucose4, Kissat404, Minisat22
except ModuleNotFoundError as exc:
    raise ModuleNotFoundError(
        "Missing SAT solver dependency. Install the 'python-sat' package in the "
        "same Python interpreter that runs this script, for example:\n"
        "python -m pip install python-sat"
    ) from exc
except ImportError as exc:
    raise ImportError(
        "Imported the wrong 'pysat' package. This project needs the SAT-solving "
        "package 'python-sat', which provides 'pysat.solvers'. If you see a "
        "'pysatData' message, that is a different astronomy package.\n"
        "Install the correct package with:\n"
        "python -m pip install python-sat"
    ) from exc


team_timezones = {
    "ARI": 1, "ATL": 3, "BAL": 3, "BUF": 3, "CAR": 3, "CHI": 2, "CIN": 3, "CLE": 3,
    "DAL": 2, "DEN": 1, "DET": 3, "GB": 2, "HOU": 2, "IND": 3, "JAX": 3, "KC": 2,
    "LAC": 0, "LAR": 0, "LV": 0, "MIA": 3, "MIN": 2, "NE": 3, "NO": 2, "NYG": 3,
    "NYJ": 3, "PHI": 3, "PIT": 3, "SEA": 0, "SF": 0, "TB": 3, "TEN": 2, "WAS": 3,
}

divisions = {
    "AFC East": ["BUF", "MIA", "NE", "NYJ"],
    "AFC North": ["BAL", "CIN", "CLE", "PIT"],
    "AFC South": ["HOU", "IND", "JAX", "TEN"],
    "AFC West": ["DEN", "KC", "LV", "LAC"],
    "NFC East": ["DAL", "NYG", "PHI", "WAS"],
    "NFC North": ["CHI", "DET", "GB", "MIN"],
    "NFC South": ["ATL", "CAR", "NO", "TB"],
    "NFC West": ["ARI", "LAR", "SF", "SEA"],
}

SAME_STADIUM_PAIRS = [("LAC", "LAR"), ("NYG", "NYJ")]
WESTERN_HOME_TEAMS = {team for team, tz in team_timezones.items() if tz <= 1}
DAY_ORDER = ["Thursday", "Friday", "Saturday", "Sunday", "Monday"]
TEAM_TO_DIVISION = {
    team: division
    for division, teams in divisions.items()
    for team in teams
}


@dataclass(frozen=True)
class Game:
    away: str
    home: str
    international: bool = False


@dataclass(frozen=True)
class Slot:
    week: int
    day: str
    game_date: date
    time_et: str
    international: bool
    capacity: int


class NFLSchedulerSAT:
    SEASON_START = date(2025, 9, 4)

    def __init__(
        self,
        matchups_csv_path: str,
        slots_csv_path: str,
        super_bowl_champion: str = "PHI",
    ):
        self.matchups_csv_path = Path(matchups_csv_path)
        self.slots_csv_path = Path(slots_csv_path)
        self.super_bowl_champion = super_bowl_champion

        self.games: list[Game] = []
        self.slots: list[Slot] = []
        self.teams = sorted(team_timezones)
        self.weeks = list(range(1, 19))

        self.var_counter = 1
        self.var_map: dict[tuple, int] = {}
        self.rev_var_map: dict[int, tuple] = {}
        self.clauses: list[list[int]] = []
        self.or_helper_cache: dict[tuple, int | None] = {}
        self.and_helper_cache: dict[tuple, int] = {}

    @staticmethod
    def read_simple_csv(path: Path) -> list[dict[str, str]]:
        with open(path, newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            return [
                {str(key).strip(): str(value).strip() for key, value in row.items()}
                for row in reader
            ]
    @staticmethod
    def parse_csv_date(value: str) -> date:
        value = value.strip()
        for fmt in ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y"):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        raise ValueError(f"Unsupported date format: {value}")

    @staticmethod
    def parse_csv_time(value: str) -> str:
        value = value.strip().lower().replace(".", "")
        if value.endswith("a") or value.endswith("p"):
            value = value + "m"
        for fmt in ("%I:%M%p", "%I:%M %p"):
            try:
                return datetime.strptime(value, fmt).strftime("%H:%M")
            except ValueError:
                continue
        raise ValueError(f"Unsupported time format: {value}")


    def aggregate_slots(self, rows: list[dict[str, str]]) -> list[Slot]:
        capacities: dict[tuple[int, str, date, str, bool], int] = {}
        for row in rows:
            game_date = self.parse_csv_date(row["Date"])
            week = ((game_date - self.SEASON_START).days // 7) + 1
            key = (
                week,
                row["Day"],
                game_date,
                self.parse_csv_time(row["Time (ET)"]),
                row["International"].strip().lower() == "yes",
            )
            capacities[key] = capacities.get(key, 0) + 1

        slots = [
            Slot(
                week=week,
                day=day,
                game_date=game_date,
                time_et=time_et,
                international=international,
                capacity=capacity,
            )
            for (week, day, game_date, time_et, international), capacity in capacities.items()
        ]
        return sorted(
            slots,
            key=lambda slot: (slot.week, slot.game_date, DAY_ORDER.index(slot.day), slot.time_et),
        )

    def load_data(self):
        game_rows = self.read_simple_csv(self.matchups_csv_path)
        slot_rows = self.read_simple_csv(self.slots_csv_path)

        self.games = [
            Game(
                away=row["Away"],
                home=row["Home"],
                international=False,
            )
            for row in game_rows
        ]
        self.slots = self.aggregate_slots(slot_rows)

        if len(self.games) != sum(slot.capacity for slot in self.slots):
            raise ValueError("The matchup count must match the total CSV slot capacity.")

    def new_var(self, key: tuple) -> int:
        if key not in self.var_map:
            value = self.var_counter
            self.var_counter += 1
            self.var_map[key] = value
            self.rev_var_map[value] = key
        return self.var_map[key]

    def assignment_var(self, game_idx: int, slot_idx: int) -> int:
        return self.new_var(("assign", game_idx, slot_idx))

    def add_clause(self, literals: list[int]):
        self.clauses.append(list(literals))

    #At least one of the vars in vars_ must be true
    def add_at_least_one(self, vars_: list[int]):
        self.add_clause(vars_)

    #At most one of the vars in vars_ can be true
    def add_at_most_one(self, vars_: list[int]):
        self.add_at_most_k(vars_, 1)

    def add_exactly_one(self, vars_: list[int]):
        self.add_at_least_one(vars_)
        self.add_at_most_one(vars_)

    def add_at_most_k(self, vars_: list[int], k: int):
        if k >= len(vars_):
            return
        if k == 0:
            for var in vars_:
                self.add_clause([-var])
            return

        #Creates counter variables that track how many of the vars_ have been set to true so far. 
        #counters[i][j] is true if at least j of the first i vars are true.
        n = len(vars_)
        counters = [
            [self.new_var(("seq", tuple(vars_), i, j)) for j in range(k)]
            for i in range(n - 1)
        ]

        #If vars_[0] is true then counters[0][0] must be true
        self.add_clause([-vars_[0], counters[0][0]])
        #Forces counters[0][j] to be false for j > 0 because can't have more than 1 true variable
        for j in range(1, k):
            self.add_clause([-counters[0][j]])

        for i in range(1, n - 1):
            #If vars_[i] is true then counters[i][0] must be true because at least 1 variable is true in the first i+1 variables.
            self.add_clause([-vars_[i], counters[i][0]])
            #If previous prefix had at least j true variables then current prefix also has at least j true variables.
            self.add_clause([-counters[i - 1][0], counters[i][0]])
            for j in range(1, k):
                #If vars_[i] is true and previous prefix had at least j-1 true variables then current prefix has at least j true variables.
                self.add_clause([-vars_[i], -counters[i - 1][j - 1], counters[i][j]])
                #If previous prefix had at least j true variables then current prefix also has at least j true variables.
                self.add_clause([-counters[i - 1][j], counters[i][j]])
            #If vars_[i] is true and previous prefix had at least k-1 true variables then current prefix has at least k true variables which
            # violates the at most k constraint, so add clause to prevent this.
            self.add_clause([-vars_[i], -counters[i - 1][k - 1]])

        #If the final prefix has at least k true variables then we violate the at most k constraint, so add clause to prevent this.
        self.add_clause([-vars_[-1], -counters[-1][k - 1]])

    def create_variables(self):
        for game_idx in range(len(self.games)):
            for slot_idx in range(len(self.slots)):
                self.assignment_var(game_idx, slot_idx)

    # Returns the list of variables where the team is playing in the given week
    def team_week_vars(self, team: str, week: int) -> list[int]:
        vars_ = []
        for game_idx, game in enumerate(self.games):
            if game.home != team and game.away != team:
                continue
            for slot_idx, slot in enumerate(self.slots):
                if slot.week == week:
                    vars_.append(self.assignment_var(game_idx, slot_idx))
        return vars_

    # Returns the list of variables where the team is playing on the diven day in the given week
    def team_week_day_vars(self, team: str, week: int, day: str) -> list[int]:
        vars_ = []
        for game_idx, game in enumerate(self.games):
            if game.home != team and game.away != team:
                continue
            for slot_idx, slot in enumerate(self.slots):
                if slot.week == week and slot.day == day:
                    vars_.append(self.assignment_var(game_idx, slot_idx))
        return vars_

    # Returns the list of variables where the team is playing in a given time zone in the given week
    def team_week_timezone_vars(self, team: str, week: int, venue_tz: int) -> list[int]:
        vars_ = []
        for game_idx, game in enumerate(self.games):
            if game.home != team and game.away != team:
                continue
            if team_timezones[game.home] != venue_tz:
                continue
            for slot_idx, slot in enumerate(self.slots):
                if slot.week == week:
                    vars_.append(self.assignment_var(game_idx, slot_idx))
        return vars_

    # Returns the list of variables where the team is playing a road game in the given week
    def team_week_road_vars(self, team: str, week: int) -> list[int]:
        vars_ = []
        for game_idx, game in enumerate(self.games):
            if game.away != team:
                continue
            for slot_idx, slot in enumerate(self.slots):
                if slot.week == week:
                    vars_.append(self.assignment_var(game_idx, slot_idx))
        return vars_

    #Creates helper for or variables
    def derived_or_var(self, key: tuple, source_vars: list[int]) -> int | None:
        if key in self.or_helper_cache:
            return self.or_helper_cache[key]
        if not source_vars:
            self.or_helper_cache[key] = None
            return None
        helper = self.new_var(("or", key))
        for var in source_vars:
            self.add_clause([-var, helper])
        self.add_clause([-helper] + source_vars)
        self.or_helper_cache[key] = helper
        return helper

    #Creates helper for and variables
    def derived_and_var(self, key: tuple, lhs: int, rhs: int) -> int:
        if key in self.and_helper_cache:
            return self.and_helper_cache[key]
        helper = self.new_var(("and", key))
        self.add_clause([-helper, lhs])
        self.add_clause([-helper, rhs])
        self.add_clause([-lhs, -rhs, helper])
        self.and_helper_cache[key] = helper
        return helper

    def day_week_helper(self, team: str, week: int, day: str) -> int | None:
        return self.derived_or_var(
            ("day_week", team, week, day),
            self.team_week_day_vars(team, week, day),
        )

    def require_one_of(self, vars_: list[int], description: str):
        if not vars_:
            raise ValueError(f"No legal assignments exist for: {description}")
        self.add_at_least_one(vars_)

    def is_division_game(self, game: Game) -> bool:
        return TEAM_TO_DIVISION[game.home] == TEAM_TO_DIVISION[game.away]

    # Each game must be assigned to exactly one slot.
    def add_each_game_exactly_once(self):
        for game_idx in range(len(self.games)):
            vars_ = [self.assignment_var(game_idx, slot_idx) for slot_idx in range(len(self.slots))]
            self.add_exactly_one(vars_)

    # Each slot can only have up to its capacity of games assigned to it.
    def add_slot_capacity_constraints(self):
        for slot_idx, slot in enumerate(self.slots):
            vars_ = [self.assignment_var(game_idx, slot_idx) for game_idx in range(len(self.games))]
            self.add_at_most_k(vars_, slot.capacity)

    # Each team can only play at most one game in each week.
    def add_team_at_most_one_game_per_week(self):
        for team in self.teams:
            for week in self.weeks:
                self.add_at_most_one(self.team_week_vars(team, week))

    # The Super Bowl champion should have a home game in the opening slot on Thursday of week 1.
    def add_super_bowl_champion_home_opener_constraint(self):
        opener_vars = []
        for game_idx, game in enumerate(self.games):
            if game.home != self.super_bowl_champion:
                continue
            for slot_idx, slot in enumerate(self.slots):
                if slot.week == 1 and slot.day == "Thursday":
                    opener_vars.append(self.assignment_var(game_idx, slot_idx))
        self.require_one_of(opener_vars, "super bowl champion home opener")

    def add_no_four_straight_road_games_constraint(self):
        for team in self.teams:
            road_week_vars = {
                week: self.derived_or_var(
                    ("road_week", team, week),
                    self.team_week_road_vars(team, week),
                )
                for week in self.weeks
            }
            for start in range(1, 16):
                window = [road_week_vars[week] for week in range(start, start + 4)]
                if all(window):
                    self.add_clause([-var for var in window])

    def add_no_cross_country_ping_pong_constraint(self):
        for team in self.teams:
            tz_helpers = {
                (week, tz): self.derived_or_var(
                    ("tz_week", team, week, tz),
                    self.team_week_timezone_vars(team, week, tz),
                )
                for week in self.weeks
                for tz in range(4)
            }
            for week in range(1, 17):
                for tz1, tz2, tz3 in product(range(4), repeat=3):
                    jump_one = tz2 - tz1
                    jump_two = tz3 - tz2
                    if abs(jump_one) < 2 or abs(jump_two) < 2:
                        continue
                    if jump_one * jump_two >= 0:
                        continue
                    if abs(jump_one) + abs(jump_two) < 5:
                        continue
                    helper_vars = [
                        tz_helpers[(week, tz1)],
                        tz_helpers[(week + 1, tz2)],
                        tz_helpers[(week + 2, tz3)],
                    ]
                    if all(helper_vars):
                        self.add_clause([-helper for helper in helper_vars])

    def add_stadium_conflict_constraint(self):
        for team_a, team_b in SAME_STADIUM_PAIRS:
            home_games_a = [idx for idx, game in enumerate(self.games) if game.home == team_a]
            home_games_b = [idx for idx, game in enumerate(self.games) if game.home == team_b]
            for slot_a_idx, slot_a in enumerate(self.slots):
                for slot_b_idx, slot_b in enumerate(self.slots):
                    if slot_a.game_date != slot_b.game_date:
                        continue
                    for game_a in home_games_a:
                        for game_b in home_games_b:
                            self.add_clause([
                                -self.assignment_var(game_a, slot_a_idx),
                                -self.assignment_var(game_b, slot_b_idx),
                            ])

    def add_no_three_games_in_eleven_days_constraint(self):
        for team in self.teams:
            for week in range(1, 17):
                monday_current = self.day_week_helper(team, week, "Monday")
                sunday_next = self.day_week_helper(team, week + 1, "Sunday")
                monday_next = self.day_week_helper(team, week + 1, "Monday")
                thursday_after = self.day_week_helper(team, week + 2, "Thursday")
                if monday_current and sunday_next and thursday_after:
                    self.add_clause([-monday_current, -sunday_next, -thursday_after])
                if monday_current and monday_next and thursday_after:
                    self.add_clause([-monday_current, -monday_next, -thursday_after])

    def add_short_week_limit_constraint(self):
        for team in self.teams:
            short_week_vars = []
            for week in range(2, 19):
                thursday_current = self.day_week_helper(team, week, "Thursday")
                previous_candidates = []
                for day in ("Sunday", "Monday", "Saturday"):
                    helper = self.day_week_helper(team, week - 1, day)
                    if helper:
                        previous_candidates.append(helper)
                if not thursday_current or not previous_candidates:
                    continue

                pair_vars = [
                    self.derived_and_var(("short_week", team, week, index), previous, thursday_current)
                    for index, previous in enumerate(previous_candidates)
                ]
                short_var = self.derived_or_var(("short_week_any", team, week), pair_vars)
                if short_var:
                    short_week_vars.append(short_var)

            self.add_at_most_k(short_week_vars, 2)

    def add_west_coast_and_mountain_home_window_constraint(self):
        for game_idx, game in enumerate(self.games):
            if game.home not in WESTERN_HOME_TEAMS:
                continue
            for slot_idx, slot in enumerate(self.slots):
                if not slot.international and slot.time_et == "13:00":
                    self.add_clause([-self.assignment_var(game_idx, slot_idx)])

    def validate_division_home_away_pairs(self):
        pair_counts: dict[tuple[str, str], int] = {}
        for game in self.games:
            if self.is_division_game(game):
                pair_counts[(game.home, game.away)] = pair_counts.get((game.home, game.away), 0) + 1

        for division_teams in divisions.values():
            for team_a in division_teams:
                for team_b in division_teams:
                    if team_a >= team_b:
                        continue
                    if pair_counts.get((team_a, team_b), 0) != 1 or pair_counts.get((team_b, team_a), 0) != 1:
                        raise ValueError(
                            f"Division pair {team_a}/{team_b} must appear once with each team at home."
                        )

    def add_thanksgiving_hosting_constraint(self):
        det_early_vars = []
        dal_late_vars = []
        for game_idx, game in enumerate(self.games):
            for slot_idx, slot in enumerate(self.slots):
                if slot.week != 13 or slot.day != "Thursday":
                    continue
                if game.home == "DET" and slot.time_et == "13:00":
                    det_early_vars.append(self.assignment_var(game_idx, slot_idx))
                if game.home == "DAL" and slot.time_et == "16:30":
                    dal_late_vars.append(self.assignment_var(game_idx, slot_idx))
        self.require_one_of(det_early_vars, "Detroit Thanksgiving host")
        self.require_one_of(dal_late_vars, "Dallas Thanksgiving host")

    def add_bye_week_window_constraint(self):
        for team in self.teams:
            for week in [1, 2, 3, 4, 15, 16, 17, 18]:
                self.require_one_of(
                    self.team_week_vars(team, week),
                    f"{team} playing in no-bye window week {week}",
                )

    def add_week_18_division_games_constraint(self):
        for game_idx, game in enumerate(self.games):
            if self.is_division_game(game):
                continue
            for slot_idx, slot in enumerate(self.slots):
                if slot.week == 18:
                    self.add_clause([-self.assignment_var(game_idx, slot_idx)])

    def build_model(self):
        self.load_data()
        self.validate_division_home_away_pairs()
        self.create_variables()

        self.add_each_game_exactly_once()
        self.add_slot_capacity_constraints()
        self.add_team_at_most_one_game_per_week()
        self.add_stadium_conflict_constraint()
        self.add_west_coast_and_mountain_home_window_constraint()
        self.add_bye_week_window_constraint()
        self.add_no_four_straight_road_games_constraint()
        self.add_week_18_division_games_constraint()
        
        self.add_super_bowl_champion_home_opener_constraint()
        self.add_thanksgiving_hosting_constraint()

        self.add_no_three_games_in_eleven_days_constraint()
        self.add_short_week_limit_constraint()
        self.add_no_cross_country_ping_pong_constraint()

    def assignment_vars(self) -> list[int]:
        return [
            var
            for key, var in self.var_map.items()
            if key[0] == "assign"
        ]

    def solver_class(self, name: str):
        solvers = {
            "cadical195": Cadical195,
            "glucose4": Glucose4,
            "kissat404": Kissat404,
            "minisat22": Minisat22,
        }
        try:
            return solvers[name.lower()]
        except KeyError as exc:
            raise ValueError(f"Unsupported solver '{name}'.") from exc

    def decode_model(self, model: list[int]) -> list[dict[str, str | int | bool]]:
        positive = {literal for literal in model if literal > 0}
        rows = []
        for var in self.assignment_vars():
            if var not in positive:
                continue
            _, game_idx, slot_idx = self.rev_var_map[var]
            game = self.games[game_idx]
            slot = self.slots[slot_idx]
            rows.append(
                {
                    "week": slot.week,
                    "date": slot.game_date.isoformat(),
                    "day": slot.day,
                    "time_et": slot.time_et,
                    "international": slot.international,
                    "away": game.away,
                    "home": game.home,
                    "game_idx": game_idx,
                    "slot_idx": slot_idx,
                    "var": var,
                }
            )
        return sorted(rows, key=lambda row: (row["week"], row["date"], row["time_et"], row["home"], row["away"]))

    def format_schedule(self, schedule: list[dict[str, str | int | bool]]) -> str:
        lines = []
        current_week = None
        for row in schedule:
            if row["week"] != current_week:
                current_week = row["week"]
                lines.append(f"Week {current_week}")
            intl = " INTL" if row["international"] else ""
            lines.append(
                f"  {row['date']} {row['day']} {row['time_et']}{intl}: {row['away']} at {row['home']}"
            )
        return "\n".join(lines)

    def solve_one_schedule(
        self,
        solver_name: str = "kissat404",
        random_seed: int | None = None,
    ) -> list[dict[str, str | int | bool]] | None:
        solver_cls = self.solver_class(solver_name)
        assignment_vars = self.assignment_vars()
        rng = random.Random(random_seed)

        with solver_cls(bootstrap_with=self.clauses) as solver:
            if random_seed is not None:
                phases = [var if rng.random() < 0.5 else -var for var in assignment_vars]
                try:
                    solver.set_phases(phases)
                except NotImplementedError:
                    pass
            if not solver.solve():
                return None
            return self.decode_model(solver.get_model())

    def sample_diverse_schedules(
        self,
        limit: int,
        solver_name: str = "kissat404",
        random_seed: int | None = None,
    ) -> list[list[dict[str, str | int | bool]]]:
        solver_cls = self.solver_class(solver_name)
        assignment_vars = self.assignment_vars()
        rng = random.Random(random_seed)
        samples = []

        with solver_cls(bootstrap_with=self.clauses) as solver:
            while len(samples) < limit:
                phases = [var if rng.random() < 0.5 else -var for var in assignment_vars]
                try:
                    solver.set_phases(phases)
                except NotImplementedError:
                    pass

                if not solver.solve():
                    break

                schedule = self.decode_model(solver.get_model())
                samples.append(schedule)

                chosen_assignment_vars = [int(row["var"]) for row in schedule]
                solver.add_clause([-var for var in chosen_assignment_vars])

        return samples

    def to_dimacs(self) -> str:
        lines = [f"p cnf {self.var_counter - 1} {len(self.clauses)}"]
        for clause in self.clauses:
            lines.append(" ".join(map(str, clause)) + " 0")
        return "\n".join(lines)

    def write_dimacs(self, filename: str):
        with open(filename, "w", encoding="utf-8") as handle:
            handle.write(self.to_dimacs())


if __name__ == "__main__":
    output_path = Path("solved_schedule.txt")
    scheduler = NFLSchedulerSAT(
        "nfl_2025_2026_regular_season_team_abbreviations.csv",
        "nfl_2025_2026_regular_season_et_international_only.csv",
    )
    scheduler.build_model()
    schedule = scheduler.solve_one_schedule(random_seed=7)
    if schedule is None:
        print("UNSAT")
    else:
        rendered = scheduler.format_schedule(schedule)
        output_path.write_text(rendered, encoding="utf-8")
        print(f"Wrote {output_path}")
        print(rendered)
