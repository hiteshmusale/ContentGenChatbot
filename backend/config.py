from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://chatbot:chatbot@localhost:5432/chatbot"
    data_dir: Path = Path("./data")
    api_url: str = "http://localhost:8000"
    anthropic_api_key: str = ""
    model: str = "claude-sonnet-5-5"
    render_previews: bool = True


settings = Settings()
