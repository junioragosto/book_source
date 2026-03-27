from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fontTools.ttLib import TTCollection, TTFont
from PIL import Image, ImageDraw, ImageFont

from cdn_manifest_lib import build_limit_flags, build_limit_summary, get_git_context, load_source_config, resolve_sources

FONT_EXTENSIONS = {".ttf", ".otf", ".ttc", ".woff", ".woff2"}
THUMB_SIZE = (640, 320)
THUMB_BACKGROUND = (250, 250, 250)
THUMB_BORDER = (221, 221, 221)
THUMB_FOREGROUND = (28, 28, 28)
THUMB_MUTED = (110, 110, 110)
FORMAT_PRIORITY = {
    ".woff2": 0,
    ".woff": 1,
    ".otf": 2,
    ".ttf": 3,
    ".ttc": 4,
}
README_SECTION_START = "<!-- FONT_COMMERCIAL_STATUS:START -->"
README_SECTION_END = "<!-- FONT_COMMERCIAL_STATUS:END -->"
README_STATUS_ORDER = [
    "restricted",
    "copyleft-review",
    "custom-review",
    "unknown",
    "opensource-review",
]
COMMERCIAL_USE_PROFILES = {
    "restricted": {
        "status": "not-recommended",
        "label": "Not recommended for commercial use",
        "note": "Embedded metadata points to Apple, Microsoft, vendor, or system-font restrictions.",
        "label_zh": "不建议商用（受限）",
        "note_zh": "字体内嵌元数据指向 Apple、Microsoft、厂商或系统字体限制，不建议直接用于商业分发。",
    },
    "copyleft-review": {
        "status": "review-copyleft",
        "label": "Review before commercial use",
        "note": "Embedded metadata points to GPL-style copyleft terms; bundling and redistribution obligations need review.",
        "label_zh": "商用前需复核（Copyleft）",
        "note_zh": "字体内嵌元数据包含 GPL 类 Copyleft 提示，商用前需检查打包、再分发和附带义务。",
    },
    "custom-review": {
        "status": "review-custom",
        "label": "Manual review required",
        "note": "A custom or non-standard license clue was detected and should be checked manually.",
        "label_zh": "需人工复核（自定义许可）",
        "note_zh": "检测到自定义或非标准许可提示，需要人工查原始协议后再判断能否商用。",
    },
    "unknown": {
        "status": "unknown",
        "label": "Unknown, not treated as commercial-safe",
        "note": "No embedded license metadata was found, so this repo does not treat the font as commercial-safe by default.",
        "label_zh": "未知，暂不视为可商用",
        "note_zh": "未检出可靠的内嵌许可信息，本仓库默认不将其视为商用安全字体。",
    },
    "opensource-review": {
        "status": "likely-allowed",
        "label": "Likely commercial-friendly",
        "note": "Embedded metadata points to OFL, Apache, or Arphic-style open font licenses. Keep required notices when distributing.",
        "label_zh": "倾向可商用（开放许可）",
        "note_zh": "字体内嵌元数据指向 OFL、Apache 或 Arphic 等开放字体许可，但分发时仍需保留要求的许可说明。",
    },
}


@dataclass
class FontRecord:
    path: Path
    sha256: str
    size_bytes: int
    extension: str
    metadata: dict[str, Any]
    commercial_use: dict[str, str]
    thumbnail_path: str
    thumbnail_render_status: str
    limit_flags: dict[str, Any]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build font catalog, cleanup report, and thumbnails.")
    parser.add_argument("--font-root", default="Base_Fonts")
    parser.add_argument("--thumb-root", default="Base_Font_Thumbs")
    parser.add_argument("--config-path", default="cdn-sources.json")
    parser.add_argument("--manifest-path", default="font-catalog.json")
    parser.add_argument("--report-path", default="font-cleanup.md")
    parser.add_argument("--readme-path", default="README.md")
    parser.add_argument("--sample-text", default="\u6c38\u548cABC123")
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def get_name(name_table: Any, name_id: int) -> str:
    preferred = [3, 1, 0]
    for platform_id in preferred:
        for record in name_table.names:
            if record.nameID != name_id or record.platformID != platform_id:
                continue
            try:
                value = record.toUnicode().strip()
            except Exception:
                continue
            if value:
                return value
    for record in name_table.names:
        if record.nameID != name_id:
            continue
        try:
            value = record.toUnicode().strip()
        except Exception:
            continue
        if value:
            return value
    return ""


