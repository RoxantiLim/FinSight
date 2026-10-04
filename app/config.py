from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field, SecretStr

DEFAULT_DEMO_USER_ID = "demo-user"
DEFAULT_DATABASE_PATH = Path("data/runtime/finsight.db")
DEFAULT_DEMO_OUTPUT_DIR = Path("data/demo")


class AgentSettings(BaseModel):
    api_key: SecretStr
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"
    reasoning_effort: Literal["none", "low", "high", "max"] = "high"
    max_tool_calls: int = Field(default=10, ge=1, le=50)
    max_consecutive_errors: int = Field(default=3, ge=1, le=10)
    timeout_seconds: float = Field(default=90.0, gt=0, le=600)
    request_timeout_seconds: float = Field(default=45.0, gt=0, le=300)
    max_output_tokens: int = Field(default=3000, ge=256, le=16000)

    @classmethod
    def from_env(cls) -> AgentSettings:
        load_dotenv()
        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            raise ValueError(
                "未配置 DEEPSEEK_API_KEY。请在 .env 或当前终端环境变量中设置。"
            )
        return cls(
            api_key=SecretStr(api_key),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
            reasoning_effort=os.getenv("DEEPSEEK_REASONING_EFFORT", "high"),
        )
