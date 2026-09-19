"""The sandbox image bakes the test database's ``mongod`` and forbids downloading one (phase-65).

Static checks on the real ``sandbox/Dockerfile`` — the build itself, and a posture-accurate run of
``mongodb-memory-server`` against the baked binary with no network, are the sandbox smoke's job.
These guard the parts a casual edit could silently undo: a version bump that leaves the library
expecting another binary, a checksum dropped, or the "never download" switch turned back on.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_DOCKERFILE = Path(__file__).resolve().parents[3] / "sandbox" / "Dockerfile"


@pytest.fixture(scope="module")
def dockerfile() -> str:
    assert _DOCKERFILE.is_file(), f"sandbox Dockerfile missing at {_DOCKERFILE}"
    return _DOCKERFILE.read_text(encoding="utf-8")


def _arg(dockerfile: str, name: str) -> str:
    match = re.search(rf"^ARG {name}=(\S+)$", dockerfile, re.MULTILINE)
    assert match, f"ARG {name} is not pinned in the Dockerfile"
    return match.group(1)


def test_the_mongod_version_is_pinned_and_shared_with_the_library(dockerfile: str) -> None:
    version = _arg(dockerfile, "MONGO_VERSION")

    assert re.fullmatch(r"\d+\.\d+\.\d+", version), "pin an exact MongoDB version"
    # The library is told the binary's version, so it neither warns nor picks a wrong engine.
    assert "MONGOMS_VERSION=${MONGO_VERSION}" in dockerfile
    # …and the tarball fetched is that same version.
    assert "debian12-${MONGO_VERSION}" in dockerfile
    assert "ubuntu2204-${MONGO_VERSION}" in dockerfile


def test_both_architectures_are_checksum_verified(dockerfile: str) -> None:
    for arg in ("MONGO_SHA256_AMD64", "MONGO_SHA256_ARM64"):
        assert re.fullmatch(r"[0-9a-f]{64}", _arg(dockerfile, arg)), f"{arg} is not a sha256"
    assert "sha256sum -c" in dockerfile


@pytest.mark.parametrize(
    "setting",
    [
        "MONGOMS_SYSTEM_BINARY=/usr/local/bin/mongod",
        # Offline a download can only fail — so it must never be attempted.
        "MONGOMS_RUNTIME_DOWNLOAD=false",
        # …not even by the non-core package's postinstall inside an install window.
        "MONGOMS_DISABLE_POSTINSTALL=1",
    ],
)
def test_the_library_is_pointed_at_the_baked_binary(dockerfile: str, setting: str) -> None:
    assert setting in dockerfile


def test_the_build_fails_if_the_binary_cannot_run_as_the_sandbox_user(dockerfile: str) -> None:
    assert "install -m 0755" in dockerfile and "/usr/local/bin/mongod" in dockerfile
    assert "su -s /bin/bash app -c 'mongod --version" in dockerfile


def test_only_mongod_is_kept_from_the_tarball(dockerfile: str) -> None:
    """The image gains a test database binary — not mongos, not tools, not the archive."""
    assert '"${tarball}/bin/mongod"' in dockerfile
    assert 'rm -rf /tmp/mongodb.tgz "/tmp/${tarball}"' in dockerfile
