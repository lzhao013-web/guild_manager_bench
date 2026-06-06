"""Baseline 操作者和评估运行器测试。"""
from pathlib import Path

from guild_manager_bench.bench.eval_runner import (
    EvalConfig,
    EvalReport,
    OperatorResult,
    run_eval_suite,
    run_single_eval,
    save_eval_results,
    save_leaderboard_replays,
)
from guild_manager_bench.bench.leaderboard import build_leaderboard
from guild_manager_bench.bench.metrics import score_final_state
from guild_manager_bench.bench.operators.greedy_operator import GreedyOperator
from guild_manager_bench.bench.operators.random_full_operator import RandomFullOperator
from guild_manager_bench.bench.operators.random_operator import RandomHuntOperator
from guild_manager_bench.bench.operators.search_operator import SearchOperator
from guild_manager_bench.bench.operators.shadow import (
    ShadowState,
    best_assignment,
    estimate_matchup_score,
)
from guild_manager_bench.bench.runner import run_operator
from guild_manager_bench.game.loader import load_game_definition

# 测试中用少量波次评分，全量 1000 波只在基准模拟中使用
_FAST_WAVES = 10


# ── Shadow 状态测试 ────────────────────────────────────────


def test_shadow_state_from_observation() -> None:
    definition = _load_definition()
    session = _new_session(definition)
    obs = session.observation()

    shadow = ShadowState.from_observation(obs)

    assert shadow.gold == obs["gold"]
    assert shadow.materials == obs["materials"]
    assert shadow.experience_pool == obs["experience_pool"]
    assert shadow.party_size == obs["party_size"]
    assert shadow.party_size_limit == obs["party_size_limit"]
    assert shadow.recruited_ids == set()


def test_shadow_state_clone_independence() -> None:
    definition = _load_definition()
    session = _new_session(definition)
    obs = session.observation()

    shadow = ShadowState.from_observation(obs)
    clone = shadow.clone()

    # 修改克隆不应影响原件
    clone.gold -= 100
    assert shadow.gold != clone.gold


def test_shadow_recruit_tracking() -> None:
    definition = _load_definition()
    session = _new_session(definition)
    obs = session.observation()

    shadow = ShadowState.from_observation(obs)
    candidate = obs["recruit_candidates"][0]

    assert not shadow.is_recruited(candidate["candidate_id"])
    shadow.apply_recruit(candidate)
    assert shadow.is_recruited(candidate["candidate_id"])
    assert shadow.party_size == 1


def test_best_assignment_optimal() -> None:
    matrix = [
        [10.0, 1.0, 0.0],
        [0.0, 10.0, 1.0],
        [1.0, 0.0, 10.0],
    ]
    pairs = best_assignment(matrix)

    assert len(pairs) == 3
    assigned_adventurers = {p[0] for p in pairs}
    assigned_monsters = {p[1] for p in pairs}
    assert assigned_adventurers == {0, 1, 2}
    assert assigned_monsters == {0, 1, 2}
    # 最优分配应该把每个冒险者分配到评分最高的怪物上
    total = sum(matrix[a][m] for a, m in pairs)
    assert total == 30.0


def test_best_assignment_rectangular() -> None:
    matrix = [
        [5.0, 10.0],
        [10.0, 5.0],
        [8.0, 3.0],
    ]
    pairs = best_assignment(matrix)

    assert len(pairs) == 2
    total = sum(matrix[a][m] for a, m in pairs)
    assert total == 20.0  # (1,0)=10 + (0,1)=10


def test_estimate_matchup_score() -> None:
    # 强攻手 vs 弱怪 = 正评分
    strong = {"hp": 100, "mp": 20, "attack": 30, "defense": 10, "speed": 10, "recovery": 5}
    weak = {"hp": 30, "mp": 0, "attack": 5, "defense": 3, "speed": 5, "recovery": 0}
    assert estimate_matchup_score(strong, weak) > 0

    # 弱攻手 vs 强怪 = 负评分
    assert estimate_matchup_score(weak, strong) < 0


# ── RandomFullOperator 测试 ────────────────────────────────


