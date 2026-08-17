from functools import lru_cache
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    pseudogram_api_key: str
    pseudogram_base_url: str = "https://pseudogram-api.onrender.com"
    mongodb_url: str = "mongodb://localhost:27017"
    database_name: str = "linkplease"
    verify_webhook_signature: bool = True

    model_config = {"env_file": ".env", "case_sensitive": False}

    @field_validator("pseudogram_api_key", mode="before")
    @classmethod
    def clean_api_key(cls, v: Any) -> str:
        if isinstance(v, str):
            return v.strip().strip('"').strip("'")
        return str(v) if v is not None else ""

    @field_validator("verify_webhook_signature", mode="before")
    @classmethod
    def parse_verify_signature(cls, v: Any) -> bool:
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return bool(v)
        if isinstance(v, str):
            val = v.strip().lower()
            if val in ("true", "1", "yes", "on"):
                return True
            if val in ("false", "0", "no", "off"):
                return False
        return bool(v)


@lru_cache
def get_settings() -> Settings:
    return Settings()
