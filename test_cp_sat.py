#!/usr/bin/env python
"""Quick test of the CP-SAT scheduler with viewership optimization."""

from nfl_cp_sat import NFLSchedulerCPSAT, TEAM_MARKET_VALUES, KEY_RIVALRIES

# Test 1: Verify data structures
print("Test 1: Verifying data structure initialization...")
print(f"  Number of teams with market values: {len(TEAM_MARKET_VALUES)}")
print(f"  Number of key rivalries: {len(KEY_RIVALRIES)}")
print(f"  Top team markets: DAL={TEAM_MARKET_VALUES['DAL']}, NYG={TEAM_MARKET_VALUES['NYG']}, PHI={TEAM_MARKET_VALUES['PHI']}")

# Test 2: Create scheduler (without solving yet)
print("\nTest 2: Creating scheduler instance...")
try:
    scheduler = NFLSchedulerCPSAT(
        "csvs/nfl_2025_2026_regular_season_team_abbreviations.csv",
        "csvs/nfl_2025_2026_regular_season_et_international_only.csv",
    )
    print("  Scheduler created successfully")
except Exception as e:
    print(f"  ERROR: {e}")
    exit(1)

# Test 3: Test slot value calculation
print("\nTest 3: Testing slot value calculation...")
try:
    # Load data first
    scheduler.load_data()
    scheduler.calculate_slot_values()
    
    # Find and display some slot values
    thursday_night = [s for s in scheduler.slots if s.day == "Thursday" and s.time_et in ("20:20", "20:30")]
    sunday_night = [s for s in scheduler.slots if s.day == "Sunday" and s.time_et == "20:30"]
    monday_night = [s for s in scheduler.slots if s.day == "Monday" and s.time_et == "20:15"]
    
    if thursday_night:
        tn_slot = thursday_night[0]
        print(f"  Thursday Night slot value: {scheduler.slot_values[tn_slot.slot_id]}")
    if sunday_night:
        sn_slot = sunday_night[0]
        print(f"  Sunday Night slot value: {scheduler.slot_values[sn_slot.slot_id]}")
    if monday_night:
        mn_slot = monday_night[0]
        print(f"  Monday Night slot value: {scheduler.slot_values[mn_slot.slot_id]}")
    
    print(f"  Total slots: {len(scheduler.slots)}")
    print(f"  Total games: {len(scheduler.games)}")
except Exception as e:
    print(f"  ERROR: {e}")
    exit(1)

# Test 4: Test game quality score calculation
print("\nTest 4: Testing game quality score calculation...")
try:
    scheduler.build_indices()
    scheduler.calculate_game_quality_scores()
    
    # Find some high-value games
    high_quality_games = sorted(
        [(g, scheduler.game_quality_scores[g]) for g in range(len(scheduler.games))],
        key=lambda x: x[1],
        reverse=True
    )[:5]
    
    print("  Top 5 highest quality games:")
    for game_idx, score in high_quality_games:
        game = scheduler.games[game_idx]
        is_rivalry = scheduler.is_rivalry(game.home, game.away)
        print(f"    {game.away} @ {game.home}: score={score}, rivalry={is_rivalry}")
    
except Exception as e:
    print(f"  ERROR: {e}")
    exit(1)

# Test 5: Test game-slot score calculation
print("\nTest 5: Testing game-slot score calculation...")
try:
    scheduler.calculate_game_slot_scores()
    
    # Find the best game-slot combinations
    best_scores = sorted(
        [(g, s, scheduler.game_slot_scores[(g, s)]) 
         for g in range(len(scheduler.games)) 
         for s in range(len(scheduler.slots))],
        key=lambda x: x[2],
        reverse=True
    )[:5]
    
    print("  Top 5 game-slot combinations:")
    for game_idx, slot_idx, score in best_scores:
        game = scheduler.games[game_idx]
        slot = scheduler.slots[slot_idx]
        print(f"    {game.away} @ {game.home} on {slot.day} {slot.time_et}: score={score}")
        
except Exception as e:
    print(f"  ERROR: {e}")
    exit(1)

print("\n✓ All tests passed!")
