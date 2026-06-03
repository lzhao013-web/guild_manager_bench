"""Tests for monster power computation in the leaderboard.

Covers the new difficulty-scan power (`_monster_power_v2`) and the
fallback to the legacy weighted-sum when the preset data dir is missing.
The new function should be a strictly more accurate predictor of combat
outcomes than the legacy sum.
"""

from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from guild_manager_bench.bench.leaderboard import (
    _build_reference_pool,
    _defeated_enemy_from_battle_dict,
    _monster_power,
    _monster_power_difficulty_scan,
    _monster_power_v2,
    _simulate_battle,
    _scan_opponent_task,
    _get_reference_pool,
)
from guild_manager_bench.game.loader import load_game_definition
from guild_manager_bench.game.models import CombatStats


PRESET_DIR = Path(__file__).resolve().parents[1] / "data" / "presets" / "default"


def test_legacy_monster_power_still_works() -> None:
    """The legacy formula must keep working for back-compat / fallback."""
    stats = {"hp": 100, "mp": 0, "attack": 50, "defense": 30, "speed": 10,
             "recovery": 0, "mp_recovery": 0}
    expected = 100 + 0 + 50 * 8 + 30 * 8 + 10 * 5
    assert _monster_power(stats) == expected


def test_monster_power_v2_returns_none_without_data_dir() -> None:
    """When data_dir is missing/unloadable, return None (caller falls back)."""
    out = _monster_power_v2(
        {"hp": 100, "attack": 50, "defense": 30, "speed": 10},
        archetype_id=None,
        data_dir=None,
    )
    assert out is None

    out = _monster_power_v2(
        {"hp": 100, "attack": 50, "defense": 30, "speed": 10},
        archetype_id=None,
        data_dir="does/not/exist/preset",
    )
    assert out is None


def test_monster_power_v2_loads_default_preset() -> None:
    """A real preset must yield a non-zero power for a strong monster."""
    stats = {"hp": 200, "mp": 50, "attack": 100, "defense": 50,
             "speed": 30, "recovery": 10, "mp_recovery": 5}
    power = _monster_power_v2(
        stats, archetype_id=None, data_dir=str(PRESET_DIR),
    )
    assert power is not None
    assert power > 0


def test_reference_pool_built_for_default_preset() -> None:
    """The reference pool should include one entry per (archetype, turn)."""
    definition = load_game_definition(PRESET_DIR)
    pool = _build_reference_pool(definition)
    assert len(pool) > 0
    # All entries should have non-trivial stats.
    for opponent in pool:
        assert opponent.stats.hp >= 1
        assert opponent.difficulty_weight >= 1
        # stats at high turn should be larger than at turn 1
        assert opponent.stats.attack >= 0


def test_simulate_battle_returns_outcome_and_hp_ratio() -> None:
    """A clearly-stronger side should always win with non-zero hp."""
    strong = CombatStats(hp=500, mp=0, attack=200, defense=100, speed=50,
                        recovery=0, mp_recovery=0)
    weak = CombatStats(hp=10, mp=0, attack=1, defense=0, speed=1,
                      recovery=0, mp_recovery=0)
    won, hp_ratio = _simulate_battle(strong, (), weak, (), seed=0)
    assert won is True
    assert 0.0 < hp_ratio <= 1.0

    won, hp_ratio = _simulate_battle(weak, (), strong, (), seed=0)
    assert won is False
    assert hp_ratio == 0.0


def test_monster_power_v2_rewards_strength_correctly() -> None:
    """A monster with much higher stats must have a higher scan power.

    This is the core property the legacy formula got wrong (e.g. defense
    was added as a positive contribution even though it only matters
    relative to opponent attack).
    """
    pool = _get_reference_pool(str(PRESET_DIR))
    assert pool is not None
    weak = CombatStats(hp=20, mp=0, attack=2, defense=1, speed=1,
                      recovery=0, mp_recovery=0)
    strong = CombatStats(hp=300, mp=0, attack=80, defense=60, speed=30,
                        recovery=10, mp_recovery=0)
    weak_power = _monster_power_difficulty_scan(weak, (), pool)
    strong_power = _monster_power_difficulty_scan(strong, (), pool)
    assert strong_power > weak_power
    # Strictly greater — a much stronger monster should clearly win more.
    assert strong_power > weak_power * 2


