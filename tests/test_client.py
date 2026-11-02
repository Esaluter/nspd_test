import json
import ssl
import urllib.error

import pytest

from nspd_test import client


class Response:
    status = 200
    def __init__(self, payload): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *_): return None
    def read(self, limit): return self.payload[:limit]


def test_client_uses_verified_tls_and_exact_api(monkeypatch):
    captured = {}
    payload = json.dumps({"data":{"features":[{"properties":{"categoryName":"Здание","options":{"cad_num":"31:16:0101001:25","readable_address":"Белгород, ул. Садовая, д. 5"}}}]}}).encode()
    class Opener:
        def open(self, request, timeout): captured.update(url=request.full_url, timeout=timeout); return Response(payload)
    monkeypatch.setattr(client.urllib.request, "build_opener", lambda *_: Opener())
    context = ssl.create_default_context()
    result = client.VerifiedNspdHttpSearcher(context=context).search("test", region="", locality="", address="", timeout_seconds=4)
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert captured["url"].startswith(client.API_URL + "?")
    assert "thematicSearchId=1" in captured["url"]
    assert captured["timeout"] == 4
    assert result[0]["cadastral_quarter"] == ""


def test_http_error_is_not_hidden(monkeypatch):
    class Opener:
        def open(self, request, timeout): raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)
    monkeypatch.setattr(client.urllib.request, "build_opener", lambda *_: Opener())
    with pytest.raises(client.NspdHttpError) as caught:
        client.VerifiedNspdHttpSearcher(context=ssl.create_default_context()).search("x", region="", locality="", address="")
    assert caught.value.status == 403

