"""Tests for backend.config Settings."""

import os
from pathlib import Path
from backend.config import Settings, get_settings


def test_default_settings():
    """Verify that default settings match specifications in PROJECT.md and milestone_1_lan.md."""
    settings = Settings(_env_file=None)  # avoid picking up ambient .env during test
    assert settings.environment == "development"
    assert settings.signaling_host == "0.0.0.0"
    assert settings.signaling_port == 8000
    assert settings.signaling_url == "ws://localhost:8000/ws"
    assert settings.room_ttl_seconds == 900
    assert settings.chunk_size_bytes == 262144
    assert settings.sliding_window_size == 32
    assert isinstance(settings.sqlite_path, Path)
    assert settings.sqlite_path == Path("./pied_piper.db")
    assert settings.log_level == "INFO"
    assert "stun:stun.l.google.com:19302" in settings.stun_urls_list


def test_env_var_overrides(monkeypatch):
    """Verify that environment variables correctly override defaults."""
    monkeypatch.setenv("SIGNALING_PORT", "9999")
    monkeypatch.setenv("SIGNALING_HOST", "127.0.0.1")
    monkeypatch.setenv("SIGNALING_URL", "ws://127.0.0.1:9999/ws")
    monkeypatch.setenv("CHUNK_SIZE_BYTES", "32768")
    monkeypatch.setenv("SQLITE_PATH", "/tmp/custom.db")
    monkeypatch.setenv("STUN_URLS", "stun:stun1.example.com:19302, stun:stun2.example.com:19302")

    settings = Settings(_env_file=None)
    assert settings.signaling_port == 9999
    assert settings.signaling_host == "127.0.0.1"
    assert settings.signaling_url == "ws://127.0.0.1:9999/ws"
    assert settings.chunk_size_bytes == 32768
    assert settings.sqlite_path == Path("/tmp/custom.db")
    assert settings.stun_urls_list == [
        "stun:stun1.example.com:19302",
        "stun:stun2.example.com:19302",
    ]


def test_get_settings_caching():
    """Verify get_settings returns a valid Settings instance."""
    settings_1 = get_settings()
    settings_2 = get_settings()
    assert settings_1 is settings_2
    assert isinstance(settings_1, Settings)
