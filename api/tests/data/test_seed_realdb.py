"""seed/realdb.py: .env loading for the real-database CLIs, the connection, and the failure sentences."""

from __future__ import annotations

import os
from pathlib import Path
from typing import ClassVar
from urllib.parse import quote_plus

import pytest
from pymongo.errors import (
    AutoReconnect,
    ConfigurationError,
    OperationFailure,
    ServerSelectionTimeoutError,
)

from agent.preflight import ENV_FILES, secret_values
from seed import realdb

USER = "presenter"
PASSWORD = "pa:ss@w0rd%21"
URI = f"mongodb+srv://{USER}:{quote_plus(PASSWORD)}@cluster0.example.mongodb.net/?appName=StitchCluster0Presenter"
SECRETS = secret_values({"MONGODB_URI": URI})
FORBIDDEN = (USER, PASSWORD, quote_plus(PASSWORD), "StitchCluster0Presenter", URI)

VARS = ("STITCH_REALDB_A", "STITCH_REALDB_B", "STITCH_REALDB_C")


@pytest.fixture
def clean_vars(monkeypatch):
    """The test variables start unset and are removed afterwards, even when load_dotenv set them."""
    for name in VARS:
        monkeypatch.setenv(name, "x")  # recorded, so monkeypatch restores "unset" at teardown
        monkeypatch.delenv(name)
    return monkeypatch


def write(path: Path, **values: str) -> Path:
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    return path


# ------------------------------------------------------------------------- load_env


def test_load_env_reads_api_env_first_then_the_root_and_never_overrides(tmp_path, clean_vars):
    api_env = write(tmp_path / "api.env", STITCH_REALDB_A="from-api", STITCH_REALDB_B="from-api")
    root_env = write(tmp_path / "root.env", STITCH_REALDB_B="from-root", STITCH_REALDB_C="from-root")
    clean_vars.setenv("STITCH_REALDB_A", "from-shell")

    loaded = realdb.load_env([api_env, root_env, tmp_path / "missing.env"], enabled=True)

    assert loaded == [api_env, root_env]
    assert os.environ["STITCH_REALDB_A"] == "from-shell"  # the environment wins
    assert os.environ["STITCH_REALDB_B"] == "from-api"  # then api/.env
    assert os.environ["STITCH_REALDB_C"] == "from-root"  # then ../.env


def test_load_env_applies_an_embedding_dim_from_a_dotenv_file_to_db_client(tmp_path, clean_vars):
    """db.client read EMBEDDING_DIM at import, before the CLI loaded .env: the CLI must not
    create 1024-dimension vector indexes when the .env (and so the API) says 512."""
    from db import client

    clean_vars.delenv("EMBEDDING_DIM", raising=False)
    clean_vars.setenv("EMBEDDING_DIM", "x")
    clean_vars.delenv("EMBEDDING_DIM")
    clean_vars.setattr(client, "VECTOR_DIM", 1024)
    realdb.load_env([write(tmp_path / "api.env", EMBEDDING_DIM="512")], enabled=True)
    assert client.VECTOR_DIM == 512
    assert client._vector_definition()["fields"][0]["numDimensions"] == 512


def test_sync_db_client_ignores_unusable_values(monkeypatch):
    from db import client

    monkeypatch.setattr(client, "VECTOR_DIM", 1024)
    for raw in ("", "abc", "0", "-5", "1024"):
        assert realdb.sync_db_client({"EMBEDDING_DIM": raw}) == []
    assert client.VECTOR_DIM == 1024
    assert realdb.sync_db_client({"EMBEDDING_DIM": " 256 "}) == ["EMBEDDING_DIM 1024 -> 256"]
    assert client.VECTOR_DIM == 256


def test_load_env_is_off_under_pytest(tmp_path, clean_vars):
    api_env = write(tmp_path / "api.env", STITCH_REALDB_A="leaked")
    assert realdb.load_env([api_env]) == []
    assert "STITCH_REALDB_A" not in os.environ


