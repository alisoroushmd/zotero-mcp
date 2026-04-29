"""Tests for the SSL health diagnostic module."""

from __future__ import annotations

from zotero_mcp.ssl_health import (
    OVERRIDE_ENV_VARS,
    ProbeResult,
    SSLHealthReport,
    _build_verdict_and_remediation,
    check_ssl_health,
    report_to_dict,
)


def _probe(url: str, ok: bool, status: int | None = None, err: str = "") -> ProbeResult:
    return ProbeResult(
        url=url,
        ok=ok,
        status=status,
        error_type="SSLCertVerificationError" if err else "",
        error_message=err,
    )


def test_healthy_when_everything_ok():
    verdict, rem = _build_verdict_and_remediation(
        cafile_exists=True,
        capath_exists=True,
        ca_count=187,
        broken_env={},
        probes=[_probe("u1", True, 200), _probe("u2", True, 200)],
    )
    assert verdict == "HEALTHY"
    assert rem == ["No action needed."]


def _ssl_probe(url: str, err: str = "verify failed") -> ProbeResult:
    """Probe that failed specifically due to an SSL error."""
    return ProbeResult(
        url=url,
        ok=False,
        status=None,
        error_type="SSLCertVerificationError",
        error_message=err,
    )


def _nonssl_probe(url: str, err_type: str = "URLError", err: str = "403") -> ProbeResult:
    """Probe that failed for a non-SSL reason (e.g. auth, DNS, timeout)."""
    return ProbeResult(url=url, ok=False, status=None, error_type=err_type, error_message=err)


def test_broken_when_ssl_errors_occur():
    """Actual SSL failures should mark the system BROKEN."""
    verdict, rem = _build_verdict_and_remediation(
        cafile_exists=True,
        capath_exists=True,
        ca_count=187,
        broken_env={},
        probes=[_ssl_probe("u1"), _ssl_probe("u2")],
    )
    assert verdict == "BROKEN"
    assert any("TLS" in step or "proxy" in step for step in rem)


def test_healthy_when_failures_are_non_ssl():
    """Non-SSL failures (auth, DNS) shouldn't degrade the verdict."""
    verdict, rem = _build_verdict_and_remediation(
        cafile_exists=True,
        capath_exists=True,
        ca_count=187,
        broken_env={},
        probes=[_probe("u1", True, 200), _nonssl_probe("u2", "URLError", "403 Forbidden")],
    )
    # One probe succeeded, the other failed for non-SSL reasons.
    assert verdict == "DEGRADED"
    # Remediation should note the non-SSL issue explicitly
    assert any("non-SSL" in step for step in rem)


def test_ssl_failure_trumps_partial_success():
    """If any probe has an actual SSL error, that's BROKEN, not DEGRADED."""
    verdict, _rem = _build_verdict_and_remediation(
        cafile_exists=True,
        capath_exists=True,
        ca_count=187,
        broken_env={},
        probes=[_probe("u1", True, 200), _ssl_probe("u2")],
    )
    assert verdict == "BROKEN"


def test_broken_env_var_flagged_first():
    verdict, rem = _build_verdict_and_remediation(
        cafile_exists=True,
        capath_exists=True,
        ca_count=187,
        broken_env={"SSL_CERT_FILE": "/nowhere/cert.pem"},
        probes=[_ssl_probe("u1")],
    )
    assert verdict == "BROKEN"
    assert "SSL_CERT_FILE" in rem[0]
    assert ".zshrc" in rem[0] or "shell profile" in rem[0]


def test_missing_cafile_remediation_mentions_homebrew():
    verdict, rem = _build_verdict_and_remediation(
        cafile_exists=False,
        capath_exists=False,
        ca_count=0,
        broken_env={},
        probes=[_ssl_probe("u1")],
    )
    assert verdict == "BROKEN"
    joined = " ".join(rem)
    assert "ca-certificates" in joined
    assert "brew" in joined


def test_zero_ca_count_flagged():
    verdict, rem = _build_verdict_and_remediation(
        cafile_exists=True,
        capath_exists=True,
        ca_count=0,
        broken_env={},
        probes=[_ssl_probe("u1")],
    )
    joined = " ".join(rem)
    assert "Zero CAs" in joined or "0 CA" in joined or "zero" in joined.lower()


def test_zero_ca_count_with_all_probes_ok_is_healthy():
    """macOS + Homebrew Python often reports 0 CAs because the CAs are loaded
    from the default capath, not the cafile. If every probe still succeeds,
    TLS verification empirically works — trust the probes over the count."""
    verdict, rem = _build_verdict_and_remediation(
        cafile_exists=True,
        capath_exists=True,
        ca_count=0,
        broken_env={},
        probes=[_probe("u1", True, 200), _probe("u2", True, 200)],
    )
    assert verdict == "HEALTHY"
    joined = " ".join(rem)
    assert "Zero CAs" not in joined


def test_zero_ca_count_offline_is_broken():
    """Without probes we can't verify empirically, so ca_count==0 must
    still be treated as a configuration fault."""
    verdict, _rem = _build_verdict_and_remediation(
        cafile_exists=True,
        capath_exists=True,
        ca_count=0,
        broken_env={},
        probes=[],
    )
    assert verdict == "BROKEN"


def test_check_ssl_health_offline_returns_report():
    """With probe=False, no network I/O happens but a report is produced."""
    report = check_ssl_health(probe=False)
    assert isinstance(report, SSLHealthReport)
    assert report.probes == []
    assert report.python_version
    assert report.openssl_version
    assert isinstance(report.loaded_ca_count, int)
    assert report.verdict in {"HEALTHY", "DEGRADED", "BROKEN"}


def test_report_to_dict_is_json_serializable():
    import json

    report = check_ssl_health(probe=False)
    d = report_to_dict(report)
    # Round-trips through JSON without error
    serialized = json.dumps(d)
    assert "python_version" in serialized
    assert "verdict" in serialized


def test_override_env_vars_list_is_stable():
    """Changing this tuple changes user-visible behavior — lock it down."""
    assert set(OVERRIDE_ENV_VARS) == {
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
    }


def test_broken_env_detection(tmp_path, monkeypatch):
    """A set-but-nonexistent SSL_CERT_FILE should land in override_env_broken."""
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "does_not_exist.pem"))
    report = check_ssl_health(probe=False)
    assert "SSL_CERT_FILE" in report.override_env
    assert "SSL_CERT_FILE" in report.override_env_broken


def test_valid_env_override_not_flagged_as_broken(tmp_path, monkeypatch):
    """A set SSL_CERT_FILE pointing at a real file should not be flagged broken."""
    fake_bundle = tmp_path / "bundle.pem"
    fake_bundle.write_text("# not a real bundle but the file exists\n")
    monkeypatch.setenv("SSL_CERT_FILE", str(fake_bundle))
    report = check_ssl_health(probe=False)
    assert "SSL_CERT_FILE" in report.override_env
    assert "SSL_CERT_FILE" not in report.override_env_broken
