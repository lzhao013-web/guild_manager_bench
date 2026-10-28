import asyncio
from pathlib import Path

import pytest

from guild_manager_bench.api.workbench_events import apply_event, recovery_history
from guild_manager_bench.api.workbench_lock import FileLease
from guild_manager_bench.api.workbench_models import CreateBatch, RunSpec
from guild_manager_bench.game.presets import resolve_data_source
from test_workbench import batch, manager, model_server, wait_for


def test_failed_attempt_is_not_terminal_and_missing_usage_is_not_zero(tmp_path):
    async def scenario():
        m = manager(tmp_path)
        await m.start()
        try:
            job = m.create_batch(batch('http://127.0.0.1:1/v1', 'not-started'))['jobs'][0]
            apply_event(job, {'type': 'turn_failed', 'trace': {'failure_reason': 'empty_response'}}, '2026-10-18T00:00:00Z')
            assert job['status'] == 'queued'
            apply_event(job, {'type': 'model_response', 'usage': {}}, '2026-10-18T00:00:00Z')
            assert job['usage']['input_tokens'] is None
            assert job['usage']['output_tokens'] is None
            apply_event(job, {'type': 'model_response', 'usage': {'input_tokens': 0, 'output_tokens': 0}}, '2026-10-18T00:00:01Z')
            assert job['usage'] == {'input_tokens': 0, 'output_tokens': 0, 'reported': 1, 'responses': 2}
        finally:
            await m.close()
    asyncio.run(scenario())


def test_model_timing_accumulates_only_recorded_responses():
    job = {"usage": {"input_tokens": None, "output_tokens": None, "reported": 0, "responses": 0},
           "model_duration_ms": None}
    apply_event(job, {"type": "model_response", "usage": {}}, "2026-10-18T00:00:00Z")
    assert job["model_duration_ms"] is None
    apply_event(job, {"type": "model_response", "timing": {"duration_ms": 0}}, "2026-10-18T01:00:00Z")
    assert job["model_duration_ms"] == 0
    apply_event(job, {"type": "model_response", "timing": {"duration_ms": 125.5}}, "2026-10-18T02:00:00Z")
    apply_event(job, {"type": "model_response", "timing": {"duration_ms": 250}}, "2026-10-19T02:00:00Z")
    assert job["model_duration_ms"] == 375.5


def test_resume_replaces_inherited_metrics_then_counts_new_response_once():
    history = recovery_history([
        {"turn": 1, "status": "completed", "rank_score": 10, "steps": [
            {"type": "assistant", "timing": {"duration_ms": 1000},
             "usage": {"input_tokens": 100, "output_tokens": 20}},
            {"type": "tool_result", "timing": {"duration_ms": 999}},
        ]},
        {"turn": 2, "status": "failed", "steps": [
            {"type": "assistant", "timing": {"duration_ms": 250},
             "usage": {"prompt_tokens": 200, "completion_tokens": 30}},
        ]},
    ])
    job = {"usage": {"input_tokens": 99, "output_tokens": 9, "reported": 1, "responses": 1},
           "model_duration_ms": 9000, "calls": 0, "tools": 0}
    apply_event(job, {"type": "run_resumed", "archive": {"directory": "archive/run-a"},
                     "restored_observation": {"turn": 2, "max_turns": 8}, "stats": {},
                     "workbench_history": history}, "2026-10-20T00:00:00Z")
    assert job["model_duration_ms"] == 1250
    assert job["scores"] == [{"turn": 1, "value": 10}]
    apply_event(job, {"type": "model_response", "timing": {"duration_ms": 500},
                     "usage": {"input_tokens": 400, "output_tokens": 50}}, "2026-10-20T00:00:01Z")
    assert job["model_duration_ms"] == 1750
    assert job["usage"] == {"input_tokens": 700, "output_tokens": 100, "reported": 3, "responses": 3}


def test_stopped_run_can_resume_same_archive_after_worker_releases_lease(tmp_path):
    with model_server() as (url, release, calls):
        async def scenario():
            m = manager(tmp_path, stop_timeout=.5)
            await m.start()
            try:
                old = m.create_batch(batch(url, 'slow-initial'))['jobs'][0]
                await wait_for(lambda: old['archive_id'] is not None and old['phase'] == 'waiting_model')
                # 工作进程持有归档锁，即使其他工作台目录也不能并行写入。
                with pytest.raises(OSError):
                    with FileLease(m.archive_root / old['archive_id'] / 'workbench.lock'):
                        pass
                m.stop(old['id'])
                await wait_for(lambda: old['status'] == 'interrupted')
                release.set()
                resumed = m.create_batch(CreateBatch(name='显式续跑测试', runs=[RunSpec(preset='default', model='resumed-fast', base_url=url, api_key='secret-for-test', resume_run_id=old['archive_id'])]))['jobs'][0]
                await wait_for(lambda: resumed['status'] in {'completed', 'failed'})
                assert resumed['status'] == 'completed', resumed['error']
                assert resumed['archive_id'] == old['archive_id']
                assert resumed['turn'] == resumed['max_turns'] == 8
                assert len(resumed['scores']) == 8
                assert resumed['usage']['input_tokens'] == 17 * 8
                assert old['status'] == 'interrupted'
            finally:
                await m.close()
        asyncio.run(scenario())


def test_full_uses_frozen_protocol_without_modifying_frozen_sources(tmp_path):
    from guild_manager_bench.bench.protocols import verify_full_release
    with model_server() as (url, release, calls):
        async def scenario():
            m = manager(tmp_path)
            m.presets['full'] = resolve_data_source(Path(__file__).resolve().parents[1] / 'data', 'full')
            await m.start()
            try:
                job = m.create_batch(CreateBatch(name='冻结协议测试', runs=[RunSpec(preset='full', model='fast-frozen', base_url=url, api_key='secret-for-test')]))['jobs'][0]
                await wait_for(lambda: job['status'] in {'completed', 'failed'}, timeout=40)
                assert job['status'] == 'completed', job['error']
                assert job['data']['benchmark_version'] == 'v1'
                assert job['data']['protocol_status'] == 'frozen'
                assert job['turn'] == job['max_turns'] == 35
                verify_full_release()
            finally:
                await m.close()
        asyncio.run(scenario())
