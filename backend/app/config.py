from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://fcip:fcip_dev_password@localhost:5432/fcip"

    jwt_secret_key: str = "change-me-dev-secret-do-not-use-in-prod"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_hours: int = 8
    session_idle_timeout_minutes: int = 30

    login_lockout_max_attempts: int = 5
    login_lockout_minutes: int = 15

    # Comma-separated browser origins allowed to call the API (the Vite dev server by default).
    cors_origins: str = "http://localhost:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
