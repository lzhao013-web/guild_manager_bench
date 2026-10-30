"""存储失效时不能半提交付费任务，也不能留下失去监管的进程。"""
import asyncio
import errno
import json
import os
from pathlib import Path

import pytest

from guild_manager_bench.api import workbench as module
from guild_manager_bench.api.workbench_models import SchedulerSettings
from test_workbench import batch, manager, model_server, wait_for


def test_atomic_state_write_retries_preserving_previous_state_and_cleans_temporary_files(tmp_path, monkeypatch):
    path = tmp_path / 'state.json'
    module.write_json(path, {'status': 'previous'})
    original_replace = Path.replace
    failures = []

    def replace(source, target):
        if target == path and len(failures) < 2:
            assert json.loads(path.read_text(encoding='utf-8')) == {'status': 'previous'}
            failures.append(source)
            raise PermissionError('temporary replacement conflict')
        return original_replace(source, target)

    monkeypatch.setattr(Path, 'replace', replace)
    monkeypatch.setattr(module, 'IO_RETRY_DELAYS', (0, 0))
    module.write_json(path, {'status': 'updated'})
    assert len(failures) == 2
    assert json.loads(path.read_text(encoding='utf-8')) == {'status': 'updated'}
    assert list(tmp_path.glob('*.tmp')) == []


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


@pytest.mark.parametrize('error_type', [OSError, PermissionError])
def test_storage_failure_stops_worker_then_allows_explicit_resume_without_service_restart(tmp_path, monkeypatch, error_type):
    with model_server() as (url, release, calls):
        async def scenario():
            m = manager(tmp_path, stop_timeout=.5)
            await m.start()
            original_write = module._write_json_once
            try:
                old = m.create_batch(batch(url, 'slow-storage-test'))['jobs'][0]
                await wait_for(lambda: bool(calls) and old['archive_id'])
                process = m._processes[old['id']]

                def fail(path, data):
                    code = errno.EACCES if error_type is PermissionError else errno.ENOSPC
                    raise error_type(code, 'simulated storage failure', str(path))

                monkeypatch.setattr(module, '_write_json_once', fail)
                await wait_for(lambda: old['status'] == 'interrupted')
                assert 'state.json' in m.snapshot()['scheduler_error']
                assert '错误码' in m.snapshot()['scheduler_error']
                assert process.poll() is not None
                assert not m._processes
                with pytest.raises(ValueError, match='自动重试'):
                    m.create_batch(batch(url, 'must-not-start'))
                assert calls == ['slow-storage-test']

                monkeypatch.setattr(module, '_write_json_once', original_write)
                await wait_for(lambda: m.failure is None)
                stored = json.loads((m.root / old['id'] / 'state.json').read_text(encoding='utf-8'))
                assert stored['status'] == 'interrupted'
                assert not m._pump.done()
                assert calls == ['slow-storage-test']
                request = batch(url, 'explicit-resume')
                request.runs[0].resume_run_id = old['archive_id']
                resumed = m.create_batch(request)['jobs'][0]
                release.set()
                await wait_for(lambda: resumed['status'] == 'completed')
                assert resumed['archive_id'] == old['archive_id']
                assert old['status'] == 'interrupted'
                assert calls.count('slow-storage-test') == 1
                assert calls.count('explicit-resume') == 8
            finally:
                monkeypatch.setattr(module, '_write_json_once', original_write)
                release.set()
                await m.close()
        asyncio.run(scenario())


def test_close_during_storage_failure_still_reaps_worker_and_releases_owner(tmp_path, monkeypatch):
    with model_server() as (url, release, calls):
        async def scenario():
            m = manager(tmp_path, stop_timeout=.1)
            await m.start()
            original_write = module._write_json_once
            try:
                job = m.create_batch(batch(url, 'slow-close-storage'))['jobs'][0]
                await wait_for(lambda: bool(calls))
                process = m._processes[job['id']]

                def fail(*args):
                    raise OSError('simulated storage failure during close')

                monkeypatch.setattr(module, '_write_json_once', fail)
                await m.close()
                assert process.poll() is not None
                assert not m._processes
                assert m._ownership is None
                monkeypatch.setattr(module, '_write_json_once', original_write)
                restored = manager(tmp_path)
                await restored.start()
                try:
                    assert restored.get(job['id'])['status'] == 'interrupted'
                    assert calls == ['slow-close-storage']
                finally:
                    await restored.close()
            finally:
                monkeypatch.setattr(module, '_write_json_once', original_write)
                release.set()
                if m._ownership is not None:
                    await m.close()
        asyncio.run(scenario())