def classify_license(description: str, url: str) -> str:
    text = f"{description} {url}".casefold().strip()
    if not text:
        return "unknown"
    if "apple" in text or "microsoft" in text or "supplied font" in text:
        return "restricted"
    if "gpl" in text or "gnu general public license" in text or "gnu public license" in text:
        return "copyleft-review"
    if "open font license" in text or "apache license" in text or "arphic public license" in text:
        return "opensource-review"
    return "custom-review"


def build_commercial_use(license_status: str) -> dict[str, str]:
    profile = COMMERCIAL_USE_PROFILES.get(license_status, COMMERCIAL_USE_PROFILES["unknown"])
    return {
        "status": profile["status"],
        "label": profile["label"],
        "note": profile["note"],
        "labelZh": profile["label_zh"],
        "noteZh": profile["note_zh"],
        "sourceStatus": license_status,
    }


def open_font(path: Path) -> tuple[Any, int]:
    if path.suffix.lower() == ".ttc":
        collection = TTCollection(str(path))
        return collection, len(collection.fonts)
    font = TTFont(str(path), lazy=True)
    return font, 1


def inspect_font(path: Path) -> dict[str, Any]:
    container, font_count = open_font(path)
    font = container.fonts[0] if isinstance(container, TTCollection) else container
    try:
        name_table = font["name"]
        license_description = get_name(name_table, 13)
        license_url = get_name(name_table, 14)
        metadata = {
            "family": get_name(name_table, 1),
            "subfamily": get_name(name_table, 2),
            "fullName": get_name(name_table, 4),
            "postscriptName": get_name(name_table, 6),
            "typographicFamily": get_name(name_table, 16),
            "typographicSubfamily": get_name(name_table, 17),
            "designer": get_name(name_table, 9),
            "manufacturer": get_name(name_table, 8),
            "licenseDescription": license_description,
            "licenseUrl": license_url,
            "licenseStatus": classify_license(license_description, license_url),
            "fontCount": font_count,
        }
        try:
            metadata["glyphCount"] = len(font.getGlyphOrder())
        except Exception:
            metadata["glyphCount"] = 0
        return metadata
    finally:
        container.close()


def slugify_path(path: Path) -> str:
    text = str(path).replace("\\", "/")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-")
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
    return f"{safe[:120]}-{digest}".strip("-")


def fit_font(font_path: Path, text: str, max_width: int, max_height: int, preferred_size: int, *, allow_font_fallback: bool = True) -> tuple[Any, str]:
    size = preferred_size
    while size >= 14:
        try:
            if font_path.suffix.lower() == ".ttc":
                font = ImageFont.truetype(str(font_path), size=size, index=0)
            else:
                font = ImageFont.truetype(str(font_path), size=size)
        except Exception:
            if not allow_font_fallback:
                break
            return ImageFont.load_default(), "fallback"
        left, top, right, bottom = font.getbbox(text)
        if (right - left) <= max_width and (bottom - top) <= max_height:
            return font, "native"
        size -= 4
    if allow_font_fallback:
        return ImageFont.load_default(), "fallback"
    raise RuntimeError(f"Unable to size text '{text}' for {font_path}")


def draw_text(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str, font: Any, fill: tuple[int, int, int]) -> None:
    left, top, right, bottom = box
    text_left, text_top, text_right, text_bottom = draw.textbbox((0, 0), text, font=font)
    width = text_right - text_left
    height = text_bottom - text_top
    x = left + max(0, (right - left - width) // 2)
    y = top + max(0, (bottom - top - height) // 2)
    draw.text((x, y), text, font=font, fill=fill)


def build_thumbnail(font_path: Path, family_label: str, sample_text: str, output_path: Path) -> str:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", THUMB_SIZE, THUMB_BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, THUMB_SIZE[0] - 1, THUMB_SIZE[1] - 1), radius=18, outline=THUMB_BORDER, width=2)

    sample_font, sample_status = fit_font(font_path, sample_text, THUMB_SIZE[0] - 80, 120, 92)
    label_font, _ = fit_font(font_path, family_label[:48], THUMB_SIZE[0] - 80, 56, 34, allow_font_fallback=True)
    meta_font = ImageFont.load_default()

    draw_text(draw, (40, 36, THUMB_SIZE[0] - 40, 170), sample_text, sample_font, THUMB_FOREGROUND)
    draw_text(draw, (40, 186, THUMB_SIZE[0] - 40, 244), family_label[:48], label_font, THUMB_MUTED)
    draw_text(draw, (40, 250, THUMB_SIZE[0] - 40, 292), font_path.name[:64], meta_font, THUMB_MUTED)

    image.save(output_path, format="PNG", optimize=True)
    return sample_status


def normalize_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def collapse_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def shorten_text(value: str, limit: int) -> str:
    compact = collapse_whitespace(value)
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)].rstrip() + "…"


