from __future__ import annotations

import csv
import sys
from pathlib import Path
from urllib.parse import urlparse

from tender_agent.config import Settings
from tender_agent.tenderplan_pipeline import TenderplanPipeline


def main() -> None:
    if len(sys.argv) > 2:
        raise SystemExit("Usage: .venv/bin/python prepare_tenderplan_batch.py [output_dir]")

    base_dir = Path(__file__).resolve().parent
    settings = Settings.load(base_dir)
    pipeline = TenderplanPipeline(settings)
    candidates = pipeline.fetch_candidates()
    candidate_urls = _hydrate_candidate_urls(pipeline, candidates)

    output_root = (
        Path(sys.argv[1]).expanduser().resolve()
        if len(sys.argv) == 2
        else base_dir / "manual_downloads" / "tenderplan_candidates"
    )
    output_root.mkdir(parents=True, exist_ok=True)

    manifest_rows: list[dict[str, str]] = []
    used_names: set[str] = set()
    for index, candidate in enumerate(candidates, start=1):
        base_name = f"{index:03d}. {_sanitize_folder_name(candidate.title)}"
        folder_name = _make_unique_folder_name(base_name, used_names)
        (output_root / folder_name).mkdir(exist_ok=True)
        manifest_rows.append(
            {
                "order": str(index),
                "tender_id": candidate.tender_id,
                "title": candidate.title,
                "folder_name": folder_name,
                "deadline_at": candidate.deadline_at.strftime("%Y-%m-%d %H:%M") if candidate.deadline_at else "",
                "url": candidate_urls.get(candidate.tender_id, candidate.url),
                "source": "tenderplan",
                "download_mode": _detect_download_mode(candidate_urls.get(candidate.tender_id, candidate.url)),
                "download_strategy": _detect_download_strategy(candidate_urls.get(candidate.tender_id, candidate.url)),
                "platform_host": _extract_host(candidate_urls.get(candidate.tender_id, candidate.url)),
            }
        )

    manifest_path = output_root / "_manifest.csv"
    with manifest_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "order",
                "tender_id",
                "title",
                "folder_name",
                "deadline_at",
                "url",
                "source",
                "download_mode",
                "download_strategy",
                "platform_host",
            ],
        )
        writer.writeheader()
        writer.writerows(manifest_rows)

    print(f"Created {len(manifest_rows)} Tenderplan folders in {output_root}")
    print(f"Manifest: {manifest_path}")


def _hydrate_candidate_urls(
    pipeline: TenderplanPipeline,
    candidates: list,
) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for candidate in candidates:
        if candidate.url:
            resolved[candidate.tender_id] = candidate.url
            continue
        try:
            payload = pipeline.client.request_json(
                "/api/tenders/v2/fullinfo",
                query={"id": candidate.tender_id},
            )
        except Exception:
            resolved[candidate.tender_id] = ""
            continue
        resolved[candidate.tender_id] = _extract_url_from_fullinfo(payload)
    return resolved


def _extract_url_from_fullinfo(payload: object) -> str:
    if isinstance(payload, dict):
        for key in ("href", "url", "sourceUrl", "externalUrl"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for key in ("tender", "data", "result"):
            nested = payload.get(key)
            if nested is not None:
                nested_url = _extract_url_from_fullinfo(nested)
                if nested_url:
                    return nested_url
    if isinstance(payload, list):
        for item in payload:
            nested_url = _extract_url_from_fullinfo(item)
            if nested_url:
                return nested_url
    return ""


def _detect_download_mode(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "pro.myseldon.com" in host or "myseldon.com" in host:
        return "seldon"
    if host:
        return "public"
    return "unknown"


def _detect_download_strategy(url: str) -> str:
    host = _extract_host(url)
    if not host:
        return "manual"
    if "pro.myseldon.com" in host or "myseldon.com" in host:
        return "supported_platform"

    stable_supported_hosts = (
        "utp.sberbank-ast.ru",
        "sberbank-ast.ru",
    )
    if any(token in host for token in stable_supported_hosts):
        return "supported_platform"

    captcha_or_login_hosts = (
        "agregatoreat.ru",
        "www.b2b-center.ru",
        "b2b-center.ru",
    )
    if any(token in host for token in captcha_or_login_hosts):
        return "manual"

    network_blocked_hosts = (
        "zakupki.gov.ru",
        "zakupki.mos.ru",
        "zakupki.rosatom.ru",
        "223.rts-tender.ru",
        "www.rts-tender.ru",
        "rts-tender.ru",
        "market-lk.rts-tender.ru",
        "market.rts-tender.ru",
        "www.roseltorg.ru",
        "roseltorg.ru",
        "tender.lot-online.ru",
        "lot-online.ru",
    )
    if any(token in host for token in network_blocked_hosts):
        return "network_blocked"

    direct_hosts = (
        "tender.pro",
        "www.tender.pro",
        "www.sibur.ru",
        "zakupki.rostelecom.ru",
        "zakupki.lsr.ru",
        "zakupki.tmk-group.com",
        "tenders.mts.ru",
        "www.uralchem.ru",
        "suppliers.severstal.com",
        "university.nornik.ru",
        "etp.metal-it.ru",
        "tender.otc.ru",
        "webtorgi.samregion.ru",
        "torgi.etp-region.ru",
        "etprf.ru",
        "zakupki.lenreg.ru",
        "bidzaar.com",
        "www.fabrikant.ru",
        "fabrikant.ru",
        "tektorg.ru",
        "etpgpb.ru",
        "gos.etpgpb.ru",
        "workspace.ru",
    )
    if any(token in host for token in direct_hosts):
        return "direct"

    return "manual"


def _extract_host(url: str) -> str:
    return urlparse(url).netloc.lower()


def _sanitize_folder_name(value: str) -> str:
    sanitized = value.replace("/", "／").replace("\0", "").strip().rstrip(". ")
    sanitized = _truncate_utf8(sanitized or "Без названия", max_bytes=180)
    return sanitized or "Без названия"


def _make_unique_folder_name(name: str, used_names: set[str]) -> str:
    candidate = name
    suffix = 2
    while candidate in used_names:
        candidate = f"{name} ({suffix})"
        suffix += 1
    used_names.add(candidate)
    return candidate


def _truncate_utf8(value: str, max_bytes: int) -> str:
    raw = value.encode("utf-8")
    if len(raw) <= max_bytes:
        return value

    ellipsis = "..."
    limit = max_bytes - len(ellipsis.encode("utf-8"))
    trimmed = raw[:limit]
    while trimmed:
        try:
            return trimmed.decode("utf-8").rstrip() + ellipsis
        except UnicodeDecodeError:
            trimmed = trimmed[:-1]
    return ellipsis


if __name__ == "__main__":
    main()
