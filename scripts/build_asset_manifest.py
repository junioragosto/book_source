from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from cdn_manifest_lib import build_limit_flags, build_limit_summary, file_content_type, get_git_context, load_source_config, repo_path_to_url_path, resolve_sources

COVER_VARIANTS = {
    "_bg": "bg",
    "_surare": "surare",
    "_thumb": "thumb",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build pure-data asset manifest for covers and fonts.")
    parser.add_argument("--config-path", default="cdn-sources.json")
    parser.add_argument("--output-path", default="cdn-manifest.json")
    parser.add_argument("--asset-roots", nargs="*", default=["Base_Cover", "Base_Fonts"])
    return parser.parse_args()


def kind_for_path(repo_path: str) -> str:
    if repo_path.startswith("Base_Cover/"):
        return "cover"
    if repo_path.startswith("Base_Fonts/"):
        return "font"
    return "asset"


def cover_group_info(repo_path: str) -> dict[str, str] | None:
    if not repo_path.startswith("Base_Cover/"):
        return None

    relative = repo_path[len("Base_Cover/") :]
    relative_path = Path(relative)
    stem = relative_path.stem
    variant = "default"
    group_stem = stem
    for suffix, variant_name in COVER_VARIANTS.items():
        if stem.endswith(suffix):
            variant = variant_name
            group_stem = stem[: -len(suffix)]
            break

    group_id = group_stem if str(relative_path.parent) == "." else f"{relative_path.parent.as_posix()}/{group_stem}"
    return {
        "groupId": group_id,
        "variant": variant,
        "directory": "." if str(relative_path.parent) == "." else relative_path.parent.as_posix(),
        "baseName": group_stem,
    }


def build_cover_index(assets: list[dict[str, object]]) -> dict[str, object]:
    groups: dict[str, dict[str, object]] = {}
    variants: dict[str, list[dict[str, str]]] = {"default": [], "bg": [], "surare": [], "thumb": []}

    for asset in assets:
        if asset["kind"] != "cover":
            continue
        info = cover_group_info(asset["path"])
        if not info:
            continue

        group = groups.setdefault(
            info["groupId"],
            {
                "id": info["groupId"],
                "directory": info["directory"],
                "baseName": info["baseName"],
                "variants": {},
            },
        )
        group["variants"][info["variant"]] = asset["path"]
        variants.setdefault(info["variant"], []).append({"groupId": info["groupId"], "path": asset["path"]})

    ordered_groups = sorted(groups.values(), key=lambda item: item["id"])
    for items in variants.values():
        items.sort(key=lambda item: (item["groupId"], item["path"]))

    return {
        "groupCount": len(ordered_groups),
        "groups": ordered_groups,
        "variants": variants,
    }


def main() -> None:
    args = parse_args()
    repo_root = Path.cwd()
    config = load_source_config(Path(args.config_path))
    git_context = get_git_context()
    _, resolved_sources = resolve_sources(config, git_context)

    files: list[Path] = []
    directories = []
    for root_name in args.asset_roots:
        root = repo_root / root_name
        if not root.exists():
            continue
        root_files = sorted(path for path in root.rglob("*") if path.is_file())
        files.extend(root_files)
        directories.append(
            {
                "name": root_name,
                "fileCount": len(root_files),
                "sizeBytes": sum(path.stat().st_size for path in root_files),
            }
        )

    files = sorted(files)
    package_size_bytes = sum(path.stat().st_size for path in files)
    limit_summary = build_limit_summary(config, resolved_sources, package_size_bytes)

    assets = []
    for path in files:
        repo_path = path.relative_to(repo_root).as_posix()
        size_bytes = path.stat().st_size
        cover_info = cover_group_info(repo_path)
        assets.append(
            {
                "path": repo_path,
                "encodedPath": repo_path_to_url_path(repo_path),
                "kind": kind_for_path(repo_path),
                "extension": path.suffix.lower(),
                "sizeBytes": size_bytes,
                "contentType": file_content_type(path),
                "coverGroupId": None if not cover_info else cover_info["groupId"],
                "coverVariant": None if not cover_info else cover_info["variant"],
                "limitFlags": build_limit_flags(limit_summary, size_bytes),
            }
        )

    manifest = {
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "resolver": {
            "configPath": args.config_path,
            "mode": "runtime-config",
        },
        "limitSummary": limit_summary,
        "directories": directories,
        "assetCount": len(assets),
        "covers": build_cover_index(assets),
        "assets": assets,
    }

    Path(args.output_path).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.output_path} with {len(assets)} assets.")


if __name__ == "__main__":
    main()
