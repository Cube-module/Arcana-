from pathlib import Path
import yaml
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent
CONFIG = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    bot_token: SecretStr = SecretStr("")
    database_url: SecretStr = SecretStr("")
    llm_base_url: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = ""
    demo_mode: bool = True

    def validate_runtime(self):
        for field in ("bot_token", "database_url", "llm_base_url", "llm_api_key", "llm_model"):
            value = getattr(self, field)
            if not (value.get_secret_value() if isinstance(value, SecretStr) else value):
                raise ValueError(f"Missing configuration: {field.upper()}")
        if not self.llm_base_url.startswith("https://"):
            raise ValueError("LLM_BASE_URL must use HTTPS")
