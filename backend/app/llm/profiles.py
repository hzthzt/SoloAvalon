from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)


ReasoningEffort = Literal[
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
]
REASONING_EFFORTS: tuple[ReasoningEffort, ...] = (
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)
NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
_http_url = TypeAdapter(HttpUrl)


def mask_api_key(api_key: str) -> str:
    if not api_key:
        return ""
    visible_prefix_length = 4
    visible_suffix_length = 4
    min_partial_mask_length = 16
    if len(api_key) < min_partial_mask_length:
        return "*" * len(api_key)
    return (
        f"{api_key[:visible_prefix_length]}"
        "********"
        f"{api_key[-visible_suffix_length:]}"
    )


def validate_base_url(base_url: str) -> None:
    try:
        _http_url.validate_python(base_url)
    except ValidationError as exc:
        raise ValueError("base_url must start with http:// or https://") from exc


class LlmProfileInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", hide_input_in_errors=True)

    name: NonEmptyString
    base_url: str
    api_key: str = Field(repr=False)
    model: NonEmptyString
    reasoning_effort: ReasoningEffort = "medium"
    timeout: float = Field(gt=0, allow_inf_nan=False)
    timeout_retries: int = Field(default=5, ge=0)

    @field_validator("base_url")
    @classmethod
    def strip_base_url(cls, value: str) -> str:
        return value.strip()

    @field_validator("reasoning_effort", mode="before")
    @classmethod
    def normalize_reasoning_effort(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_connection(self) -> "LlmProfileInput":
        # Unconfigured is an internal placeholder, never a usable connection.
        if getattr(self, "id", None) != "unconfigured":
            validate_base_url(self.base_url)
            if not self.api_key.strip():
                raise ValueError("api_key is required")
        return self


class LlmProfile(LlmProfileInput):
    id: str = Field(min_length=1)
    created_at: str
    updated_at: str

    def to_public_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude={"api_key"}) | {
            "api_key_masked": mask_api_key(self.api_key),
        }


class LlmProfileConfig(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    profiles: list[LlmProfile]