def escape_markdown_cell(value: str) -> str:
    compact = collapse_whitespace(value)
    if not compact:
        return "-"
    return compact.replace("|", "\\|")


def build_font_display_name(record: FontRecord) -> str:
    return (
        record.metadata.get("fullName")
        or record.metadata.get("family")
        or record.metadata.get("typographicFamily")
        or record.path.stem
    )


def build_license_clue(metadata: dict[str, Any]) -> str:
    description = shorten_text(metadata.get("licenseDescription", ""), 140)
    license_url = collapse_whitespace(metadata.get("licenseUrl", ""))
    parts = []
    if description:
        parts.append(description)
    if license_url:
        parts.append(license_url)
    if not parts:
        parts.append("无内嵌许可信息")
    return escape_markdown_cell(" ; ".join(parts))


def choose_canonical(paths: list[Path]) -> Path:
    def key(path: Path) -> tuple[int, int, str]:
        relative = str(path).replace("\\", "/")
        return (FORMAT_PRIORITY.get(path.suffix.lower(), 99), len(relative), relative)

    return sorted(paths, key=key)[0]


def build_readme_font_status_section(records: list[FontRecord], generated_at: str) -> str:
    lines = [
        "### 字体授权概览",
        "",
        "以下结果由构建脚本根据字体内嵌许可信息汇总生成，用于工程筛查。",
        "单个字体的详细许可字段请查看 `font-catalog.json` 中的 `fonts[].commercialUse` 和 `fonts[].metadata`。",
        "",
        "生产建议：",
        "",
        "- `restricted` 不进入正式素材库。",
        "- `unknown` 需要补充来源和授权证明后再使用。",
        "- 需要做严格筛选时，直接基于 `font-catalog.json` 过滤，不建议手工维护名单。",
        "",
        f"生成时间：`{generated_at}`",
        "",
        "当前统计：",
        "",
    ]

    status_groups: dict[str, list[FontRecord]] = {key: [] for key in README_STATUS_ORDER}
    for record in sorted(records, key=lambda item: str(item.path).casefold()):
        source_status = record.commercial_use.get("sourceStatus", "unknown")
        status_groups.setdefault(source_status, []).append(record)

    for key in README_STATUS_ORDER:
        profile = COMMERCIAL_USE_PROFILES[key]
        lines.append(f"- `{profile['label_zh']}`：{len(status_groups.get(key, []))}。{profile['note_zh']}")

    return "\n".join(lines).rstrip()


def sync_readme_font_status(readme_path: Path, section_markdown: str) -> bool:
    if not readme_path.exists():
        return False

    original = readme_path.read_text(encoding="utf-8")
    replacement = f"{README_SECTION_START}\n{section_markdown}\n{README_SECTION_END}"
    pattern = re.compile(rf"{re.escape(README_SECTION_START)}.*?{re.escape(README_SECTION_END)}", re.DOTALL)

    if pattern.search(original):
        updated = pattern.sub(lambda _: replacement, original, count=1)
    else:
        suffix = "" if original.endswith("\n") else "\n"
        updated = (
            f"{original}{suffix}\n## 授权说明\n\n"
            "字体是否可商用需要逐个字体判断，不能按整个仓库一概而论。\n\n"
            f"{replacement}\n"
        )

    if updated == original:
        return False

    readme_path.write_text(updated, encoding="utf-8")
    return True


