import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", f"sqlite:///{(ROOT / 'data' / 'roster.db').as_posix()}"))
    public_url: str = field(default_factory=lambda: os.getenv("PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/"))
    client_id: str = field(default_factory=lambda: os.getenv("DISCORD_CLIENT_ID", ""))
    client_secret: str = field(default_factory=lambda: os.getenv("DISCORD_CLIENT_SECRET", ""))
    guild_id: str = field(default_factory=lambda: os.getenv("GUILD_ID", ""))
    admin_ids: tuple[str, ...] = field(default_factory=lambda: tuple(v.strip() for v in os.getenv("AUTHORIZED_USER_IDS", "474113669295505409").split(",") if v.strip()))
    production: bool = field(default_factory=lambda: os.getenv("APP_ENV") == "production")
    demo: bool = False
    verification_mode: str = field(default_factory=lambda: os.getenv("VERIFICATION_MODE", "classes"))

    def validate(self):
        parsed = urlparse(self.public_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path not in {"", "/"}:
            raise ValueError("PUBLIC_URL must be an absolute origin without a path")
        if self.production and (parsed.scheme != "https" or self.demo):
            raise ValueError("Production requires HTTPS and cannot enable demo mode")
        if self.production and (not self.client_id or not self.client_secret or not self.guild_id):
            raise ValueError("Set DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET and GUILD_ID")
        if self.production and not self.database_url.startswith(("postgres://", "postgresql://", "postgresql+psycopg://")):
            raise ValueError("Production requires a shared PostgreSQL database")

    @property
    def redirect_uri(self):
        return self.public_url + "/auth/callback"
