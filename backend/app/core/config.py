from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    data_dir: Path = Path("data")
    groq_api_key: str = ""
    groq_models: str = ""
    gemini_api_key: str = ""
    gemini_models: str = ""
    ollama_url: str = ""
    ollama_models: str = ""
    embedding_model: str = ""
    embedding_cache_dir: Path = Path("data/models")
    reranker_model: str = ""
    rerank_enabled: bool = True
    low_memory: bool = False
    cors_origins: str = "http://localhost:3000"
    access_token: str = ""
    max_repo_mb: int = 100
    max_file_bytes: int = 200000
    max_files: int = 2000
    max_chunks: int = 12000
    clone_timeout: int = 120
    min_dense_score: float = 0.30
    max_repos: int = 10
    query_cache_size: int = 128
    llm_attempt_timeout: float = 45
    llm_helper_timeout: float = 20


settings = Settings()