@pytest.mark.skipif(os.name != 'nt', reason='验证 Windows 真实读取句柄阻止原子替换的行为')
def test_windows_reader_contention_retries_without_stopping_or_relaunching_worker(tmp_path, monkeypatch):
    with model_server() as (url, release, calls):
        async def scenario():
            m = manager(tmp_path, stop_timeout=.5)
            await m.start()
            reader = None
            try:
                job = m.create_batch(batch(url, 'slow-file-contention'))['jobs'][0]
                await wait_for(lambda: bool(calls))
                process = m._processes[job['id']]
                path = m.root / job['id'] / 'state.json'
                reader = path.open(encoding='utf-8')
                previous = json.load(reader)
                original_replace = Path.replace
                conflicts = []

                def replace(source, target):
                    try:
                        return original_replace(source, target)
                    except PermissionError:
                        if target == path:
                            conflicts.append(target)
                        raise

                monkeypatch.setattr(Path, 'replace', replace)
                # 主事件循环必须能在重试等待期间继续运行，才能释放真实读取句柄。
                await wait_for(lambda: bool(conflicts))
                assert m.failure is None
                assert job['status'] == 'running'
                assert json.loads(path.read_text(encoding='utf-8')) == previous
                assert list(path.parent.glob('*.tmp')) == []
                reader.close()
                reader = None
                release.set()
                await wait_for(lambda: job['status'] == 'completed')
                assert process.returncode == 0
                assert m.failure is None
                assert calls == ['slow-file-contention'] * 8
                assert job['usage']['input_tokens'] == 17 * 8
                assert json.loads(path.read_text(encoding='utf-8'))['status'] == 'completed'
            finally:
                if reader is not None:
                    reader.close()
                release.set()
                await m.close()
        asyncio.run(scenario())


def test_event_read_contention_retries_without_duplicate_usage_or_requests(tmp_path, monkeypatch):
    with model_server() as (url, release, calls):
        async def scenario():
            m = manager(tmp_path, stop_timeout=.5)
            await m.start()
            original_read = module.read_events
            try:
                job = m.create_batch(batch(url, 'slow-event-contention'))['jobs'][0]
                await wait_for(lambda: bool(calls))
                failures = []

                def read(path, *args):
                    if path.parent.name == job['id'] and len(failures) < 2:
                        failures.append(path)
                        raise PermissionError('temporary event lock')
                    return original_read(path, *args)

                monkeypatch.setattr(module, 'read_events', read)
                await wait_for(lambda: len(failures) == 2)
                assert m.failure is None
                release.set()
                await wait_for(lambda: job['status'] == 'completed')
                assert calls == ['slow-event-contention'] * 8
                assert job['usage']['input_tokens'] == 17 * 8
                assert job['usage']['output_tokens'] == 5 * 8
            finally:
                monkeypatch.setattr(module, 'read_events', original_read)
                release.set()
                await m.close()
        asyncio.run(scenario())


@pytest.mark.parametrize('delete', [False, True])
def test_cancel_during_starting_write_retry_never_launches_worker(tmp_path, monkeypatch, delete):
    async def scenario():
        m = manager(tmp_path)
        launch_done = asyncio.Event()
        original_launch = m._launch

        async def launch(job):
            try:
                return await original_launch(job)
            finally:
                launch_done.set()

        monkeypatch.setattr(m, '_launch', launch)
        await m.start()
        original_write = module._write_json_once
        try:
            job = m.create_batch(batch('http://127.0.0.1:1/v1', 'never-launch'))['jobs'][0]
            failures = []

            def write(path, data):
                if data.get('id') == job['id'] and data.get('status') == 'starting':
                    failures.append(path)
                    raise PermissionError('temporary starting state lock')
                return original_write(path, data)

            monkeypatch.setattr(module, '_write_json_once', write)
            await wait_for(lambda: bool(failures))
            m.stop(job['id'])
            if delete:
                m.delete_job(job['id'])
            await wait_for(launch_done.is_set)
            assert job['status'] == 'interrupted'
            assert not m._processes
            assert job['id'] not in m._payloads
            assert m.failure is None
            assert not (m.root / job['id'] / 'stderr.log').exists()
            if delete:
                assert job['id'] not in m.jobs
                assert not (m.root / job['id']).exists()
            else:
                stored = json.loads((m.root / job['id'] / 'state.json').read_text(encoding='utf-8'))
                assert stored['status'] == 'interrupted'
        finally:
            monkeypatch.setattr(module, '_write_json_once', original_write)
            await m.close()
    asyncio.run(scenario())
