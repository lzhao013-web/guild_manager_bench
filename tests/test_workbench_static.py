import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from guild_manager_bench.api.static_files import WorkbenchFiles


@pytest.mark.parametrize(("path", "body", "policy"), [
    ("index.html", "<h1>工作台</h1>", "no-cache"),
    ("assets/index-Ab12_-cd.js", "export const ready = true;", "public, max-age=31536000, immutable"),
    ("assets/watch-Bc23_-de.css", "body { color: teal; }", "public, max-age=31536000, immutable"),
    ("assets/portrait.png", "image-fixture", "no-cache"),
    ("fonts/misans/MiSans-normal.woff2", "font-fixture", "no-cache"),
])
def test_workbench_cache_policy_and_conditional_requests(tmp_path, path, body, policy):
    file = tmp_path / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(body, encoding="utf-8")
    app = FastAPI()
    app.mount("/workbench", WorkbenchFiles(directory=tmp_path, html=True))
    with TestClient(app) as client:
        first = client.get(f"/workbench/{path}")
        assert first.text == body
        assert first.headers["cache-control"] == policy
        unchanged = client.get(f"/workbench/{path}", headers={"If-None-Match": first.headers["etag"]})
        assert unchanged.status_code == 304
        assert unchanged.content == b""
        assert unchanged.headers["cache-control"] == policy
        if policy == "no-cache":
            file.write_text(body + " updated", encoding="utf-8")
            updated = client.get(f"/workbench/{path}", headers={"If-None-Match": first.headers["etag"]})
            assert updated.status_code == 200
            assert updated.text == body + " updated"
        missing = client.get("/workbench/assets/missing-Ab12_-cd.js")
        assert missing.status_code == 404
        assert "immutable" not in missing.headers.get("cache-control", "")
