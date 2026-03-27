from __future__ import annotations

import json
import mimetypes
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import quote

CONTENT_TYPE_OVERRIDES = {
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".ttc": "font/collection",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}


def run_git(args: list[str]) -> str:
    return subprocess.check_output(["git", *args], text=True, encoding="utf-8").strip()


def parse_github_remote(remote_url: str) -> tuple[str, str]:
    match = re.search(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/.]+)(?:\.git)?$", remote_url)
    if not match:
        raise ValueError(f"Unable to parse GitHub owner/repo from origin URL: {remote_url}")
    return match.group("owner"), match.group("repo")


def get_git_context() -> dict[str, str]:
    origin = run_git(["remote", "get-url", "origin"])
    owner, repo = parse_github_remote(origin)
    return {
        "git_origin": origin,
        "git_owner": owner,
        "git_repo": repo,
        "git_branch": run_git(["branch", "--show-current"]),
        "git_commit": run_git(["rev-parse", "HEAD"]),
    }


def load_source_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_variables(config: dict[str, Any], git_context: dict[str, str]) -> dict[str, str]:
    variables = dict(git_context)
    for key, value in config.get("variables", {}).items():
        variables[key] = git_context.get(key, "") if value == "auto" else value
    return variables


def resolve_source_variables(source: dict[str, Any], base_variables: dict[str, str], git_context: dict[str, str]) -> dict[str, str]:
    variables = dict(base_variables)
    for key, value in source.get("variables", {}).items():
        variables[key] = git_context.get(key, "") if value == "auto" else value
    return variables


def repo_path_to_url_path(path: str) -> str:
    return "/".join(quote(part, safe="") for part in path.split("/"))


def file_content_type(path: Path) -> str:
    extension = path.suffix.lower()
    if extension in CONTENT_TYPE_OVERRIDES:
        return CONTENT_TYPE_OVERRIDES[extension]
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def resolve_sources(config: dict[str, Any], git_context: dict[str, str]) -> tuple[dict[str, str], list[dict[str, Any]]]:
    base_variables = resolve_variables(config, git_context)
    resolved = []
    for source in config.get("sources", []):
        resolved.append(
            {
                "id": source["id"],
                "provider": source.get("provider", "generic"),
                "enabled": bool(source.get("enabled", True)),
                "limitProfile": source.get("limitProfile"),
                "urlTemplate": source["urlTemplate"],
                "variables": resolve_source_variables(source, base_variables, git_context),
            }
        )
    return base_variables, resolved


def build_source_urls(resolved_sources: list[dict[str, Any]], repo_path: str) -> dict[str, str]:
    encoded_path = repo_path_to_url_path(repo_path)
    urls: dict[str, str] = {}
    for source in resolved_sources:
        if not source.get("enabled", True):
            continue
        variables = dict(source["variables"])
        variables["path"] = repo_path
        variables["encoded_path"] = encoded_path
        urls[source["id"]] = source["urlTemplate"].format_map(variables)
    return urls


def build_limit_summary(config: dict[str, Any], resolved_sources: list[dict[str, Any]], package_size_bytes: int) -> dict[str, Any]:
    limit_profiles = config.get("limitProfiles", {})
    summary: dict[str, Any] = {}
    for source in resolved_sources:
        if not source.get("enabled", True):
            continue
        profile_id = source.get("limitProfile")
        if not profile_id:
            continue
        profile = limit_profiles.get(profile_id)
        if not profile:
            continue
        entry = summary.setdefault(
            profile_id,
            {
                "singleFileBytes": profile.get("singleFileBytes"),
                "packageBytes": profile.get("packageBytes"),
                "notes": profile.get("notes", ""),
                "sourceIds": [],
                "packageSizeBytes": package_size_bytes,
                "packageExceeded": False,
            },
        )
        entry["sourceIds"].append(source["id"])
        package_limit = entry.get("packageBytes")
        if isinstance(package_limit, int):
            entry["packageExceeded"] = package_size_bytes > package_limit
    return summary


def build_limit_flags(limit_summary: dict[str, Any], file_size_bytes: int) -> dict[str, Any]:
    flags: dict[str, Any] = {}
    for profile_id, summary in limit_summary.items():
        single_file_limit = summary.get("singleFileBytes")
        flags[profile_id] = {
            "singleFileBytes": single_file_limit,
            "singleFileExceeded": isinstance(single_file_limit, int) and file_size_bytes > single_file_limit,
            "packageBytes": summary.get("packageBytes"),
            "packageSizeBytes": summary.get("packageSizeBytes"),
            "packageExceeded": summary.get("packageExceeded", False),
        }
    return flags
