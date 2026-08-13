from __future__ import annotations

ARTIFACT_SCHEMA_VERSION = 3
FACTS_VERSION = "2026-04-16-facts-v1"
RENDERER_VERSION = "2026-04-16-renderer-v1"
QUALITY_GATES_VERSION = "2026-04-16-quality-v1"
REASON_CODES_VERSION = "2026-04-16-reasons-v1"


def build_versions_payload(*, policy_sha1: str | None = None) -> dict[str, str]:
    payload = {
        "facts_version": FACTS_VERSION,
        "renderer_version": RENDERER_VERSION,
        "quality_gates_version": QUALITY_GATES_VERSION,
        "reason_codes_version": REASON_CODES_VERSION,
    }
    if policy_sha1:
        payload["policy_sha1"] = str(policy_sha1)
    return payload
