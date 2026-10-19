"""存储失效时不能半提交付费任务，也不能留下失去监管的进程。"""
import asyncio

import pytest

from guild_manager_bench.api import workbench as module
from guild_manager_bench.api.workbench_models import SchedulerSettings
from test_workbench import batch, manager, model_server, wait_for


def test_failed_settings_write_does_not_apply_unacknowledged_settings(tmp_path, monkeypatch):
    async def scenario():
        m = manager(tmp_path)
        await m.start()
        original = m.settings

        def fail(*args):
            raise OSError('simulated disk full')

        monkeypatch.setattr(module, 'write_json', fail)
        try:
            with pytest.raises(OSError):
                m.configure(SchedulerSettings(max_parallel=8, per_endpoint=8))
            assert m.settings == original
        finally:
            await m.close()
    asyncio.run(scenario())


def test_partial_batch_write_never_publishes_paid_work(tmp_path, monkeypatch):
    async def scenario():
        m = manager(tmp_path)
        await m.start()
        original_write = module.write_json
        writes = 0

        def fail_second(*args):
            nonlocal writes
            writes += 1
            if writes == 2:
                raise OSError('simulated disk full')
            original_write(*args)

        monkeypatch.setattr(module, 'write_json', fail_second)
        try:
            with pytest.raises(OSError):
                m.create_batch(batch('http://127.0.0.1:1/v1', 'first', 'second'))
            assert m.snapshot()['jobs'] == []
        finally:
            await m.close()
    asyncio.run(scenario())


def test_storage_failure_stops_actual_worker_and_exposes_scheduler_failure(tmp_path, monkeypatch):
    with model_server() as (url, release, calls):
        async def scenario():
            m = manager(tmp_path, stop_timeout=.5)
            await m.start()
            m.create_batch(batch(url, 'slow-storage-test'))
            await wait_for(lambda: bool(calls))

            def fail(*args):
                raise OSError('simulated disk full')

            monkeypatch.setattr(module, 'write_json', fail)
            await wait_for(lambda: m.snapshot()['jobs'][0]['status'] == 'interrupted')
            assert m.snapshot()['scheduler_error']
            assert m.snapshot()['jobs'][0]['status'] == 'interrupted'
            release.set()
            with pytest.raises(OSError, match='simulated disk full'):
                await m.close()
        asyncio.run(scenario())
