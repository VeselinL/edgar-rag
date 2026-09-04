"""Single typed owner for AVA runtime configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALLOWED_MODELS = (
    "AZURE_GPT_4o_2024_1120",
    "AZURE_GPT_41_2025_0414",
    "AZURE_GPT_5_2025_0807",
    "AZURE_GPT_51_2025_1113",
    "AZURE_GPT_54_2026_0305",
    "AZURE_GPT_55_2026_0424",
    "AZURE_GPT_56_SOL_2026_0709",
)
DEFAULT_LLM_MODEL = ALLOWED_MODELS[0]


def load_project_environment(project_root: Path = PROJECT_ROOT) -> None:
    """Load the project dotenv once, preserving process-environment precedence."""
    load_dotenv(project_root / ".env", override=False)


def _text(values: Mapping[str, str], key: str, default: str = "") -> str:
    return values.get(key, default).strip()


def _optional(values: Mapping[str, str], key: str) -> str | None:
    return values.get(key) or None


def _boolean(values: Mapping[str, str], key: str, default: bool) -> bool:
    raw = values.get(key, str(default)).strip().casefold()
    if raw not in {"true", "false"}:
        raise ValueError(f"{key} must be 'true' or 'false'.")
    return raw == "true"


def _integer(
    values: Mapping[str, str], key: str, default: int, *, minimum: int = 1
) -> int:
    value = int(values.get(key, str(default)))
    if value < minimum:
        raise ValueError(f"{key} must be at least {minimum}.")
    return value


def _number(
    values: Mapping[str, str], key: str, default: float, *, minimum: float = 0
) -> float:
    value = float(values.get(key, str(default)))
    if value <= minimum:
        raise ValueError(f"{key} must be greater than {minimum}.")
    return value


def _enum(
    values: Mapping[str, str], key: str, default: str, allowed: set[str]
) -> str:
    value = _text(values, key, default).casefold()
    if value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ValueError(f"{key} must be one of: {choices}.")
    return value


@dataclass(frozen=True)
class ProviderSettings:
    api_key: str | None = field(default=None, repr=False)
    base_url: str | None = None
    app_id: str | None = None
    user_id: str | None = None
    company_id: str | None = None
    api_version: str | None = None
    timeout_seconds: float = 90.0
    maximum_retries: int = 2
    circuit_failures: int = 5
    circuit_recovery_seconds: float = 30.0

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "ProviderSettings":
        return cls(
            api_key=_optional(values, "OPENAI_API_KEY"),
            base_url=_optional(values, "OPENAI_API_URL"),
            app_id=_optional(values, "OPENAI_APP_ID"),
            user_id=_optional(values, "OPENAI_USER_ID"),
            company_id=_optional(values, "OPENAI_COMPANY_ID"),
            api_version=_optional(values, "OPENAI_API_VERSION"),
            timeout_seconds=_number(values, "AVA_PROVIDER_TIMEOUT_SECONDS", 90),
            maximum_retries=_integer(
                values, "AVA_PROVIDER_MAX_RETRIES", 2, minimum=0
            ),
            circuit_failures=_integer(values, "AVA_PROVIDER_CIRCUIT_FAILURES", 5),
            circuit_recovery_seconds=_number(
                values, "AVA_PROVIDER_CIRCUIT_RECOVERY_SECONDS", 30
            ),
        )

    def validate(self, *, required: bool) -> None:
        if self.maximum_retries > 5:
            raise ValueError("AVA_PROVIDER_MAX_RETRIES must be between 0 and 5.")
        if required and (not self.api_key or not self.base_url):
            raise ValueError("The backend LLM credentials are not configured.")

    @classmethod
    def from_environment(cls) -> "ProviderSettings":
        load_project_environment()
        settings = cls.from_mapping(os.environ)
        settings.validate(required=True)
        return settings


@dataclass(frozen=True)
class PipelineSettings:
    mode: str = "real"
    model_device: str = "cpu"
    llm_model: str = DEFAULT_LLM_MODEL
    llm_streaming: bool = True
    context_window_tokens: int = 32_768
    reserved_output_tokens: int = 4_096
    observability_retention_days: int = 30
    qdrant_mode: str = "disabled"
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_api_key: str | None = field(default=None, repr=False)
    qdrant_collection_alias: str = "ava_filing_chunks_current"
    qdrant_local_path: str | None = None
    qdrant_timeout_seconds: int = 30
    request_routing_enabled: bool = True
    calculator_enabled: bool = False
    web_search_enabled: bool = False
    web_search_provider: str = "disabled"
    web_search_api_key: str | None = field(default=None, repr=False)
    web_search_api_url: str = "https://api.tavily.com"
    web_search_timeout_seconds: float = 8.0
    web_search_max_results: int = 5
    max_tool_executions: int = 4
    max_web_searches: int = 2

    def __post_init__(self) -> None:
        if self.calculator_enabled:
            raise ValueError("Calculator settings remain disabled until the Phase 2 gate passes.")

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "PipelineSettings":
        mode = _enum(values, "AVA_PIPELINE_MODE", "real", {"real", "mock"})
        llm_model = _text(values, "AVA_LLM_MODEL", DEFAULT_LLM_MODEL)
        if llm_model not in ALLOWED_MODELS:
            raise ValueError("AVA_LLM_MODEL is not in the supported model allowlist.")
        web_provider = _enum(
            values, "AVA_WEB_SEARCH_PROVIDER", "disabled", {"disabled", "tavily"}
        )
        web_enabled = _boolean(values, "AVA_WEB_SEARCH_ENABLED", False)
        web_key = _optional(values, "TAVILY_API_KEY")
        web_url = _text(values, "TAVILY_API_URL", "https://api.tavily.com")
        maximum_tools = _integer(values, "AVA_MAX_TOOL_EXECUTIONS", 4)
        maximum_searches = _integer(values, "AVA_MAX_WEB_SEARCHES", 2)
        if maximum_searches > maximum_tools:
            raise ValueError("AVA_MAX_WEB_SEARCHES cannot exceed AVA_MAX_TOOL_EXECUTIONS.")
        if web_enabled and (web_provider != "tavily" or not web_key):
            raise ValueError(
                "Enabled web search requires AVA_WEB_SEARCH_PROVIDER=tavily and "
                "TAVILY_API_KEY."
            )
        web_timeout = _number(values, "AVA_WEB_SEARCH_TIMEOUT_SECONDS", 8)
        if web_timeout > 30:
            raise ValueError("AVA_WEB_SEARCH_TIMEOUT_SECONDS must be at most 30.")
        web_results = _integer(values, "AVA_WEB_SEARCH_MAX_RESULTS", 5)
        if web_results > 10:
            raise ValueError("AVA_WEB_SEARCH_MAX_RESULTS must be at most 10.")
        return cls(
            mode=mode,
            model_device=_text(values, "AVA_MODEL_DEVICE", "cpu"),
            llm_model=llm_model,
            llm_streaming=_boolean(values, "AVA_LLM_STREAMING", True),
            context_window_tokens=_integer(
                values, "AVA_LLM_CONTEXT_WINDOW_TOKENS", 32_768
            ),
            reserved_output_tokens=_integer(
                values, "AVA_LLM_RESERVED_OUTPUT_TOKENS", 4_096
            ),
            observability_retention_days=_integer(
                values, "AVA_OBSERVABILITY_RETENTION_DAYS", 30
            ),
            qdrant_mode=_enum(
                values,
                "AVA_QDRANT_MODE",
                "disabled",
                {"disabled", "shadow", "primary"},
            ),
            qdrant_url=_text(values, "QDRANT_URL", "http://127.0.0.1:6333"),
            qdrant_api_key=_optional(values, "QDRANT_API_KEY"),
            qdrant_collection_alias=_text(
                values, "QDRANT_COLLECTION_ALIAS", "ava_filing_chunks_current"
            ),
            qdrant_local_path=_text(values, "QDRANT_LOCAL_PATH") or None,
            qdrant_timeout_seconds=_integer(values, "QDRANT_TIMEOUT_SECONDS", 30),
            request_routing_enabled=_boolean(
                values, "AVA_REQUEST_ROUTING_ENABLED", True
            ),
            # Phase 1 keeps every deployment path fail-closed. Phase 2 may
            # promote this only after the route and false-positive gates pass.
            calculator_enabled=False,
            web_search_enabled=web_enabled,
            web_search_provider=web_provider,
            web_search_api_key=web_key,
            web_search_api_url=web_url,
            web_search_timeout_seconds=web_timeout,
            web_search_max_results=web_results,
            max_tool_executions=maximum_tools,
            max_web_searches=maximum_searches,
        )

    @classmethod
    def from_environment(cls) -> "PipelineSettings":
        load_project_environment()
        return cls.from_mapping(os.environ)


@dataclass(frozen=True)
class ConversationSettings:
    mode: str = "disabled"
    postgres_dsn: str | None = field(default=None, repr=False)
    tenant_id: str | None = None
    user_id: str | None = None
    single_user_boundary_acknowledged: bool = False
    recent_token_budget: int = 2_048
    summary_token_budget: int = 768
    long_term_token_budget: int = 512
    long_term_candidate_k: int = 5
    long_term_score_threshold: float = 0.55
    retention_days: int = 90
    long_term_store: str = "disabled"

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "ConversationSettings":
        settings = cls(
            mode=_enum(
                values,
                "AVA_CONVERSATION_MODE",
                "disabled",
                {"disabled", "single_user", "oidc"},
            ),
            postgres_dsn=_optional(values, "AVA_POSTGRES_DSN"),
            tenant_id=_optional(values, "AVA_TENANT_ID"),
            user_id=_optional(values, "AVA_USER_ID"),
            single_user_boundary_acknowledged=_boolean(
                values, "AVA_SINGLE_USER_BOUNDARY_ACKNOWLEDGED", False
            ),
            recent_token_budget=_integer(
                values, "AVA_SHORT_TERM_TOKEN_BUDGET", 2_048
            ),
            summary_token_budget=_integer(values, "AVA_SUMMARY_TOKEN_BUDGET", 768),
            long_term_token_budget=_integer(
                values, "AVA_LONG_TERM_TOKEN_BUDGET", 512
            ),
            long_term_candidate_k=_integer(
                values, "AVA_LONG_TERM_CANDIDATE_K", 5
            ),
            long_term_score_threshold=float(
                values.get("AVA_LONG_TERM_SCORE_THRESHOLD", "0.55")
            ),
            retention_days=_integer(
                values, "AVA_CONVERSATION_RETENTION_DAYS", 90
            ),
            long_term_store=_enum(
                values,
                "AVA_LONG_TERM_MEMORY_STORE",
                "disabled",
                {"disabled", "qdrant"},
            ),
        )
        settings.validate()
        return settings

    @classmethod
    def from_environment(cls) -> "ConversationSettings":
        load_project_environment()
        return cls.from_mapping(os.environ)

    def validate(self) -> None:
        values = (
            self.recent_token_budget,
            self.summary_token_budget,
            self.long_term_token_budget,
            self.long_term_candidate_k,
            self.retention_days,
        )
        if any(value <= 0 for value in values):
            raise ValueError("Conversation budgets and retention must be positive.")
        if not 0 <= self.long_term_score_threshold <= 1:
            raise ValueError("AVA_LONG_TERM_SCORE_THRESHOLD must be between zero and one.")
        if self.mode == "single_user" and (
            not self.postgres_dsn
            or not self.tenant_id
            or not self.user_id
            or not self.single_user_boundary_acknowledged
        ):
            raise ValueError(
                "Single-user history requires AVA_POSTGRES_DSN, AVA_TENANT_ID, "
                "AVA_USER_ID, and AVA_SINGLE_USER_BOUNDARY_ACKNOWLEDGED=true."
            )
        if self.mode == "oidc" and not self.postgres_dsn:
            raise ValueError("OIDC conversation history requires AVA_POSTGRES_DSN.")


@dataclass(frozen=True)
class DocumentSettings:
    enabled: bool = False
    asset_root: Path = Path("data/private/uploads")

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "DocumentSettings":
        return cls(
            enabled=_boolean(values, "AVA_UPLOADS_ENABLED", False),
            asset_root=Path(
                values.get("AVA_UPLOAD_STORE_PATH", "data/private/uploads")
            ).expanduser().resolve(),
        )

    @classmethod
    def from_environment(cls) -> "DocumentSettings":
        load_project_environment()
        return cls.from_mapping(os.environ)


@dataclass(frozen=True)
class OperationalSettings:
    maximum_body_bytes: int = 16_384
    maximum_upload_bytes: int = 20 * 1024 * 1024
    requests_per_minute: int = 60
    stream_timeout_seconds: int = 180

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "OperationalSettings":
        return cls(
            maximum_body_bytes=_integer(values, "AVA_MAX_BODY_BYTES", 16_384),
            maximum_upload_bytes=_integer(
                values, "AVA_UPLOAD_MAX_BODY_BYTES", 20 * 1024 * 1024
            ),
            requests_per_minute=_integer(values, "AVA_REQUESTS_PER_MINUTE", 60),
            stream_timeout_seconds=_integer(
                values, "AVA_STREAM_TIMEOUT_SECONDS", 180
            ),
        )

    @classmethod
    def from_environment(cls) -> "OperationalSettings":
        load_project_environment()
        return cls.from_mapping(os.environ)


@dataclass(frozen=True)
class AuthSettings:
    issuer: str = ""
    client_id: str = ""
    client_secret: str | None = field(default=None, repr=False)
    redirect_uri: str = ""
    fixed_tenant_id: str | None = None
    tenant_claim: str | None = None
    algorithms: tuple[str, ...] = ("RS256",)
    scopes: tuple[str, ...] = ("openid", "profile")
    discovery_timeout_seconds: float = 5.0
    clock_skew_seconds: int = 30
    allow_insecure_http: bool = False
    cookie_name: str = "ava_session"
    csrf_cookie_name: str = "ava_csrf"
    login_ttl_seconds: int = 300
    session_ttl_seconds: int = 28_800
    cookie_secure: bool = True
    cookie_same_site: str = "lax"

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "AuthSettings":
        same_site = _enum(
            values, "AVA_AUTH_COOKIE_SAME_SITE", "lax", {"lax", "strict"}
        )
        return cls(
            issuer=_text(values, "AVA_OIDC_ISSUER").rstrip("/"),
            client_id=_text(values, "AVA_OIDC_CLIENT_ID"),
            client_secret=_optional(values, "AVA_OIDC_CLIENT_SECRET"),
            redirect_uri=_text(values, "AVA_OIDC_REDIRECT_URI"),
            fixed_tenant_id=_optional(values, "AVA_OIDC_TENANT_ID"),
            tenant_claim=_optional(values, "AVA_OIDC_TENANT_CLAIM"),
            algorithms=tuple(
                item.strip()
                for item in values.get("AVA_OIDC_ALGORITHMS", "RS256").split(",")
                if item.strip()
            ),
            scopes=tuple(
                item.strip()
                for item in values.get("AVA_OIDC_SCOPES", "openid profile").split()
                if item.strip()
            ),
            discovery_timeout_seconds=_number(
                values, "AVA_OIDC_DISCOVERY_TIMEOUT_SECONDS", 5
            ),
            clock_skew_seconds=_integer(
                values, "AVA_OIDC_CLOCK_SKEW_SECONDS", 30, minimum=0
            ),
            allow_insecure_http=_boolean(
                values, "AVA_OIDC_ALLOW_INSECURE_HTTP", False
            ),
            cookie_name=_text(values, "AVA_AUTH_COOKIE_NAME", "ava_session"),
            csrf_cookie_name=_text(values, "AVA_AUTH_CSRF_COOKIE_NAME", "ava_csrf"),
            login_ttl_seconds=_integer(values, "AVA_AUTH_LOGIN_TTL_SECONDS", 300),
            session_ttl_seconds=_integer(
                values, "AVA_AUTH_SESSION_TTL_SECONDS", 28_800
            ),
            cookie_secure=_boolean(values, "AVA_AUTH_COOKIE_SECURE", True),
            cookie_same_site=same_site,
        )

    def validate(self, *, required: bool) -> None:
        if not required:
            return
        if not self.issuer or not self.client_id or not self.redirect_uri:
            raise ValueError(
                "OIDC requires AVA_OIDC_ISSUER, AVA_OIDC_CLIENT_ID, and "
                "AVA_OIDC_REDIRECT_URI."
            )
        issuer = urlparse(self.issuer)
        redirect = urlparse(self.redirect_uri)
        loopback = issuer.hostname in {"localhost", "127.0.0.1", "::1"}
        if issuer.scheme != "https" and not (self.allow_insecure_http and loopback):
            raise ValueError("AVA_OIDC_ISSUER must use HTTPS outside loopback development.")
        if redirect.scheme not in {"https", "http"} or not redirect.netloc:
            raise ValueError("AVA_OIDC_REDIRECT_URI must be an absolute HTTP(S) URL.")
        if bool(self.fixed_tenant_id) == bool(self.tenant_claim):
            raise ValueError(
                "Configure exactly one of AVA_OIDC_TENANT_ID or AVA_OIDC_TENANT_CLAIM."
            )
        if not self.algorithms or any(item.casefold() == "none" for item in self.algorithms):
            raise ValueError("AVA_OIDC_ALGORITHMS must contain approved signed algorithms.")
        if "openid" not in self.scopes:
            raise ValueError("AVA_OIDC_SCOPES must include openid.")


@dataclass(frozen=True)
class UISettings:
    query_max_length: int = 4_000
    cors_origins: tuple[str, ...] = ("http://localhost:5173",)
    available_models: tuple[str, ...] = ALLOWED_MODELS
    default_model: str = DEFAULT_LLM_MODEL

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "UISettings":
        origins = tuple(
            item.strip()
            for item in values.get("AVA_CORS_ORIGINS", "http://localhost:5173").split(",")
            if item.strip()
        )
        if not origins:
            raise ValueError("AVA_CORS_ORIGINS must include at least one origin.")
        return cls(
            query_max_length=_integer(values, "AVA_QUERY_MAX_LENGTH", 4_000),
            cors_origins=origins,
            default_model=_text(values, "AVA_LLM_MODEL", DEFAULT_LLM_MODEL),
        )


@dataclass(frozen=True)
class LoggingSettings:
    json_logs: bool = False
    level: str = "INFO"

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "LoggingSettings":
        level = _text(values, "AVA_LOG_LEVEL", "INFO").upper()
        if level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ValueError("AVA_LOG_LEVEL is not a supported logging level.")
        return cls(
            json_logs=_boolean(values, "AVA_JSON_LOGS", False),
            level=level,
        )


@dataclass(frozen=True)
class ApplicationSettings:
    pipeline: PipelineSettings = field(default_factory=lambda: PipelineSettings(mode="mock"))
    provider: ProviderSettings = field(default_factory=ProviderSettings)
    conversation: ConversationSettings = field(default_factory=ConversationSettings)
    documents: DocumentSettings = field(default_factory=DocumentSettings)
    operations: OperationalSettings = field(default_factory=OperationalSettings)
    auth: AuthSettings = field(default_factory=AuthSettings)
    ui: UISettings = field(default_factory=UISettings)
    logging: LoggingSettings = field(default_factory=LoggingSettings)

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "ApplicationSettings":
        pipeline = PipelineSettings.from_mapping(values)
        provider = ProviderSettings.from_mapping(values)
        provider.validate(required=pipeline.mode == "real")
        auth = AuthSettings.from_mapping(values)
        conversation = ConversationSettings.from_mapping(values)
        auth.validate(required=conversation.mode == "oidc")
        settings = cls(
            pipeline=pipeline,
            provider=provider,
            conversation=conversation,
            documents=DocumentSettings.from_mapping(values),
            operations=OperationalSettings.from_mapping(values),
            auth=auth,
            ui=UISettings.from_mapping(values),
            logging=LoggingSettings.from_mapping(values),
        )
        if settings.conversation.mode == "oidc" and "*" in settings.ui.cors_origins:
            raise ValueError("OIDC deployments require explicit AVA_CORS_ORIGINS.")
        return settings

    @classmethod
    def from_environment(
        cls, project_root: Path = PROJECT_ROOT
    ) -> "ApplicationSettings":
        load_project_environment(project_root)
        return cls.from_mapping(os.environ)

    @classmethod
    def for_tests(cls, **values: str) -> "ApplicationSettings":
        environment = {
            "AVA_PIPELINE_MODE": "mock",
            "AVA_CONVERSATION_MODE": "disabled",
            "AVA_LONG_TERM_MEMORY_STORE": "disabled",
            "AVA_UPLOADS_ENABLED": "false",
            "AVA_REQUEST_ROUTING_ENABLED": "true",
            "AVA_WEB_SEARCH_ENABLED": "false",
            "AVA_WEB_SEARCH_PROVIDER": "disabled",
            "AVA_CORS_ORIGINS": "http://testserver",
            **values,
        }
        return cls.from_mapping(environment)