def build_exact_duplicate_groups(records: list[FontRecord]) -> list[dict[str, Any]]:
    groups: dict[str, list[FontRecord]] = defaultdict(list)
    for record in records:
        groups[record.sha256].append(record)

    report: list[dict[str, Any]] = []
    for digest, members in groups.items():
        if len(members) < 2:
            continue
        canonical = choose_canonical([member.path for member in members])
        report.append(
            {
                "sha256": digest,
                "canonical": str(canonical).replace("\\", "/"),
                "duplicates": [str(member.path).replace("\\", "/") for member in sorted(members, key=lambda item: str(item.path))],
                "sizeBytes": members[0].size_bytes,
            }
        )
    return sorted(report, key=lambda item: item["canonical"])


def build_family_style_groups(records: list[FontRecord]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[FontRecord]] = defaultdict(list)
    for record in records:
        family = record.metadata.get("family") or record.metadata.get("typographicFamily") or ""
        subfamily = record.metadata.get("subfamily") or record.metadata.get("typographicSubfamily") or ""
        key = (normalize_token(family), normalize_token(subfamily))
        if not key[0]:
            continue
        groups[key].append(record)

    result: list[dict[str, Any]] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        canonical = choose_canonical([member.path for member in members])
        family = members[0].metadata.get("family") or members[0].metadata.get("typographicFamily") or ""
        subfamily = members[0].metadata.get("subfamily") or members[0].metadata.get("typographicSubfamily") or ""
        result.append(
            {
                "family": family,
                "subfamily": subfamily,
                "canonical": str(canonical).replace("\\", "/"),
                "members": [str(member.path).replace("\\", "/") for member in sorted(members, key=lambda item: str(item.path))],
                "extensions": sorted({member.extension for member in members}),
            }
        )
    return sorted(result, key=lambda item: (item["family"], item["subfamily"], item["canonical"]))


