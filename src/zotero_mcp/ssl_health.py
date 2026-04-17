"""Diagnose Python SSL/TLS certificate configuration.

macOS + Homebrew Python is notorious for silent certificate-verification
failures (``CERTIFICATE_VERIFY_FAILED``) when any of the following is true:

* Homebrew's ``ca-certificates`` is stale or missing.
* ``SSL_CERT_FILE`` / ``REQUESTS_CA_BUNDLE`` / ``CURL_CA_BUNDLE`` in the
  environment point at a nonexistent path (inherited from a prior install,
  conda, or an old ``.zshrc`` export).
* The venv was created against a since-removed cert bundle.
* ``certifi`` in the venv is older than the system ca-certificates.

This module returns a structured report that surfaces each of those failure
modes in one shot, along with a live HTTPS probe so the user can see whether
verification actually works right now.

The report is intentionally concrete: it names the file paths, shows which
env vars are set, counts how many CAs are loaded, and either confirms a 200
from a canonical endpoint or reports the exception. A non-developer can copy
the JSON into a forum post and get a useful answer; a developer can read it
and know immediately what to fix.
"""

from __future__ import annotations

import os
import platform
import ssl
import sys
from dataclasses import asdict, dataclass

# Canonical HTTPS endpoints used for the live probe. These are auth-free,
# stable, and served from separate CA chains (ISRG Root X1 via Let's Encrypt
# for pypi; DigiCert Global Root G2 for github), so a single-host TLS
# failure is distinguishable from a system-wide cert problem.
PROBE_URLS = (
    "https://pypi.org/simple/",
    "https://github.com/",
)

# Exception class names that indicate an actual TLS/cert failure. Anything
# else (auth errors, 4xx/5xx, DNS, timeout) is a non-SSL failure and should
# not degrade the health verdict.
SSL_ERROR_TYPES = frozenset(
    {
        "SSLError",
        "SSLCertVerificationError",
        "CertificateError",
        "SSLEOFError",
    }
)

