from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    root: Path
    rss_url: str
    transcription_provider: str = "openai"
    analysis_provider: str = "openai"
    openai_transcription_model: str = "whisper-1"
    openai_analysis_model: str = "gpt-6-astra"
    wordpress_base_url: str = ""
    wordpress_username: str = ""
    wordpress_application_password: str = ""
    create_wordpress_drafts: bool = False
    http_timeout_seconds: int = 60

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            root=Path(os.getenv("LFTP_REPOSITORY_ROOT", ".")).resolve(),
            rss_url=os.getenv("LFTP_RSS_URL", ""),
            transcription_provider=os.getenv("LFTP_TRANSCRIPTION_PROVIDER", "openai"),
            analysis_provider=os.getenv("LFTP_ANALYSIS_PROVIDER", "openai"),
            openai_transcription_model=os.getenv("OPENAI_TRANSCRIPTION_MODEL", "whisper-1"),
            openai_analysis_model=os.getenv("OPENAI_ANALYSIS_MODEL", "gpt-6-astra"),
            wordpress_base_url=os.getenv("WORDPRESS_BASE_URL", ""),
            wordpress_username=os.getenv("WORDPRESS_USERNAME", ""),
            wordpress_application_password=os.getenv("WORDPRESS_APPLICATION_PASSWORD", ""),
            create_wordpress_drafts=os.getenv("LFTP_CREATE_WORDPRESS_DRAFTS", "false").lower() == "true",
            http_timeout_seconds=int(os.getenv("LFTP_HTTP_TIMEOUT_SECONDS", "60")),
        )