def write_report(path: Path, records: list[FontRecord], exact_duplicates: list[dict[str, Any]], family_groups: list[dict[str, Any]], limit_summary: dict[str, Any]) -> None:
    total_bytes = sum(record.size_bytes for record in records)
    license_counter = Counter(record.metadata.get("licenseStatus", "unknown") for record in records)
    lines = [
        "# Font Cleanup Report",
        "",
        f"- Total font files: {len(records)}",
        f"- Total size bytes: {total_bytes}",
        "",
        "## License Summary",
        "",
    ]

    for key in sorted(license_counter):
        lines.append(f"- `{key}`: {license_counter[key]}")

    lines.extend(["", "## CDN Limit Summary", ""])
    if not limit_summary:
        lines.append("- No active limit profiles configured.")
    else:
        for profile_id, summary in limit_summary.items():
            lines.append(f"- `{profile_id}` package: {summary['packageSizeBytes']} / {summary.get('packageBytes')} bytes, exceeded={summary.get('packageExceeded')}")

    lines.extend(["", "## Safe Exact Duplicates", ""])
    if not exact_duplicates:
        lines.append("- No exact duplicate binaries found.")
    else:
        for group in exact_duplicates:
            lines.append(f"- Keep `{group['canonical']}`")
            for duplicate in group["duplicates"]:
                if duplicate == group["canonical"]:
                    continue
                lines.append(f"  remove candidate: `{duplicate}`")

    lines.extend(["", "## Family/Style Groups With Multiple Variants", ""])
    if not family_groups:
        lines.append("- No family/style overlap groups found.")
    else:
        for group in family_groups:
            ext_list = ", ".join(group["extensions"])
            lines.append(f"- `{group['family']} / {group['subfamily']}` -> keep candidate `{group['canonical']}`")
            lines.append(f"  variants: {ext_list}")
            for member in group["members"]:
                if member == group["canonical"]:
                    continue
                lines.append(f"  review: `{member}`")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    repo_root = Path.cwd()
    font_root = (repo_root / args.font_root).resolve()
    thumb_root = (repo_root / args.thumb_root).resolve()
    manifest_path = repo_root / args.manifest_path
    report_path = repo_root / args.report_path
    readme_path = repo_root / args.readme_path

    config = load_source_config(repo_root / args.config_path)
    git_context = get_git_context()
    _, resolved_sources = resolve_sources(config, git_context)

    if thumb_root.exists():
        for old_thumb in thumb_root.glob("*.png"):
            old_thumb.unlink()
    thumb_root.mkdir(parents=True, exist_ok=True)

    files = sorted(path for path in font_root.rglob("*") if path.is_file() and path.suffix.lower() in FONT_EXTENSIONS)
    package_size_bytes = sum(path.stat().st_size for path in files)
    limit_summary = build_limit_summary(config, resolved_sources, package_size_bytes)

    records: list[FontRecord] = []
    errors: list[dict[str, str]] = []

    for path in files:
        relative = path.relative_to(repo_root)
        try:
            metadata = inspect_font(path)
        except Exception as exc:
            metadata = {
                "family": "",
                "subfamily": "",
                "fullName": "",
                "postscriptName": "",
                "typographicFamily": "",
                "typographicSubfamily": "",
                "designer": "",
                "manufacturer": "",
                "licenseDescription": "",
                "licenseUrl": "",
                "licenseStatus": "unknown",
                "fontCount": 0,
                "glyphCount": 0,
                "metadataError": str(exc),
            }
            errors.append({"path": relative.as_posix(), "stage": "metadata", "error": str(exc)})

        thumb_name = f"{slugify_path(relative)}.png"
        thumb_relative = Path(thumb_root.name) / thumb_name
        family_label = metadata.get("fullName") or metadata.get("family") or path.stem
        try:
            render_status = build_thumbnail(path, family_label, args.sample_text, thumb_root / thumb_name)
        except Exception as exc:
            render_status = "error"
            errors.append({"path": relative.as_posix(), "stage": "thumbnail", "error": str(exc)})

        size_bytes = path.stat().st_size
        commercial_use = build_commercial_use(metadata.get("licenseStatus", "unknown"))
        records.append(
            FontRecord(
                path=relative,
                sha256=sha256_file(path),
                size_bytes=size_bytes,
                extension=path.suffix.lower(),
                metadata=metadata,
                commercial_use=commercial_use,
                thumbnail_path=thumb_relative.as_posix(),
                thumbnail_render_status=render_status,
                limit_flags=build_limit_flags(limit_summary, size_bytes),
            )
        )

    exact_duplicates = build_exact_duplicate_groups(records)
    family_groups = build_family_style_groups(records)
    license_counter = Counter(record.metadata.get("licenseStatus", "unknown") for record in records)
    commercial_counter = Counter(record.commercial_use.get("status", "unknown") for record in records)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    manifest = {
        "generatedAt": generated_at,
        "resolver": {
            "configPath": args.config_path,
            "mode": "runtime-config",
        },
        "thumbnailRoot": str(thumb_root.name).replace("\\", "/"),
        "sampleText": args.sample_text,
        "limitSummary": limit_summary,
        "summary": {
            "fontCount": len(records),
            "totalSizeBytes": sum(record.size_bytes for record in records),
            "exactDuplicateGroupCount": len(exact_duplicates),
            "familyStyleGroupCount": len(family_groups),
            "licenseStatusCounts": dict(sorted(license_counter.items())),
            "commercialUseCounts": dict(sorted(commercial_counter.items())),
        },
        "exactDuplicates": exact_duplicates,
        "familyStyleGroups": family_groups,
        "fonts": [
            {
                "path": str(record.path).replace("\\", "/"),
                "sha256": record.sha256,
                "sizeBytes": record.size_bytes,
                "extension": record.extension,
                "thumbnailPath": record.thumbnail_path,
                "logoPath": record.thumbnail_path,
                "thumbnailRenderStatus": record.thumbnail_render_status,
                "limitFlags": record.limit_flags,
                "commercialUse": record.commercial_use,
                "metadata": record.metadata,
            }
            for record in records
        ],
        "errors": errors,
    }

    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_report(report_path, records, exact_duplicates, family_groups, limit_summary)
    readme_updated = sync_readme_font_status(readme_path, build_readme_font_status_section(records, generated_at))
    print(f"Wrote {manifest_path} with {len(records)} fonts.")
    print(f"Wrote {report_path}.")
    if readme_updated:
        print(f"Synced {readme_path}.")
    else:
        print(f"No README sync needed for {readme_path}.")


if __name__ == "__main__":
    main()
