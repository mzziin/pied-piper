"""Configuration management for Pied Piper.

Loads configuration from environment variables and optional .env file,
exposing a typed Settings instance.
"""

from functools import lru_cache
from pathlib import Path
from typing import List, Union

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with type validation and defaults."""

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # General environment
    environment: str = Field(default="development", description="Execution environment (development, production, test)")
    log_level: str = Field(default="INFO", description="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)")

    # Signaling service configuration
    signaling_host: str = Field(default="0.0.0.0", description="Host to bind signaling server to")
    signaling_port: int = Field(default=8000, description="Port to bind signaling server to")
    signaling_url: str = Field(default="ws://localhost:8000/ws", description="WebSocket URL for signaling client")
    room_ttl_seconds: int = Field(default=900, description="Time-to-live in seconds for waiting rooms")

    # WebRTC NAT traversal (STUN / TURN)
    stun_urls: Union[str, List[str]] = Field(
        default="stun:stun.l.google.com:19302",
        description="Comma-separated or list of STUN server URLs",
    )
    turn_url: str = Field(default="", description="TURN server URL (if configured)")
    turn_username: str = Field(default="", description="TURN server username")
    turn_credential: str = Field(default="", description="TURN server credential")

    # Transfer protocol parameters
    chunk_size_bytes: int = Field(default=262144, description="Default chunk size in bytes (16 KB)")
    sliding_window_size: int = Field(default=32, description="Sliding window size for flow control")
    sqlite_path: Path = Field(default=Path("./pied_piper.db"), description="Path to local SQLite database")

    @field_validator("sqlite_path", mode="before")
    @classmethod
    def parse_sqlite_path(cls, v: Union[str, Path]) -> Path:
        """Ensure sqlite_path is resolved as a pathlib.Path."""
        if isinstance(v, str):
            return Path(v)
        return v

    @property
    def stun_urls_list(self) -> List[str]:
        """Return STUN URLs as a list of strings."""
        if isinstance(self.stun_urls, list):
            return self.stun_urls
        if isinstance(self.stun_urls, str):
            return [url.strip() for url in self.stun_urls.split(",") if url.strip()]
        return []


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached singleton instance of Settings."""
    return Settings()