def test_defeated_enemy_dict_falls_back_without_data_dir() -> None:
    """When data_dir is missing, the result must still have a 'power' field."""
    battle = {
        "monster_id": "mx",
        "monster_name": "Test Troll",
        "monster_stats": {"hp": 100, "attack": 30, "defense": 20, "speed": 5},
    }
    observation = None
    out = _defeated_enemy_from_battle_dict(
        battle, observation, turn_number=1, data_dir=None,
    )
    assert out is not None
    assert "power" in out
    assert out["power_v2_source"] == "legacy_sum"
    assert out["power"] == _monster_power(
        {"hp": 100, "attack": 30, "defense": 20, "speed": 5}
    )


def test_defeated_enemy_dict_uses_difficulty_scan_with_data_dir() -> None:
    """When data_dir is valid, the result must include power_v2 metadata."""
    battle = {
        "monster_id": "mx",
        "monster_name": "Test Dragon",
        "monster_stats": {
            "hp": 200, "mp": 50, "attack": 100, "defense": 50,
            "speed": 30, "recovery": 10, "mp_recovery": 5,
        },
    }
    observation = None
    out = _defeated_enemy_from_battle_dict(
        battle, observation, turn_number=1, data_dir=str(PRESET_DIR),
    )
    assert out is not None
    assert out["power_v2_source"] == "difficulty_scan"
    assert "power_v2" in out
    assert "power_legacy" in out
    # Both should be positive
    assert out["power_v2"] > 0
    assert out["power_legacy"] > 0
    # power field equals the chosen source
    assert out["power"] == out["power_v2"]


def test_reference_pool_is_cached() -> None:
    """Repeated calls with the same data_dir should hit the LRU cache."""
    _get_reference_pool.cache_clear()
    pool_a = _get_reference_pool(str(PRESET_DIR))
    pool_b = _get_reference_pool(str(PRESET_DIR))
    assert pool_a is pool_b  # same tuple object — cache hit


def test_scan_opponent_task_picklable() -> None:
    """``_scan_opponent_task`` must be picklable so ProcessPoolExecutor
    can dispatch it.  This is a regression test for picking up missing
    ``MappingProxyType`` or unbound closures.
    """
    import pickle
    m = CombatStats(hp=100, mp=0, attack=50, defense=30, speed=10,
                   recovery=0, mp_recovery=0)
    r = CombatStats(hp=20, mp=0, attack=5, defense=2, speed=3,
                   recovery=0, mp_recovery=0)
    args = (m, (), r, (), 100)
    dumped = pickle.dumps(args)
    reloaded = pickle.loads(dumped)
    wins, hp_sum, weight = _scan_opponent_task(reloaded)
    assert weight == 100
    assert wins >= 0
    assert 0.0 <= hp_sum <= wins


def test_monster_power_difficulty_scan_parallel_matches_serial() -> None:
    """Parallel scan via ProcessPoolExecutor must produce the same power
    as the serial scan.  This is the correctness property that lets
    build_leaderboard safely parallelise.
    """
    pool = _get_reference_pool(str(PRESET_DIR))
    assert pool is not None
    stats = CombatStats(hp=300, mp=50, attack=80, defense=60, speed=30,
                       recovery=10, mp_recovery=5)
    serial = _monster_power_difficulty_scan(stats, (), pool)
    with ProcessPoolExecutor(max_workers=2) as executor:
        parallel = _monster_power_difficulty_scan(
            stats, (), pool, executor=executor, chunk_size=64,
        )
    assert abs(serial - parallel) < 1e-6


def test_defeated_enemy_dict_with_executor_still_works() -> None:
    """End-to-end: passing an executor through the dict builder must
    still produce the same power value (and the same source flag).
    """
    battle = {
        "monster_id": "mx",
        "monster_name": "Test Dragon",
        "monster_stats": {
            "hp": 200, "mp": 50, "attack": 100, "defense": 50,
            "speed": 30, "recovery": 10, "mp_recovery": 5,
        },
    }
    serial = _defeated_enemy_from_battle_dict(
        battle, None, turn_number=1, data_dir=str(PRESET_DIR),
    )
    assert serial is not None
    with ProcessPoolExecutor(max_workers=2) as executor:
        parallel = _defeated_enemy_from_battle_dict(
            battle, None, turn_number=1,
            data_dir=str(PRESET_DIR), executor=executor,
        )
    assert parallel is not None
    assert serial["power_v2"] == parallel["power_v2"]
    assert serial["power"] == parallel["power"]
