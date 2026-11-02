import socket
from pathlib import Path

from nspd_test import diagnostics
from nspd_test.diagnostics import DiagnosticStore


def test_dns_failure_skips_dependent_probes(monkeypatch, tmp_path):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: (_ for _ in ()).throw(socket.gaierror()))
    result=diagnostics.run_network_diagnostics(DiagnosticStore(tmp_path))
    assert [x.status for x in result]==["ОШИБКА","ПРОПУЩЕНО","ПРОПУЩЕНО","ПРОПУЩЕНО","ПРОПУЩЕНО"]


def test_store_redacts_secrets_and_blocks_nothing_needed(tmp_path):
    store=DiagnosticStore(tmp_path);store.event("TEST","OK",details={"token":"secret","value":"safe"})
    text=(tmp_path/"events.jsonl").read_text(encoding="utf-8")
    assert "secret" not in text and "[REDACTED]" in text

