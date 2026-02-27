"""
pipelines/config.py
--------------------
Centralised settings loaded from .env via pydantic-settings.
All nodes and tools import from here — never read os.environ directly.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Azure OpenAI ---
    azure_openai_api_key: str = Field(default="", alias="AZURE_OPENAI_API_KEY")
    azure_openai_endpoint: str = Field(default="", alias="AZURE_OPENAI_ENDPOINT")
    azure_openai_deployment: str = Field(default="gpt-4o", alias="AZURE_OPENAI_DEPLOYMENT")
    azure_openai_api_version: str = Field(default="2024-12-01-preview", alias="AZURE_OPENAI_API_VERSION")

    # --- Dataverse (Guide 01) ---
    dataverse_base_url: str = Field(default="", alias="DATAVERSE_BASE_URL")
    dataverse_tenant_id: str = Field(default="", alias="DATAVERSE_TENANT_ID")
    dataverse_client_id: str = Field(default="", alias="DATAVERSE_CLIENT_ID")
    dataverse_client_secret: str = Field(default="", alias="DATAVERSE_CLIENT_SECRET")
    dataverse_scope: str = Field(default="", alias="DATAVERSE_SCOPE")
    dataverse_publisher_prefix: str = Field(default="new_", alias="DATAVERSE_PUBLISHER_PREFIX")

    # --- Power Automate Core Flow (Guide 02) ---
    core_flow_url: str = Field(default="", alias="CORE_FLOW_URL")
    core_flow_sas_key: str = Field(default="", alias="CORE_FLOW_SAS_KEY")

    # --- Smoke Test (Guide 05) ---
    smoke_test_freshness_minutes: int = Field(default=60, alias="SMOKE_TEST_FRESHNESS_MINUTES")

    # --- Test Harness ---
    max_parallel_l1: int = Field(default=10, alias="MAX_PARALLEL_L1")
    max_retry_count: int = Field(default=2, alias="MAX_RETRY_COUNT")

    @property
    def dataverse_publisher(self) -> str:
        """Return publisher prefix (e.g. 'new_')."""
        return self.dataverse_publisher_prefix

    @property
    def table(self) -> dict[str, str]:
        """Resolve all custom Dataverse table names using the publisher prefix."""
        p = self.dataverse_publisher_prefix
        return {
            "eval_run": f"{p}evalruns",
            "eval_result": f"{p}evalresults",
            "eval_artifact": f"{p}evalartifacts",
            "test_case": f"{p}testcases",
            "agent_test_config": f"{p}agenttestconfigs",
            "smoke_test_log": f"{p}smoketestlogs",
            "batch_upload": f"{p}batchuploads",
            "batch_row": f"{p}batchrows",
        }


settings = Settings()