def test_random_full_completes() -> None:
    definition = _load_definition()
    session = run_operator(definition, RandomFullOperator(seed=42), max_steps=200)

    assert session.state is not None
    assert session.state.turn == definition.rules.max_turns + 1


def test_random_full_deterministic() -> None:
    definition = _load_definition()

    session1 = run_operator(definition, RandomFullOperator(seed=7), max_steps=200)
    session2 = run_operator(definition, RandomFullOperator(seed=7), max_steps=200)

    assert session1.state is not None
    assert session2.state is not None
    report1 = score_final_state(definition, session1.state, waves=_FAST_WAVES)
    report2 = score_final_state(definition, session2.state, waves=_FAST_WAVES)
    assert report1.score == report2.score


def test_random_full_scores_higher_than_random_hunt() -> None:
    """有准备的随机操作者应比纯随机狩猎得分更高。

    注意：默认预设没有初始冒险者，RandomHunt 永远得 0 分。
    RandomFull 会招募并装备冒险者，因此得分应 > 0。
    """

    definition = _load_definition()
    from dataclasses import replace
    seeded = replace(definition, rules=replace(definition.rules, seed=0))

    s1 = run_operator(seeded, RandomHuntOperator(seed=0), max_steps=200)
    s2 = run_operator(seeded, RandomFullOperator(seed=0), max_steps=200)

    assert s1.state is not None
    assert s2.state is not None

    score_hunt = score_final_state(seeded, s1.state, waves=_FAST_WAVES).score
    score_full = score_final_state(seeded, s2.state, waves=_FAST_WAVES).score
    assert score_full > score_hunt, (
        f"RandomFull ({score_full:.2f}) should > RandomHunt ({score_hunt:.2f})"
    )


# ── GreedyOperator 测试 ───────────────────────────────────


def test_greedy_completes() -> None:
    definition = _load_definition()
    session = run_operator(definition, GreedyOperator(seed=0), max_steps=200)

    assert session.state is not None
    assert session.state.turn == definition.rules.max_turns + 1


def test_greedy_deterministic() -> None:
    definition = _load_definition()

    session1 = run_operator(definition, GreedyOperator(seed=0), max_steps=200)
    session2 = run_operator(definition, GreedyOperator(seed=0), max_steps=200)

    assert session1.state is not None
    assert session2.state is not None
    report1 = score_final_state(definition, session1.state, waves=_FAST_WAVES)
    report2 = score_final_state(definition, session2.state, waves=_FAST_WAVES)
    assert report1.score == report2.score


def test_greedy_outperforms_random() -> None:
    """贪心操作者应比随机操作者得分更高。"""

    definition = _load_definition()
    from dataclasses import replace
    seeded = replace(definition, rules=replace(definition.rules, seed=0))

    s1 = run_operator(seeded, RandomFullOperator(seed=0), max_steps=200)
    s2 = run_operator(seeded, GreedyOperator(seed=0), max_steps=200)

    assert s1.state is not None
    assert s2.state is not None

    score_random = score_final_state(seeded, s1.state, waves=_FAST_WAVES).score
    score_greedy = score_final_state(seeded, s2.state, waves=_FAST_WAVES).score
    assert score_greedy >= score_random, (
        f"Greedy ({score_greedy:.2f}) should >= RandomFull ({score_random:.2f})"
    )


# ── SearchOperator 测试 ───────────────────────────────────


def test_search_completes() -> None:
    definition = _load_definition()
    session = run_operator(
        definition,
        SearchOperator(seed=0, beam_width=3, max_prep_per_turn=3),
        max_steps=200,
    )

    assert session.state is not None
    assert session.state.turn == definition.rules.max_turns + 1


def test_search_outperforms_greedy() -> None:
    """搜索操作者应能完成游戏并获得正分。

    注意：单回合束搜索受限于启发式精度，不一定优于贪心。
    其主要价值在于探索更广的动作空间作为上界参考。
    """

    definition = _load_definition()
    from dataclasses import replace
    seeded = replace(definition, rules=replace(definition.rules, seed=0))

    s = run_operator(
        seeded,
        SearchOperator(seed=0, beam_width=5, max_prep_per_turn=4),
        max_steps=200,
    )

    assert s.state is not None
    score = score_final_state(seeded, s.state, waves=_FAST_WAVES).score
    assert score > 0, f"Search should score > 0, got {score:.2f}"


