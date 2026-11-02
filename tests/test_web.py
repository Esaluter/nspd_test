import asyncio

from httpx import ASGITransport, AsyncClient

from main import create_app
from nspd_test.diagnostics import DiagnosticStore
from nspd_test.models import ProbeResult


class Searcher:
    def search(self, query, **kwargs):
        return [{"cadastral_number":"77:01:1:1","address":"Москва, ул. Тверская, д. 1","category_name":"Здание","cadastral_quarter":"77:01:1"}]


def run(scenario, tmp_path):
    async def execute():
        app=create_app(password="correct-password",searcher=Searcher(),store=DiagnosticStore(tmp_path))
        async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as client:
            return await scenario(client)
    return asyncio.run(execute())


async def login(client):
    response=await client.post("/api/login",data={"password":"correct-password"})
    assert response.status_code==200
    return response.json()["csrf"]


def test_api_is_protected(tmp_path):
    async def scenario(client):
        assert (await client.get("/api/status")).status_code==401
        assert (await client.post("/api/search",json={"address":"Москва, ул. Тверская, д. 1"})).status_code==401
    run(scenario,tmp_path)


def test_login_csrf_and_search(tmp_path):
    async def scenario(client):
        csrf=await login(client)
        assert (await client.get("/api/status")).status_code==200
        assert (await client.post("/api/search",json={"address":"Москва, ул. Тверская, д. 1"})).status_code==403
        response=await client.post("/api/search",headers={"X-CSRF-Token":csrf},json={"address":"Москва, ул. Тверская, д. 1"})
        assert response.status_code==200 and response.json()["status"]=="НАЙДЕНО"
    run(scenario,tmp_path)


def test_login_rate_limit(tmp_path):
    async def scenario(client):
        for _ in range(5):
            assert (await client.post("/api/login",data={"password":"wrong"})).status_code==401
        assert (await client.post("/api/login",data={"password":"wrong"})).status_code==429
    run(scenario,tmp_path)


def test_network_diagnostics_endpoint_is_protected_and_mocked(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "main.run_network_diagnostics",
        lambda store: [ProbeResult("DNS", "УСПЕШНО", 1, "127.0.0.1")],
    )
    async def scenario(client):
        assert (await client.post("/api/diagnostics/network")).status_code == 401
        csrf = await login(client)
        response = await client.post(
            "/api/diagnostics/network", headers={"X-CSRF-Token": csrf}
        )
        assert response.status_code == 200
        assert response.json()["stages"][0]["stage"] == "DNS"
    run(scenario, tmp_path)
