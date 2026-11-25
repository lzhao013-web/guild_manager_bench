from copy import deepcopy
from html.parser import HTMLParser
from base64 import b64decode
from pathlib import Path
import json
import re

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from guild_manager_bench.api.routes_llm_archive import llm_archive_router
from guild_manager_bench.api.run_report import render_run_report


class Document(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.tags = []
        self.parts = []
        self.sections = {}
        self.section = None
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        if tag == "section":
            self.section = attrs.get("id")
            if self.section:
                self.sections[self.section] = []

    def handle_endtag(self, tag):
        if tag == "section":
            self.section = None

    def handle_data(self, data):
        self.parts.append(data)
        if self.section:
            self.sections[self.section].append(data)

    @property
    def text(self):
        return " ".join(self.parts)


def report_replay():
    hero = {"adventurer_id": "hero", "name": "测试骑士", "level": 8,
            "effective_stats": {"hp": 120, "mp": 20, "attack": 30, "defense": 16, "speed": 7},
            "equipment_slots": [{"slot": "main_hand", "item": {"name": "钢剑"}},
                                {"slot": "off_hand", "item": None, "blocked_by": None}],
            "skills": [{"skill_id": "guard", "name": "防御姿态"}],
            "tactics": {"skill_priority": ["guard"], "disabled_skills": []}}
    before = {"max_turns": 2, "adventurers": [hero], "monsters": [
        {"monster_id": "boss", "name": "山林首领", "tier": "boss"}],
        "global_upgrades": [{"upgrade_id": "roster_expansion_1", "unlocked": False}]}
    after = deepcopy(before)
    after["adventurers"][0]["level"] = 9
    after["global_upgrades"][0]["unlocked"] = True
    first = {"turn": 1, "status": "completed", "observation_before": before, "steps": [
        {"type": "assistant", "content": "PRIVATE-ANSWER", "reasoning_content": "PRIVATE-THOUGHT",
         "usage": {"input_tokens": 100, "output_tokens": 20}, "timing": {"duration_ms": 1000}},
        {"type": "tool_result", "name": "execute_plan", "result": {"ok": True}, "observation_after": after},
        {"type": "tool_result", "name": "end_turn", "result": {"ok": True, "turn_result": {"battles": [
            {"adventurer_id": "hero", "adventurer_name": "测试骑士", "monster_id": "boss", "won": True}]}},
         "observation_after": after},
    ]}
    failed = {"turn": 2, "status": "failed", "steps": [
        {"type": "assistant", "usage": {"prompt_tokens": 5}, "timing": {"duration_ms": 500}},
        {"type": "tool_result", "name": "purchase_upgrade", "arguments": {"upgrade_id": "roster_expansion_2"}, "result": {"ok": True}},
    ]}
    second = {"turn": 2, "status": "completed", "observation_before": after, "steps": [
        {"type": "assistant", "content": "PRIVATE-ANSWER"},
        {"type": "tool_result", "name": "preview_team_power", "result": {"ok": True, "simulation_only": True},
         "observation_after": {"global_upgrades": [{"upgrade_id": "roster_expansion_2", "unlocked": True}]}},
    ]}
    return {"kind": "llm_replay", "schema_version": 1, "status": "completed",
            "created_at": "2026-11-03T12:00:00", "updated_at": "2026-11-03T13:00:00",
            "agent": {"config": {"model": "report-model", "reasoning_effort": "high",
                                   "api_key": "PRIVATE-KEY", "base_url": "https://private-gateway.test"}},
            "config": {"game_seed": 42, "scoring_seed": 123, "objective": "PRIVATE-OBJECTIVE"},
            "data": {"preset": "advanced", "benchmark_version": "dev", "protocol_status": "mixed",
                     "data_dir": "D:/PRIVATE-PATH", "protocol_hash": "test-protocol"},
            "turns": [first, failed, {**failed, "status": "rolled_back", "steps": failed["steps"][1:]}, second], "final_observation": {**after, "finished": True, "gold": 0, "experience_pool": 10},
            "score": {"rank_score": 8123.4567, "score": 98.7654321,
                      "rank_score_per_adventurer": [{"adventurer_id": "hero", "rank_score": 8123.4567}]},
            "stats": {"token_usage": {"input_tokens": 999999, "output_tokens": 888888}},
            "memo": "PRIVATE-MEMO", "trace": "PRIVATE-TRACE", "unknown": "PRIVATE-UNKNOWN"}


@pytest.fixture
def archive(tmp_path):
    path = tmp_path / "run" / "replay.json"
    path.parent.mkdir()
    payload = report_replay()
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    app = FastAPI()
    app.include_router(llm_archive_router(tmp_path))
    with TestClient(app) as client:
        yield path, payload, client


def save(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_download_is_offline_whitelisted_and_read_only(archive):
    path, _, client = archive
    before = path.read_bytes()
    response = client.get("/api/llm/runs/run/report")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert 'attachment; filename="guild-report.html"' == response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"
    doc = Document(response.text)
    for value in ("report-model", "8,123.4567", "测试骑士", "钢剑", "防御姿态", "跨版本", "正式 Rank Score"):
        assert value in doc.text
    for value in ("PRIVATE-", "private-gateway", "98.7654321", "999999", "888888"):
        assert value not in response.text
    assert not any(tag in {"script", "iframe", "object", "embed", "link", "img", "form"} for tag, _ in doc.tags)
    assert not any(key.startswith("on") or key in {"src", "srcdoc"} for _, attrs in doc.tags for key in attrs)
    assert all(value.startswith("#") for _, attrs in doc.tags for key, value in attrs.items() if key == "href")
    assert any(tag == "meta" and attrs.get("http-equiv") == "Content-Security-Policy" for tag, attrs in doc.tags)
    assert path.read_bytes() == before
    assert list(path.parent.iterdir()) == [path]


def test_embedded_art_is_small_deduplicated_and_ignores_archive_paths(archive):
    path, payload, client = archive
    member = payload['final_observation']['adventurers'][0]
    member['template_id'] = 'foot_knight'
    member['equipment_slots'][0]['item']['template_id'] = 't1_heavy_sword'
    member['equipment_slots'].append({'slot': 'boots', 'item': {'name': '未知装备仍有名称', 'template_id': '../../PRIVATE-PATH'}})
    save(path, payload)
    before = (path.stat().st_size, path.stat().st_mtime_ns, path.read_bytes())
    response = client.get('/api/llm/runs/run/report')
    assert response.status_code == 200
    images = re.findall(r'data:image/webp;base64,([A-Za-z0-9+/=]+)', response.text)
    assert len(images) == 2  # 同一人物图用于阵容总览与人物卡，只编码一次。
    assert all(b64decode(image).startswith(b'RIFF') for image in images)
    assert sum(len(b64decode(image)) for image in images) < 16 * 1024
    assert len(response.content) < 100 * 1024
    assert '未知装备仍有名称' in response.text and 'PRIVATE-PATH' not in response.text
    assert 'img-src data:' in response.text
    assert all(url.startswith('data:image/webp;base64,') for url in re.findall(r'url\("([^\"]+)"\)', response.text))
    assert (path.stat().st_size, path.stat().st_mtime_ns, path.read_bytes()) == before
    assert list(path.parent.iterdir()) == [path]


def test_art_budget_caps_encoded_content_without_dropping_member_or_equipment_text():
    payload = report_replay()
    assets = Path(__file__).parents[1] / 'src/guild_manager_bench/api/report_assets'
    portraits = sorted((assets / 'portraits').glob('*.webp'))
    equipment = sorted((assets / 'equipment').glob('*.webp'))
    members = []
    for index, portrait in enumerate(portraits):
        hero = deepcopy(payload['final_observation']['adventurers'][0])
        hero.update(adventurer_id=f'hero-{index}', name=f'成员{index}', template_id=portrait.stem)
        hero['equipment_slots'] = [
            {'slot': 'main_hand', 'item': {'name': f'展示装备{item.stem}', 'template_id': item.stem}}
            for item in equipment[index::len(portraits)]
        ]
        members.append(hero)
    payload['final_observation']['adventurers'] = members
    html = render_run_report(payload)
    images = re.findall(r'data:image/webp;base64,([A-Za-z0-9+/=]+)', html)
    assert 0 < len(images) < len(portraits) + len(equipment)
    assert sum(len(b64decode(image)) for image in images) <= 128 * 1024
    assert len(html.encode('utf-8')) < 350 * 1024
    assert all(f'成员{index}' in html for index in range(len(portraits)))
    assert all(f'展示装备{item.stem}' in html for item in equipment)


def test_rank_trend_does_not_bridge_missing_turns_or_substitute_deprecated_scores():
    payload = report_replay()
    payload['turns'] = [
        {'turn': 1, 'status': 'completed', 'rank_score': 0, 'steps': []},
        {'turn': 2, 'status': 'completed', 'score': 98.7654321, 'steps': []},
        {'turn': 3, 'status': 'completed', 'rank_score': 200, 'steps': []},
        {'turn': 4, 'status': 'completed', 'rank_score': 250, 'steps': []},
        {'turn': 5, 'status': 'rolled_back', 'rank_score': 987654321, 'steps': []},
        {'turn': 6, 'status': 'completed', 'rank_score': 300, 'steps': []},
    ]
    doc = Document(render_run_report(payload))
    assert len([tag for tag, _ in doc.tags if tag == 'circle']) == 4
    assert len([tag for tag, _ in doc.tags if tag == 'path']) == 1
    assert 'T1 Rank 0' in doc.text and 'T6 Rank 300' in doc.text
    assert '98.7654321' not in doc.text and '987654321' not in doc.text


def test_milestones_have_real_sources_not_failed_or_preview_operations(archive):
    _, _, client = archive
    doc = Document(client.get("/api/llm/runs/run/report").text)
    assert "解锁扩编 1" in doc.text
    assert "测试骑士达到 Lv9" in doc.text
    assert "山林首领" in doc.text
    assert "扩编 2" not in doc.text
    assert "记录 #2" in doc.text and "execute_plan" in doc.text
    assert "记录 #3" in doc.text and "end_turn" in doc.text
    assert "observation_after" in doc.text
    assert len([tag for tag, _ in doc.tags if tag == "details"]) >= 3


def test_next_opening_evidence_does_not_invent_an_action_location(archive):
    path, payload, client = archive
    for step in payload["turns"][0]["steps"]:
        step.pop("observation_after", None)
    save(path, payload)
    response = client.get("/api/llm/runs/run/report")
    assert response.status_code == 200
    text = Document(response.text).text
    assert "T2 开局快照 · observation_before" in text
    assert "具体行动位置未记录" in text
    assert "execute_plan" not in text


def test_usage_counts_recorded_responses_including_failed_attempts_once(archive):
    _, _, client = archive
    text = Document(client.get("/api/llm/runs/run/report").text).text
    assert "105" in text and "20" in text
    assert "1.5 秒" in text
    assert "输入 2/3" in text and "输出 1/3" in text and "耗时 2/3" in text


def test_unknown_rank_and_final_state_never_use_old_score_or_mid_turn_snapshot(archive):
    path, payload, client = archive
    payload["score"].pop("rank_score")
    payload["score"].pop("rank_score_per_adventurer")
    payload["final_observation"] = None
    save(path, payload)
    response = client.get("/api/llm/runs/run/report")
    assert response.status_code == 200
    doc = Document(response.text)
    assert "最终阵容未记录" in doc.text
    assert "未记录" in doc.text
    assert "98.7654321" not in doc.text
    assert "测试骑士" not in " ".join(doc.sections["party"])
    assert "测试骑士" in " ".join(doc.sections["milestones"])


def test_zero_rank_and_usage_remain_zero_and_dynamic_text_is_escaped(archive):
    path, payload, client = archive
    attack = '<script>alert("x")</script><img src="https://evil.test/x" onerror="alert(1)">'
    payload["agent"]["config"]["model"] = attack
    payload["final_observation"]["adventurers"][0]["name"] = attack
    payload["score"] = {"rank_score": 0, "score": 99}
    payload["turns"] = [{"turn": 1, "status": "completed", "steps": [
        {"type": "assistant", "usage": {"input_tokens": 0, "output_tokens": 0}, "timing": {"duration_ms": 0}}]}]
    save(path, payload)
    response = client.get("/api/llm/runs/run/report")
    doc = Document(response.text)
    assert attack in doc.text
    assert "&lt;script&gt;" in response.text
    assert '<strong id="rank-score">0</strong>' in response.text
    assert "0 秒" in doc.text and "输入 1/1" in doc.text
    assert not any(tag in {"script", "img"} for tag, _ in doc.tags)


def test_branch_and_resume_are_marked_without_exporting_source_paths(archive):
    path, payload, client = archive
    payload["resume_history"] = [{"operation": "rerun", "from_turn": 2, "source_run_id": "PRIVATE-SOURCE",
                                   "agent": {"config": {"base_url": "PRIVATE-CONNECTION"}}}]
    save(path, payload)
    response = client.get("/api/llm/runs/run/report")
    assert "从 T2 分支" in Document(response.text).text
    assert "继承" in Document(response.text).text
    assert "PRIVATE-" not in response.text


@pytest.mark.parametrize("status", ["running", "failed", "interrupted"])
def test_incomplete_runs_are_rejected_without_writing(archive, status):
    path, payload, client = archive
    payload["status"] = status
    save(path, payload)
    before = path.read_bytes()
    response = client.get("/api/llm/runs/run/report")
    assert response.status_code == 422
    assert "已完成" in response.json()["detail"]
    assert path.read_bytes() == before


def test_bad_or_missing_archives_have_actionable_errors(archive):
    path, _, client = archive
    assert client.get("/api/llm/runs/missing/report").status_code == 404
    assert client.get("/api/llm/runs/a%5Cb/report").status_code == 400
    for payload in ([], {"kind": "manual_replay", "status": "completed"},
                    {"kind": "llm_replay", "status": "completed", "turns": "broken"}):
        save(path, payload)
        assert client.get("/api/llm/runs/run/report").status_code == 422
    path.write_text('{"broken":', encoding="utf-8")
    assert client.get("/api/llm/runs/run/report").status_code == 422
