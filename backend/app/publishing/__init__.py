"""Publishing prep boundary (M10).

Local-only publishing preparation: Guard-gated prep pipeline plus a
credential-gated platform adapter interface. Nothing here performs an
external publish without an explicitly configured platform credential, and
every prep run stays behind the Guard publish gate (Constitution article 3).
"""
from __future__ import annotations

from .contracts import (
    MockPublishAdapter,
    PublishAdapter,
    PublishAdapterRegistry,
    PublishError,
)
from .prep import (
    PREP_STEP_NAMES,
    PrepStepResult,
    PublishPrepPipeline,
    PublishPrepResult,
)
from .youtube_adapter import (
    ADAPTER_VERSION as YOUTUBE_ADAPTER_VERSION,
    DEFAULT_CREDENTIAL_REF as YOUTUBE_DEFAULT_CREDENTIAL_REF,
    PLATFORM_ID as YOUTUBE_PLATFORM_ID,
    YouTubePublishAdapter,
)

__all__ = [
    "MockPublishAdapter",
    "PREP_STEP_NAMES",
    "PrepStepResult",
    "PublishAdapter",
    "PublishAdapterRegistry",
    "PublishError",
    "PublishPrepPipeline",
    "PublishPrepResult",
    "YOUTUBE_ADAPTER_VERSION",
    "YOUTUBE_DEFAULT_CREDENTIAL_REF",
    "YOUTUBE_PLATFORM_ID",
    "YouTubePublishAdapter",
]