def test_load_env_defaults_to_the_api_files():
    assert realdb.load_env.__defaults__[0] == ENV_FILES
    api_dir = Path(realdb.__file__).resolve().parents[1]
    assert ENV_FILES == (api_dir / ".env", api_dir.parent / ".env")


@pytest.mark.parametrize(("env", "under_pytest", "expected"), [
    ({}, False, True),
    ({"STITCH_NO_DOTENV": "1"}, False, False),
    ({"STITCH_NO_DOTENV": " TRUE "}, False, False),
    ({"STITCH_NO_DOTENV": "yes"}, False, False),
    ({"STITCH_NO_DOTENV": "0"}, False, True),
    ({}, True, False),
])
def test_dotenv_rules(env, under_pytest, expected):
    assert realdb.dotenv_enabled(env, under_pytest=under_pytest) is expected


def test_dotenv_is_off_under_this_test_run():
    assert realdb.dotenv_enabled({}) is False


def test_uri_and_db_name_from_env():
    assert realdb.uri_from_env({"MONGODB_URI": "  mongodb://h  "}) == "mongodb://h"
    assert realdb.uri_from_env({"MONGODB_URI": "   "}) is None
    assert realdb.uri_from_env({}) is None
    assert realdb.db_name_from_env({}) == "stitch"
    assert realdb.db_name_from_env({"MONGODB_DB": " demo "}) == "demo"
    assert realdb.db_name_from_env({"MONGODB_DB": ""}) == "stitch"


def test_redacted_shows_only_scheme_and_hosts():
    shown = realdb.redacted(URI)
    assert shown == "mongodb+srv://<credentials redacted>@cluster0.example.mongodb.net"
    assert realdb.redacted("mongodb://localhost:27017") == "mongodb://localhost:27017"


# -------------------------------------------------------------------------- connect


class FakeAdmin:
    def __init__(self, fail: Exception | None) -> None:
        self.fail = fail
        self.commands: list[str] = []

    def command(self, name):
        self.commands.append(name)
        if self.fail:
            raise self.fail
        return {"ok": 1.0}


class FakeClient:
    made: ClassVar[list[FakeClient]] = []
    fail: ClassVar[Exception | None] = None

    def __init__(self, uri, **kwargs) -> None:
        self.uri, self.kwargs = uri, kwargs
        self.admin = FakeAdmin(FakeClient.fail)
        self.closed = False
        FakeClient.made.append(self)

    def __getitem__(self, name):
        return f"db:{name}"

    def close(self):
        self.closed = True


@pytest.fixture
def fake_client(monkeypatch):
    FakeClient.made, FakeClient.fail = [], None
    monkeypatch.setattr(realdb, "MongoClient", FakeClient)
    return FakeClient


def test_connect_bounds_every_timeout_and_pings(fake_client):
    client, db = realdb.connect("mongodb://h", "stitch", timeout_ms=1500, appname="stitch-seed")
    assert db == "db:stitch"
    assert client.kwargs == {
        "tz_aware": True, "serverSelectionTimeoutMS": 1500, "connectTimeoutMS": 1500,
        "socketTimeoutMS": 20000, "appname": "stitch-seed",
    }
    assert client.admin.commands == ["ping"] and not client.closed
    _, _ = realdb.connect("mongodb://h", "stitch")
    assert fake_client.made[-1].kwargs["serverSelectionTimeoutMS"] == 5000
    assert fake_client.made[-1].kwargs["appname"] == "stitch-preflight"


def test_connect_closes_the_client_when_the_ping_fails(fake_client):
    fake_client.fail = ServerSelectionTimeoutError("down")
    with pytest.raises(ServerSelectionTimeoutError):
        realdb.connect("mongodb://h", "stitch", timeout_ms=10)
    assert fake_client.made[-1].closed


# ----------------------------------------------------------------- describe_failure


def assert_clean(text: str) -> None:
    for secret in FORBIDDEN:
        assert secret.lower() not in text.lower(), (secret, text)


