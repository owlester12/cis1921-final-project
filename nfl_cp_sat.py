from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

try:
    from ortools.sat.python import cp_model
except ModuleNotFoundError as exc:
    raise ModuleNotFoundError(
        "Missing OR-Tools dependency. Install it in the Python interpreter that "
        "runs this script with:\n"
        "python -m pip install ortools"
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
TEAM_TO_DIVISION = {
    team: division
    for division, teams in divisions.items()
    for team in teams
}

# Team market size/viewership value (higher = larger market/fanbase)
# Based on TV market rankings and team popularity
TEAM_MARKET_VALUES = {
    "DAL": 100,  # Largest market
    "NYG": 95,   
    "PHI": 90,   
    "NYJ": 85,   
    "CHI": 85,   
    "LAR": 80,   
    "SF": 80,    
    "NE": 75,    
    "BUF": 75,   
    "LAC": 70,   
    "DEN": 70,   
    "KC": 70,    
    "MIN": 65,   
    "PIT": 65,   
    "BAL": 65,   
    "WAS": 60,   
    "SEA": 60,   
    "GB": 60,    
    "DET": 55,   
    "NO": 55,    
    "TB": 55,    
    "ATL": 50,   
    "MIA": 50,   
    "CIN": 50,   
    "HOU": 50,   
    "ARI": 45,   
    "CAR": 40,   
    "IND": 40,   
    "TEN": 40,   
    "JAX": 35,   
    "LV": 35,    
    "CLE": 50,   
}

# High-profile rivalries that draw viewership
# Rivalries are bidirectional and should be prioritized for good slots
KEY_RIVALRIES = {
    frozenset(["DAL", "PHI"]),  # NFC East
    frozenset(["DAL", "NYG"]),
    frozenset(["DAL", "WAS"]),
    frozenset(["PHI", "NYG"]),
    frozenset(["PHI", "WAS"]),
    frozenset(["NYG", "WAS"]),
    frozenset(["SF", "LAR"]),   # NFC West
    frozenset(["SF", "SEA"]),
    frozenset(["LAR", "SEA"]),
    frozenset(["PIT", "BAL"]),  # AFC North
    frozenset(["PIT", "CLE"]),
    frozenset(["BAL", "CIN"]),
    frozenset(["NE", "NYJ"]),   # AFC East
    frozenset(["NE", "MIA"]),
    frozenset(["NE", "BUF"]),
    frozenset(["NYJ", "MIA"]),
    frozenset(["NYJ", "BUF"]),
    frozenset(["MIA", "BUF"]),
    frozenset(["KC", "LAC"]),   # AFC West
    frozenset(["KC", "DEN"]),
    frozenset(["LAC", "DEN"]),
    frozenset(["GB", "MIN"]),   # NFC North
    frozenset(["GB", "CHI"]),
    frozenset(["GB", "DET"]),
    frozenset(["MIN", "CHI"]),
    frozenset(["MIN", "DET"]),
    frozenset(["CHI", "DET"]),
    frozenset(["NO", "TB"]),    # NFC South
    frozenset(["ATL", "TB"]),
    frozenset(["ATL", "CAR"]),
}


@dataclass(frozen=True)
class Game:
    away: str
    home: str


@dataclass(frozen=True)
class Slot:
    slot_id: int
    week: int
    day: str
    game_date: date
    time_et: str
    international: bool


class NFLSchedulerCPSAT:
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

        self.model = cp_model.CpModel()
        self.game_to_slot: list[cp_model.IntVar] = []
        self.slot_to_game: list[cp_model.IntVar] = []

        self.team_game_indices: dict[str, list[int]] = {}
        self.team_home_game_indices: dict[str, list[int]] = {}
        self.team_away_game_indices: dict[str, list[int]] = {}
        self.games_by_home_team: dict[str, list[int]] = {}
        self.slots_by_week: dict[int, list[int]] = {}
        self.slots_by_week_day: dict[tuple[int, str], list[int]] = {}
        self.slot_week_bools: dict[tuple[int, int], cp_model.IntVar] = {}
        self.slot_day_bools: dict[tuple[int, int, str], cp_model.IntVar] = {}
        self.slot_timezone_bools: dict[tuple[int, int, int], cp_model.IntVar] = {}

        self.team_week_played: dict[tuple[str, int], cp_model.IntVar] = {}
        self.team_week_day_played: dict[tuple[str, int, str], cp_model.IntVar] = {}
        self.team_week_timezone_played: dict[tuple[str, int, int], cp_model.IntVar] = {}
        self.team_week_road: dict[tuple[str, int], cp_model.IntVar] = {}
        
        # For TV viewership optimization
        self.slot_values: dict[int, int] = {}  # slot_id -> viewership importance value
        self.game_quality_scores: dict[int, int] = {}  # game_idx -> base quality score
        self.game_slot_scores: dict[tuple[int, int], int] = {}  # (game_idx, slot_idx) -> combined score

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

    def build_unique_slots(self, rows: list[dict[str, str]]) -> list[Slot]:
        slots = []
        for slot_id, row in enumerate(rows):
            game_date = self.parse_csv_date(row["Date"])
            week = ((game_date - self.SEASON_START).days // 7) + 1
            slots.append(
                Slot(
                    slot_id=slot_id,
                    week=week,
                    day=row["Day"],
                    game_date=game_date,
                    time_et=self.parse_csv_time(row["Time (ET)"]),
                    international=row["International"].strip().lower() == "yes",
                )
            )
        return slots

    def load_data(self):
        game_rows = self.read_simple_csv(self.matchups_csv_path)
        slot_rows = self.read_simple_csv(self.slots_csv_path)

        self.games = [Game(away=row["Away"], home=row["Home"]) for row in game_rows]
        self.slots = self.build_unique_slots(slot_rows)

        if len(self.games) != len(self.slots):
            raise ValueError("The matchup count must match the number of unique CSV slots.")

    def validate_division_home_away_pairs(self):
        pair_counts: dict[tuple[str, str], int] = {}
        for game in self.games:
            if TEAM_TO_DIVISION[game.home] == TEAM_TO_DIVISION[game.away]:
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

    def build_indices(self):
        for team in self.teams:
            self.team_game_indices[team] = [
                idx for idx, game in enumerate(self.games)
                if game.home == team or game.away == team
            ]
            self.team_home_game_indices[team] = [
                idx for idx, game in enumerate(self.games)
                if game.home == team
            ]
            self.team_away_game_indices[team] = [
                idx for idx, game in enumerate(self.games)
                if game.away == team
            ]
            self.games_by_home_team[team] = self.team_home_game_indices[team]

        for week in self.weeks:
            self.slots_by_week[week] = [slot.slot_id for slot in self.slots if slot.week == week]
            for day in ("Monday", "Sunday", "Thursday", "Saturday"):
                self.slots_by_week_day[(week, day)] = [
                    slot.slot_id
                    for slot in self.slots
                    if slot.week == week and slot.day == day
                ]

    def create_variables(self):
        slot_max = len(self.slots) - 1
        game_max = len(self.games) - 1
        self.game_to_slot = [
            self.model.NewIntVar(0, slot_max, f"game_to_slot_{game_idx}")
            for game_idx in range(len(self.games))
        ]
        self.slot_to_game = [
            self.model.NewIntVar(0, game_max, f"slot_to_game_{slot_idx}")
            for slot_idx in range(len(self.slots))
        ]
        self.model.AddInverse(self.game_to_slot, self.slot_to_game)

    def eq_var(self, int_var: cp_model.IntVar, value: int, name: str) -> cp_model.IntVar:
        var = self.model.NewBoolVar(name)
        self.model.Add(int_var == value).OnlyEnforceIf(var)
        self.model.Add(int_var != value).OnlyEnforceIf(var.Not())
        return var

    def membership_var(self, int_var: cp_model.IntVar, allowed: list[int], name: str) -> cp_model.IntVar:
        var = self.model.NewBoolVar(name)
        if not allowed:
            self.model.Add(var == 0)
            return var

        allowed_set = sorted(set(allowed))
        self.model.AddAllowedAssignments([int_var], [[value] for value in allowed_set]).OnlyEnforceIf(var)
        self.model.AddForbiddenAssignments([int_var], [[value] for value in allowed_set]).OnlyEnforceIf(var.Not())
        return var

    def build_slot_relation_bools(self):
        for game_idx, game in enumerate(self.games):
            for week in self.weeks:
                self.slot_week_bools[(game_idx, week)] = self.membership_var(
                    self.game_to_slot[game_idx],
                    self.slots_by_week[week],
                    f"game_{game_idx}_week_{week}",
                )
                for day in ("Monday", "Sunday", "Thursday", "Saturday"):
                    self.slot_day_bools[(game_idx, week, day)] = self.membership_var(
                        self.game_to_slot[game_idx],
                        self.slots_by_week_day[(week, day)],
                        f"game_{game_idx}_week_{week}_{day}",
                    )
                venue_tz = team_timezones[game.home]
                self.slot_timezone_bools[(game_idx, week, venue_tz)] = self.membership_var(
                    self.game_to_slot[game_idx],
                    [
                        slot.slot_id
                        for slot in self.slots
                        if slot.week == week and team_timezones[game.home] == venue_tz
                    ],
                    f"game_{game_idx}_week_{week}_tz_{venue_tz}",
                )

    def calculate_slot_values(self):
        """Assign viewership importance values to each slot based on day and time."""
        for slot in self.slots:
            # Prime time slots get highest values
            if slot.international:
                self.slot_values[slot.slot_id] = 10  # International games are lowest priority
            elif slot.day == "Thursday" and slot.time_et in ("20:20", "20:30"):
                self.slot_values[slot.slot_id] = 100  # Thursday Night Football
            elif slot.day == "Sunday" and slot.time_et == "20:30":
                self.slot_values[slot.slot_id] = 90  # Sunday Night Football
            elif slot.day == "Monday" and slot.time_et == "20:15":
                self.slot_values[slot.slot_id] = 85  # Monday Night Football
            elif slot.day == "Sunday" and slot.time_et == "16:30":
                self.slot_values[slot.slot_id] = 80  # Late Sunday afternoon
            elif slot.day == "Sunday" and slot.time_et == "13:00":
                self.slot_values[slot.slot_id] = 70  # Early Sunday afternoon
            elif slot.day == "Saturday":
                self.slot_values[slot.slot_id] = 65  # Saturday games
            elif slot.day == "Sunday" and slot.time_et == "09:30":
                self.slot_values[slot.slot_id] = 50  # Early morning Sunday
            else:
                self.slot_values[slot.slot_id] = 40  # Other times

    def is_rivalry(self, team1: str, team2: str) -> bool:
        """Check if two teams are in a key rivalry."""
        return frozenset([team1, team2]) in KEY_RIVALRIES

    def calculate_game_quality_scores(self):
        """Calculate the base quality score for each game based on teams involved."""
        for game_idx, game in enumerate(self.games):
            # Base score: average of both teams' market values
            home_score = TEAM_MARKET_VALUES.get(game.home, 30)
            away_score = TEAM_MARKET_VALUES.get(game.away, 30)
            base_score = (home_score + away_score) // 2
            
            # Add bonus for rivalries (high-interest matchups)
            rivalry_bonus = 0
            if self.is_rivalry(game.home, game.away):
                rivalry_bonus = 20
            
            self.game_quality_scores[game_idx] = base_score + rivalry_bonus

    def calculate_game_slot_scores(self):
        """Calculate the combined score for each game-slot pair."""
        for game_idx in range(len(self.games)):
            game_quality = self.game_quality_scores[game_idx]
            for slot_id, slot_value in self.slot_values.items():
                # Combined score is quality * slot importance
                # Normalized by dividing by 100 to scale reasonably
                self.game_slot_scores[(game_idx, slot_id)] = (game_quality * slot_value) // 10

    def add_viewership_objective(self):
        """Add objective function to maximize TV viewership."""
        # Create a more efficient objective using element constraints
        # For each game, determine its score based on which slot it's assigned to
        objective_terms = []
        
        for game_idx in range(len(self.games)):
            # Create an IntVar representing the score for this game's slot assignment
            slot_assignment = self.game_to_slot[game_idx]
            
            # Build a mapping of slot_id -> score for this game
            slot_scores = []
            for slot_id in range(len(self.slots)):
                score = self.game_slot_scores[(game_idx, slot_id)]
                slot_scores.append(score)
            
            # Create a variable that takes the score value corresponding to the assigned slot
            game_score = self.model.NewIntVar(0, max(slot_scores) if slot_scores else 0, f"game_score_{game_idx}")
            self.model.AddElement(slot_assignment, slot_scores, game_score)
            objective_terms.append(game_score)
        
        if objective_terms:
            self.model.Maximize(sum(objective_terms))

    def make_team_week_helpers(self):
        for team in self.teams:
            team_games = self.team_game_indices[team]
            team_road_games = self.team_away_game_indices[team]
            for week in self.weeks:
                played = self.model.NewBoolVar(f"played_{team}_w{week}")
                self.model.AddMaxEquality(
                    played,
                    [self.slot_week_bools[(game_idx, week)] for game_idx in team_games],
                )
                self.team_week_played[(team, week)] = played

                road = self.model.NewBoolVar(f"road_{team}_w{week}")
                if team_road_games:
                    self.model.AddMaxEquality(
                        road,
                        [self.slot_week_bools[(game_idx, week)] for game_idx in team_road_games],
                    )
                else:
                    self.model.Add(road == 0)
                self.team_week_road[(team, week)] = road

                for day in ("Monday", "Sunday", "Thursday", "Saturday"):
                    helper = self.model.NewBoolVar(f"played_{team}_w{week}_{day}")
                    self.model.AddMaxEquality(
                        helper,
                        [self.slot_day_bools[(game_idx, week, day)] for game_idx in team_games],
                    )
                    self.team_week_day_played[(team, week, day)] = helper

                for tz in range(4):
                    relevant = [
                        game_idx for game_idx in team_games
                        if team_timezones[self.games[game_idx].home] == tz
                    ]
                    helper = self.model.NewBoolVar(f"tz_{team}_w{week}_{tz}")
                    if relevant:
                        self.model.AddMaxEquality(
                            helper,
                            [self.slot_week_bools[(game_idx, week)] for game_idx in relevant],
                        )
                    else:
                        self.model.Add(helper == 0)
                    self.team_week_timezone_played[(team, week, tz)] = helper

    def add_team_at_most_one_game_per_week(self):
        for team in self.teams:
            for week in self.weeks:
                self.model.Add(
                    sum(self.slot_week_bools[(game_idx, week)] for game_idx in self.team_game_indices[team]) <= 1
                )

    def add_super_bowl_champion_home_opener_constraint(self):
        candidates = []
        for game_idx in self.games_by_home_team[self.super_bowl_champion]:
            candidates.append(
                self.membership_var(
                    self.game_to_slot[game_idx],
                    [slot.slot_id for slot in self.slots if slot.week == 1 and slot.day == "Thursday"],
                    f"sb_opener_{game_idx}",
                )
            )
        self.model.AddBoolOr(candidates)

    def add_no_four_straight_road_games_constraint(self):
        for team in self.teams:
            for start in range(1, 16):
                self.model.Add(
                    sum(self.team_week_road[(team, week)] for week in range(start, start + 4)) <= 3
                )

    def add_no_cross_country_ping_pong_constraint(self):
        for team in self.teams:
            for week in range(1, 16):
                for tz1 in range(4):
                    for tz2 in range(4):
                        for tz3 in range(4):
                            for tz4 in range(4):
                                distance = abs(tz2 - tz1) + abs(tz3 - tz2) + abs(tz4 - tz3)
                                if distance <= 7:
                                    continue
                                self.model.AddBoolOr([
                                    self.team_week_timezone_played[(team, week, tz1)].Not(),
                                    self.team_week_timezone_played[(team, week + 1, tz2)].Not(),
                                    self.team_week_timezone_played[(team, week + 2, tz3)].Not(),
                                    self.team_week_timezone_played[(team, week + 3, tz4)].Not(),
                                ])

    def add_stadium_conflict_constraint(self):
        for team_a, team_b in SAME_STADIUM_PAIRS:
            team_a_games = self.games_by_home_team[team_a]
            team_b_games = self.games_by_home_team[team_b]
            windows: dict[date, list[int]] = {}
            for slot in self.slots:
                windows.setdefault(slot.game_date, []).append(slot.slot_id)

            for same_day_slots in windows.values():
                forbidden_pairs = [
                    [slot_a, slot_b]
                    for slot_a in same_day_slots
                    for slot_b in same_day_slots
                ]
                for game_a in team_a_games:
                    for game_b in team_b_games:
                        self.model.AddForbiddenAssignments(
                            [self.game_to_slot[game_a], self.game_to_slot[game_b]],
                            forbidden_pairs,
                        )

    def add_no_three_games_in_eleven_days_constraint(self):
        for team in self.teams:
            for week in range(1, 17):
                self.model.AddBoolOr([
                    self.team_week_day_played[(team, week, "Monday")].Not(),
                    self.team_week_day_played[(team, week + 1, "Sunday")].Not(),
                    self.team_week_day_played[(team, week + 2, "Thursday")].Not(),
                ])
                self.model.AddBoolOr([
                    self.team_week_day_played[(team, week, "Monday")].Not(),
                    self.team_week_day_played[(team, week + 1, "Monday")].Not(),
                    self.team_week_day_played[(team, week + 2, "Thursday")].Not(),
                ])

    def add_short_week_limit_constraint(self):
        for team in self.teams:
            short_week_vars = []
            for week in range(2, 19):
                previous_any = self.model.NewBoolVar(f"prev_short_{team}_{week}")
                self.model.AddMaxEquality(
                    previous_any,
                    [
                        self.team_week_day_played[(team, week - 1, "Sunday")],
                        self.team_week_day_played[(team, week - 1, "Monday")],
                        self.team_week_day_played[(team, week - 1, "Saturday")],
                    ],
                )
                short_var = self.model.NewBoolVar(f"short_{team}_{week}")
                self.model.Add(short_var <= previous_any)
                self.model.Add(short_var <= self.team_week_day_played[(team, week, "Thursday")])
                self.model.Add(short_var >= previous_any + self.team_week_day_played[(team, week, "Thursday")] - 1)
                short_week_vars.append(short_var)
            self.model.Add(sum(short_week_vars) <= 2)

    def add_west_coast_and_mountain_home_window_constraint(self):
        forbidden_slots = [
            slot.slot_id
            for slot in self.slots
            if not slot.international and slot.time_et == "13:00"
        ]
        for team in WESTERN_HOME_TEAMS:
            for game_idx in self.games_by_home_team[team]:
                self.model.AddForbiddenAssignments(
                    [self.game_to_slot[game_idx]],
                    [[slot_id] for slot_id in forbidden_slots],
                )

    def add_thanksgiving_hosting_constraint(self):
        det_slots = [
            slot.slot_id
            for slot in self.slots
            if slot.week == 13 and slot.day == "Thursday" and slot.time_et == "13:00"
        ]
        dal_slots = [
            slot.slot_id
            for slot in self.slots
            if slot.week == 13 and slot.day == "Thursday" and slot.time_et == "16:30"
        ]
        self.model.AddBoolOr([
            self.membership_var(self.game_to_slot[game_idx], det_slots, f"det_thanks_{game_idx}")
            for game_idx in self.games_by_home_team["DET"]
        ])
        self.model.AddBoolOr([
            self.membership_var(self.game_to_slot[game_idx], dal_slots, f"dal_thanks_{game_idx}")
            for game_idx in self.games_by_home_team["DAL"]
        ])

    def add_bye_week_window_constraint(self):
        for team in self.teams:
            for week in [1, 2, 3, 4, 15, 16, 17, 18]:
                self.model.Add(self.team_week_played[(team, week)] == 1)

    def add_week_18_division_games_constraint(self):
        week18_slots = [slot.slot_id for slot in self.slots if slot.week == 18]
        for game_idx, game in enumerate(self.games):
            if TEAM_TO_DIVISION[game.home] == TEAM_TO_DIVISION[game.away]:
                continue
            self.model.AddForbiddenAssignments(
                [self.game_to_slot[game_idx]],
                [[slot_id] for slot_id in week18_slots],
            )

    def build_model(self):
        self.load_data()
        self.validate_division_home_away_pairs()
        self.build_indices()
        self.create_variables()
        self.build_slot_relation_bools()
        self.make_team_week_helpers()

        self.add_team_at_most_one_game_per_week()
        self.add_super_bowl_champion_home_opener_constraint()
        self.add_no_four_straight_road_games_constraint()
        self.add_stadium_conflict_constraint()
        self.add_no_three_games_in_eleven_days_constraint()
        self.add_short_week_limit_constraint()
        self.add_west_coast_and_mountain_home_window_constraint()
        self.add_thanksgiving_hosting_constraint()
        self.add_bye_week_window_constraint()
        self.add_week_18_division_games_constraint()
        self.add_no_cross_country_ping_pong_constraint()
        
        # TV viewership optimization
        self.calculate_slot_values()
        self.calculate_game_quality_scores()
        self.calculate_game_slot_scores()
        self.add_viewership_objective()

    def solve(
        self,
        max_time_seconds: float = 600.0,
        num_workers: int = 8,
    ) -> list[dict[str, str | int | bool]] | None:
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = max_time_seconds
        solver.parameters.num_search_workers = num_workers

        status = solver.Solve(self.model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return None

        rows = []
        for game_idx, game in enumerate(self.games):
            slot_idx = solver.Value(self.game_to_slot[game_idx])
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


if __name__ == "__main__":
    output_path = Path("outputs/solved_schedule_cp.txt")
    scheduler = NFLSchedulerCPSAT(
        "csvs/nfl_2025_2026_regular_season_team_abbreviations.csv",
        "csvs/nfl_2025_2026_regular_season_et_international_only.csv",
    )
    scheduler.build_model()
    schedule = scheduler.solve()
    if schedule is None:
        print("UNSAT_OR_TIMEOUT")
    else:
        rendered = scheduler.format_schedule(schedule)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
        print(f"Wrote {output_path}")
        print(rendered)
