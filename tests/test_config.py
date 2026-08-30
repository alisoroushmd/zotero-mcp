"""Tests for centralized Config module."""

import pytest

from zotero_mcp.config import Config, _reset_config, get_config, load_config


@pytest.fixture(autouse=True)
def _clean_singleton():
    """Reset config singleton before and after each test."""
    _reset_config()
    yield
    _reset_config()


class TestConfigProperties:
    """Test Config dataclass computed properties."""

    def test_has_web_api_true(self):
        cfg = Config(zotero_api_key="key", zotero_user_id="123")
        assert cfg.has_web_api is True

    def test_has_web_api_missing_key(self):
        cfg = Config(zotero_api_key="", zotero_user_id="123")
        assert cfg.has_web_api is False

    def test_has_web_api_missing_user(self):
        cfg = Config(zotero_api_key="key", zotero_user_id="")
        assert cfg.has_web_api is False

    def test_has_web_api_both_missing(self):
        cfg = Config()
        assert cfg.has_web_api is False

    def test_missing_web_vars_none_missing(self):
        cfg = Config(zotero_api_key="key", zotero_user_id="123")
        assert cfg.missing_web_vars == []

    def test_missing_web_vars_both_missing(self):
        cfg = Config()
        assert "ZOTERO_API_KEY" in cfg.missing_web_vars
        assert "ZOTERO_USER_ID" in cfg.missing_web_vars

    def test_missing_web_vars_one_missing(self):
        cfg = Config(zotero_api_key="key")
        assert cfg.missing_web_vars == ["ZOTERO_USER_ID"]

    def test_has_openalex_true(self):
        cfg = Config(openalex_api_key="abc")
        assert cfg.has_openalex is True

    def test_has_openalex_false(self):
        cfg = Config()
        assert cfg.has_openalex is False

    def test_effective_graph_db_path_uses_override(self):
        cfg = Config(graph_db_path="/custom/path.db")
        assert cfg.effective_graph_db_path == "/custom/path.db"

    def test_effective_graph_db_path_uses_default(self):
        cfg = Config()
        assert cfg.effective_graph_db_path == cfg.default_graph_db_path
        assert "zotero-mcp" in cfg.effective_graph_db_path

    def test_graph_materialization_limit_defaults_to_100k(self):
        assert Config().graph_materialization_limit == 100_000

    def test_legacy_positional_field_order_is_preserved(self):
        """The new graph limit does not shift pre-existing positional arguments."""
        cfg = Config(
            "api-key",
            "user-id",
            "openalex-key",
            "semantic-scholar-key",
            "ncbi-key",
            "author@example.org",
            "/graphs/library.db",
            "/zotero-data",
            "/linked-attachments",
            "imported",
            "/xdg-data",
        )

        assert cfg.graph_db_path == "/graphs/library.db"
        assert cfg.zotero_data_dir == "/zotero-data"
        assert cfg.linked_attachment_dir == "/linked-attachments"
        assert cfg.attachment_mode == "imported"
        assert cfg.xdg_data_home == "/xdg-data"
        assert cfg.graph_materialization_limit == 100_000

    def test_default_graph_db_path_respects_xdg(self):
        cfg = Config(xdg_data_home="/tmp/xdg")
        assert cfg.default_graph_db_path.startswith("/tmp/xdg/")


class TestLoadConfig:
    """Test load_config reads from environment."""

    def test_load_config_reads_env(self, monkeypatch):
        monkeypatch.setenv("ZOTERO_API_KEY", "test-key")
        monkeypatch.setenv("ZOTERO_USER_ID", "42")
        monkeypatch.setenv("OPENALEX_API_KEY", "oakey")
        cfg = load_config()
        assert cfg.zotero_api_key == "test-key"
        assert cfg.zotero_user_id == "42"
        assert cfg.openalex_api_key == "oakey"

    def test_load_config_reads_graph_materialization_limit(self, monkeypatch):
        monkeypatch.setenv("ZOTERO_MCP_GRAPH_MATERIALIZATION_LIMIT", "250000")
        assert load_config().graph_materialization_limit == 250_000

    @pytest.mark.parametrize("value", ["0", "-1", "not-an-integer"])
    def test_load_config_rejects_invalid_graph_materialization_limit(self, monkeypatch, value):
        monkeypatch.setenv("ZOTERO_MCP_GRAPH_MATERIALIZATION_LIMIT", value)
        with pytest.raises(ValueError, match="positive integer"):
            load_config()

    def test_load_config_defaults(self, monkeypatch):
        monkeypatch.delenv("ZOTERO_API_KEY", raising=False)
        monkeypatch.delenv("ZOTERO_USER_ID", raising=False)
        cfg = load_config()
        assert cfg.zotero_api_key == ""
        assert cfg.zotero_user_id == ""
        assert cfg.polite_email == "zotero-mcp@example.com"


class TestGetConfig:
    """Test singleton behavior."""

    def test_singleton_returns_same_instance(self, monkeypatch):
        monkeypatch.setenv("ZOTERO_API_KEY", "k1")
        cfg1 = get_config()
        cfg2 = get_config()
        assert cfg1 is cfg2

    def test_reset_clears_singleton(self, monkeypatch):
        monkeypatch.setenv("ZOTERO_API_KEY", "k1")
        cfg1 = get_config()
        _reset_config()
        monkeypatch.setenv("ZOTERO_API_KEY", "k2")
        cfg2 = get_config()
        assert cfg1.zotero_api_key == "k1"
        assert cfg2.zotero_api_key == "k2"
        assert cfg1 is not cfg2
