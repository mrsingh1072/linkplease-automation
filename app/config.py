from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    pseudogram_api_key: str
    pseudogram_base_url: str = "https://pseudogram-api.onrender.com"
    mongodb_url: str = "mongodb://localhost:27017"
    database_name: str = "linkplease"

    model_config = {"env_file": ".env", "case_sensitive": False}


@lru_cache
def get_settings() -> Settings:
    return Settings()