# Environment variables that silently override Python's default cert resolution.
OVERRIDE_ENV_VARS = (
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
)


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of a single HTTPS verification attempt."""

    url: str
    ok: bool
    status: int | None
    error_type: str
    error_message: str


@dataclass(frozen=True)
class SSLHealthReport:
    """Structured diagnostic of the current Python SSL configuration."""

    python_version: str
    python_executable: str
    platform: str
    openssl_version: str
    default_cafile: str
    default_cafile_exists: bool
    default_capath: str
    default_capath_exists: bool
    loaded_ca_count: int
    override_env: dict[str, str]
    override_env_broken: dict[str, str]
    certifi_path: str
    certifi_version: str
    probes: list[ProbeResult]
    verdict: str
    remediation: list[str]


def _load_ca_count() -> int:
    """Return the number of CA certs the default SSL context loads."""
    try:
        return len(ssl.create_default_context().get_ca_certs())
    except Exception:
        return 0


def _check_env_overrides() -> tuple[dict[str, str], dict[str, str]]:
    """Return (all set overrides, subset pointing at missing paths)."""
    set_vars = {k: os.environ[k] for k in OVERRIDE_ENV_VARS if k in os.environ}
    broken = {
        k: v
        for k, v in set_vars.items()
        # SSL_CERT_DIR and CAPATH can be directories; the rest are files.
        if not (os.path.isfile(v) or os.path.isdir(v))
    }
    return set_vars, broken


def _probe(url: str, timeout: float = 5.0) -> ProbeResult:
    """Hit ``url`` with stdlib urllib (uses Python's default SSL context)."""
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return ProbeResult(
                url=url,
                ok=True,
                status=resp.status,
                error_type="",
                error_message="",
            )
    except Exception as e:
        return ProbeResult(
            url=url,
            ok=False,
            status=None,
            error_type=type(e).__name__,
            error_message=str(e)[:240],
        )


def _certifi_info() -> tuple[str, str]:
    """Return (path, version) of the certifi bundle, if installed."""
    try:
        import certifi  # type: ignore

        return certifi.where(), getattr(certifi, "__version__", "unknown")
    except ImportError:
        return "", "not installed"


def _build_verdict_and_remediation(
    cafile_exists: bool,
    capath_exists: bool,
    ca_count: int,
    broken_env: dict[str, str],
    probes: list[ProbeResult],
) -> tuple[str, list[str]]:
    """Produce a human-readable verdict and ordered remediation steps."""
    # Classify failures: SSL-specific errors are the only ones that should
    # determine the verdict. Network/auth/DNS errors are reported verbatim
    # but don't downgrade "HEALTHY" to "BROKEN" — TLS is still working.
    all_ok = all(p.ok for p in probes)
    any_ok = any(p.ok for p in probes)
    ssl_failures = [p for p in probes if not p.ok and p.error_type in SSL_ERROR_TYPES]
    non_ssl_failures = [p for p in probes if not p.ok and p.error_type not in SSL_ERROR_TYPES]

    remediation: list[str] = []

    if broken_env:
        remediation.append(
            f"Unset or fix these env vars — they point at paths that don't exist: "
            f"{', '.join(broken_env.keys())}. "
            "Check your shell profile (~/.zshrc, ~/.bash_profile) and remove the "
            "offending export, then restart your shell."
        )

    if not cafile_exists and not capath_exists:
        remediation.append(
            "Neither the default cafile nor capath exists. On macOS + Homebrew, "
            "run `brew install ca-certificates` (or `brew reinstall ca-certificates`) "
            "and then `brew postinstall ca-certificates` to refresh the bundle at "
            "/opt/homebrew/etc/ca-certificates/cert.pem."
        )

    if ca_count == 0:
        remediation.append(
            "Zero CAs are loaded by the default SSL context. If the cafile exists "
            "but is empty, the bundle is corrupted — reinstall ca-certificates. "
            "If the cafile doesn't exist, Python was compiled against a path that "
            "has since been removed; recreating the venv usually fixes it."
        )

    if ssl_failures and cafile_exists and ca_count > 0 and not broken_env:
        remediation.append(
            "TLS verification is failing despite a present cert bundle. Possible "
            "causes: corporate TLS inspection proxy (check for a custom CA that "
            "needs to be added to the bundle), outdated ca-certificates "
            "(`brew upgrade ca-certificates`), or a venv built against a since-"
            "removed bundle (recreate the venv)."
        )

    if non_ssl_failures and not ssl_failures:
        # Worth telling the user so they don't misread a 401/DNS blip as SSL.
        remediation.append(
            "One or more probes failed with non-SSL errors (see 'probes' field). "
            "These are unrelated to certificate configuration — most likely "
            "network, auth, or service-side issues. TLS itself appears healthy."
        )

    # Verdict priority:
    #   BROKEN  — configuration is wrong (missing bundle, broken env, zero CAs,
    #             or an actual SSL cert failure from a probe).
    #   DEGRADED— config looks fine but not all probes succeeded for non-SSL
    #             reasons. Verification works where it was reachable.
    #   HEALTHY — all probes succeeded (or probes skipped) with clean config.
    config_broken = not cafile_exists or ca_count == 0 or bool(broken_env)
    if config_broken or ssl_failures:
        verdict = "BROKEN"
    elif all_ok:
        verdict = "HEALTHY"
        if not remediation:
            remediation.append("No action needed.")
    elif any_ok:
        verdict = "DEGRADED"
    else:
        verdict = "BROKEN"

    return verdict, remediation


def check_ssl_health(probe: bool = True) -> SSLHealthReport:
    """Run a full SSL/TLS configuration audit.

    Args:
        probe: If True (default), make live HTTPS requests to canonical
            endpoints to confirm verification actually works. Set to False
            for offline diagnostics (reports config only).

    Returns:
        A :class:`SSLHealthReport` with paths, env vars, probe results,
        verdict (HEALTHY / DEGRADED / BROKEN), and concrete remediation
        steps suitable for a non-developer to follow.
    """
    paths = ssl.get_default_verify_paths()
    cafile = paths.cafile or ""
    capath = paths.capath or ""
    cafile_exists = bool(cafile) and os.path.isfile(cafile)
    capath_exists = bool(capath) and os.path.isdir(capath)

    set_env, broken_env = _check_env_overrides()
    certifi_path, certifi_version = _certifi_info()
    ca_count = _load_ca_count()

    probes: list[ProbeResult] = [_probe(url) for url in PROBE_URLS] if probe else []

    verdict, remediation = _build_verdict_and_remediation(
        cafile_exists=cafile_exists,
        capath_exists=capath_exists,
        ca_count=ca_count,
        broken_env=broken_env,
        probes=probes,
    )

    return SSLHealthReport(
        python_version=platform.python_version(),
        python_executable=sys.executable,
        platform=platform.platform(),
        openssl_version=ssl.OPENSSL_VERSION,
        default_cafile=cafile,
        default_cafile_exists=cafile_exists,
        default_capath=capath,
        default_capath_exists=capath_exists,
        loaded_ca_count=ca_count,
        override_env=set_env,
        override_env_broken=broken_env,
        certifi_path=certifi_path,
        certifi_version=certifi_version,
        probes=probes,
        verdict=verdict,
        remediation=remediation,
    )


def report_to_dict(report: SSLHealthReport) -> dict:
    """JSON-serializable dict form of an :class:`SSLHealthReport`."""
    d = asdict(report)
    d["probes"] = [asdict(p) for p in report.probes]
    return d