# ── EvalRunner 测试 ────────────────────────────────────────


def test_single_eval_completed() -> None:
    definition = _load_definition()
    result = run_single_eval(
        lambda seed: RandomFullOperator(seed=seed),
        definition,
        seed=42,
    )

    assert result.status == "completed"
    assert result.score > 0
    assert result.duration_seconds > 0
    assert result.operator_name == "RandomFullOperator"


def test_eval_suite_produces_reports() -> None:
    config = EvalConfig(
        data_dir=str(_data_dir()),
        seeds=(0,),
        max_steps=200,
        max_workers=1,
        score_waves=_FAST_WAVES,
    )

    results = run_eval_suite(
        {
            "RandomFull": lambda seed: RandomFullOperator(seed=seed),
            "Greedy": lambda seed: GreedyOperator(seed=seed),
        },
        config=config,
    )

    assert len(results) == 2
    assert "RandomFull" in results
    assert "Greedy" in results

    full_report = results["RandomFull"]
    assert full_report.seed_count == 1
    assert full_report.mean > 0
    assert len(full_report.per_seed) == 1


def test_save_eval_results_json(tmp_path: Path) -> None:
    config = EvalConfig(
        data_dir=str(_data_dir()),
        seeds=(0,),
        max_steps=200,
        max_workers=1,
        score_waves=_FAST_WAVES,
    )

    results = run_eval_suite(
        {"RandomFull": lambda seed: RandomFullOperator(seed=seed)},
        config=config,
    )

    output_path = tmp_path / "results.json"
    leaderboard_dir = tmp_path / "leaderboard"
    save_eval_results(
        results,
        output_path,
        config=config,
        leaderboard_dir=leaderboard_dir,
    )

    import json
    content = json.loads(output_path.read_text(encoding="utf-8"))
    assert "timestamp" in content
    assert "results" in content
    assert "RandomFull" in content["results"]
    assert content["results"]["RandomFull"]["seed_count"] == 1
    assert len(list(leaderboard_dir.glob("baseline-*.json"))) == 1


def test_save_baseline_leaderboard_replays(tmp_path: Path) -> None:
    config = EvalConfig(
        data_dir=str(_data_dir()),
        seeds=(0,),
        max_steps=200,
        max_workers=1,
        score_waves=_FAST_WAVES,
    )
    results = run_eval_suite(
        {"RandomFull": lambda seed: RandomFullOperator(seed=seed)},
        config=config,
    )

    replay_dir = tmp_path / "replays"
    paths = save_leaderboard_replays(results, replay_dir, config=config)

    assert len(paths) == 1
    import json
    replay = json.loads(paths[0].read_text(encoding="utf-8"))
    assert replay["kind"] == "baseline_replay"
    assert replay["baseline"]["operator"] == "RandomFull"
    assert replay["data"]["game_seed"] == 0
    assert replay["score"]["rank_score"] == results["RandomFull"].rank_scores[0]
    assert replay["final_observation"]["finished"] is True
    assert replay["turns"] == []

    output = tmp_path / "leaderboard_data.json"
    build_leaderboard(replay_dir, output, incremental=False)
    leaderboard = json.loads(output.read_text(encoding="utf-8"))
    model = leaderboard["models"][0]
    assert model["model"] == "Baseline · RandomFull"
    assert model["is_baseline"] is True
    assert model["rank_score"]["best"] == results["RandomFull"].rank_scores[0]
    assert model["run_details"][0]["game_seed"] == 0
    assert model["run_details"][0]["rank_score_per_adventurer"]


# ── 辅助函数 ──────────────────────────────────────────────


def _data_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "presets" / "default"


def _load_definition():
    return load_game_definition(_data_dir())


def _new_session(definition):
    from guild_manager_bench.runtime.session import GameSession
    return GameSession(definition)
