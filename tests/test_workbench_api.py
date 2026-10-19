import asyncio
from contextlib import asynccontextmanager
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from guild_manager_bench.api.routes_workbench import workbench_router
from guild_manager_bench.api.workbench_models import CreateBatch, RunSpec
from test_workbench import manager, model_server, wait_for


def test_api_is_same_origin_and_reconnecting_clients_do_not_own_jobs(tmp_path):
    m = manager(tmp_path, stop_timeout=.5)

    @asynccontextmanager
    async def lifespan(app):
        await m.start()
        try:
            yield
        finally:
            await m.close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(workbench_router(m))
    with model_server() as (url, release, calls), TestClient(app) as client:
        request = {"name": "API 构造测试", "runs": [{"preset": "default", "provider": "openai", "model": "slow-api", "base_url": url, "api_key": "api-only-secret"}]}
        assert client.post('/api/workbench/batches', json=request, headers={"Origin": "https://other.example"}).status_code == 403
        assert not m.jobs
        response = client.post('/api/workbench/batches', json=request)
        assert response.status_code == 201
        assert 'api-only-secret' not in response.text
        job_id = response.json()['jobs'][0]['id']
        # 两个独立的观看客户端都可以离开，不改变服务管理的任务。
        second = TestClient(app)
        assert second.get('/api/workbench').json()['jobs'][0]['id'] == job_id
        second.close()
        assert client.get('/api/workbench').json()['jobs'][0]['status'] in {'queued', 'running'}
        assert client.get(f'/api/workbench/jobs/{job_id}/events?after=-1').status_code == 422
        assert client.get('/api/workbench/jobs/missing/events').status_code == 404
        assert client.post('/api/workbench/jobs/missing/stop').status_code == 404
        release.set()
        response = client.post(f'/api/workbench/jobs/{job_id}/stop')
        assert response.status_code == 200


def test_directory_has_only_one_scheduler_owner(tmp_path):
    async def scenario():
        first, second = manager(tmp_path), manager(tmp_path)
        await first.start()
        try:
            try:
                await second.start()
            except RuntimeError as error:
                assert '其他服务' in str(error)
            else:
                raise AssertionError('不应允许第二个服务接管相同运行目录')
        finally:
            await first.close()
        await second.start()
        await second.close()
    asyncio.run(scenario())


def test_home_preserves_manual_and_legacy_routes(tmp_path, monkeypatch):
    from pathlib import Path
    from guild_manager_bench.api.app import create_app

    data = Path(__file__).resolve().parents[1] / 'data'
    monkeypatch.chdir(tmp_path)
    with TestClient(create_app(data)) as client:
        home = client.get('/')
        assert home.status_code == 200
        assert '/workbench/assets/' in home.text
        assert 'llmStartButton' in client.get('/?mode=manual').text
        assert 'llmStartButton' in client.get('/legacy/').text
        assert client.get('/replay/compare.html').status_code == 200
        assert client.get('/leaderboard/').status_code == 200
        assert client.get('/app.js').status_code == 200