def test_unreachable_names_the_atlas_ip_access_list():
    text = realdb.describe_failure(ServerSelectionTimeoutError(f"{URI}: timed out"), SECRETS)
    assert "Could not reach MongoDB" in text and "IP access list" in text
    assert text.count("\n") == 0
    assert_clean(text)


@pytest.mark.parametrize("code", [18, 8000])
def test_authentication_failed(code):
    exc = OperationFailure(f"bad auth : Authentication failed for {URI}", code=code)
    text = realdb.describe_failure(exc, SECRETS)
    assert "authentication failed" in text and "percent-encode" in text
    assert_clean(text)


def test_a_bad_uri_names_the_problem_without_quoting_the_password(monkeypatch):
    bad = "mongodb+srv://presenter:pa/ss@w0rd@cluster0.example.mongodb.net/"
    monkeypatch.setenv("MONGODB_URI", bad)
    secrets = secret_values({"MONGODB_URI": bad})
    exc = ConfigurationError("Bad database name 'ss@w0rd@cluster0.example.mongodb.net'")
    text = realdb.describe_failure(exc, secrets)
    assert text.startswith("MONGODB_URI is not a valid MongoDB connection string")
    assert "percent-encode" in text
    assert "w0rd" not in text and "presenter" not in text


def test_pymongos_value_error_for_an_unescaped_slash_is_a_bad_uri_sentence(monkeypatch):
    """pymongo raises a bare ValueError ('Port contains non-digit characters') for this one."""
    monkeypatch.delenv("MONGODB_URI", raising=False)
    bad = "mongodb+srv://presenter:pa/ss@w0rd@cluster0.example.mongodb.net/"
    exc = ValueError("Port contains non-digit characters. Hint: username and password must be escaped "
                     "according to RFC 3986, use urllib.parse.quote_plus.")
    text = realdb.describe_failure(exc, secret_values({"MONGODB_URI": bad}), uri=bad)
    assert text.startswith("MONGODB_URI is not a valid MongoDB connection string")
    assert "percent-encode" in text and "w0rd" not in text
    # A ValueError that has nothing to do with the URI stays an unexpected error.
    assert realdb.describe_failure(ValueError("boom"), [], uri="mongodb://h").startswith("Unexpected error")


def test_a_bad_uri_without_a_known_problem_shows_the_scrubbed_parser_message(monkeypatch):
    monkeypatch.setenv("MONGODB_URI", URI)
    text = realdb.describe_failure(ConfigurationError(f"Unknown option fooBar in {URI}"), SECRETS)
    assert "not a valid MongoDB connection string" in text and "Unknown option" in text
    assert_clean(text)


@pytest.mark.parametrize("exc", [
    ConfigurationError("The DNS query name does not exist: _mongodb._tcp.cluster0.example.mongodb.net."),
    ConfigurationError("The resolution lifetime expired after 20.0 seconds"),
])
def test_srv_lookup_failures(exc):
    text = realdb.describe_failure(exc, SECRETS)
    assert "DNS SRV record" in text and "cluster host name" in text
    assert_clean(text)


def test_unresolvable_host_during_selection():
    exc = ServerSelectionTimeoutError("cluster0.example.mongodb.net:27017: [Errno 8] nodename nor servname provided")
    assert "Could not resolve the MongoDB host" in realdb.describe_failure(exc, SECRETS)


def test_other_failures_are_one_scrubbed_sentence():
    text = realdb.describe_failure(OperationFailure(f"not authorized on stitch via {URI}", code=13), SECRETS)
    assert "refused the operation (code 13)" in text
    assert_clean(text)
    text = realdb.describe_failure(AutoReconnect(f"connection reset {URI}"), SECRETS)
    assert text.startswith("The connection to MongoDB failed")
    assert_clean(text)
    text = realdb.describe_failure(RuntimeError(f"odd {URI}"), SECRETS)
    assert text.startswith("Unexpected error talking to MongoDB")
    assert_clean(text)
