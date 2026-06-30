#!/usr/bin/env python3
"""
Compare the Drupal installation composer files with the latest production
version in drupal_packages, write a changelog entry, bump the version,
and copy composer.json + composer.lock into a new production version folder
and the matching development track (e.g. development/3.x).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import date
from pathlib import Path
from typing import Any


def parse_version(version: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version)
    if not match:
        raise ValueError(f"Invalid semver directory name: {version!r}")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def bump_version(version: str, bump: str) -> str:
    major, minor, patch = parse_version(version)
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    if bump == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"Unknown bump type: {bump!r}")


def version_key(version: str) -> tuple[int, int, int]:
    return parse_version(version)


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def repository_key(repo: dict[str, Any]) -> str:
    if "name" in repo:
        return f"name:{repo['name']}"
    if "url" in repo:
        return f"url:{repo['url']}"
    return json.dumps(repo, sort_keys=True)


def compare_maps(
    old: dict[str, str],
    new: dict[str, str],
    label: str,
) -> list[str]:
    lines: list[str] = []
    old_keys = set(old)
    new_keys = set(new)

    for key in sorted(new_keys - old_keys):
        lines.append(f"- add {label} `{key}` {new[key]}")
    for key in sorted(old_keys - new_keys):
        lines.append(f"- remove {label} `{key}` {old[key]}")
    for key in sorted(old_keys & new_keys):
        if old[key] != new[key]:
            lines.append(f"- update {label} `{key}` from {old[key]} to {new[key]}")

    return lines


def compare_repositories(
    old: list[dict[str, Any]],
    new: list[dict[str, Any]],
) -> list[str]:
    old_map = {repository_key(repo): repo for repo in old}
    new_map = {repository_key(repo): repo for repo in new}
    lines: list[str] = []

    for key in sorted(set(new_map) - set(old_map)):
        repo = new_map[key]
        name = repo.get("name", repo.get("url", key))
        lines.append(f"- add repository `{name}`")
    for key in sorted(set(old_map) - set(new_map)):
        repo = old_map[key]
        name = repo.get("name", repo.get("url", key))
        lines.append(f"- remove repository `{name}`")
    for key in sorted(set(old_map) & set(new_map)):
        if old_map[key] != new_map[key]:
            name = new_map[key].get("name", new_map[key].get("url", key))
            lines.append(f"- update repository `{name}`")

    return lines


def compare_composer(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    lines.extend(compare_maps(old.get("require", {}), new.get("require", {}), "package"))

    old_stability = old.get("minimum-stability")
    new_stability = new.get("minimum-stability")
    if isinstance(old_stability, str) and isinstance(new_stability, str):
        if old_stability != new_stability:
            lines.append(
                f"- update minimum-stability from {old_stability} to {new_stability}"
            )

    old_prefer = old.get("prefer-stable")
    new_prefer = new.get("prefer-stable")
    if old_prefer != new_prefer:
        lines.append(f"- update prefer-stable from {old_prefer} to {new_prefer}")

    lines.extend(
        compare_repositories(
            old.get("repositories", []),
            new.get("repositories", []),
        )
    )

    return lines


def find_latest_version(production_dir: Path) -> str:
    versions = []
    for child in production_dir.iterdir():
        if child.is_dir():
            try:
                versions.append((version_key(child.name), child.name))
            except ValueError:
                continue

    if not versions:
        raise SystemExit(f"No production versions found in {production_dir}")

    versions.sort(key=lambda item: item[0])
    return versions[-1][1]


def find_development_dir(packages_repo: Path, package_name: str, major: int) -> Path:
    development_root = packages_repo / package_name / "development"
    if not development_root.is_dir():
        raise SystemExit(f"Development directory does not exist: {development_root}")

    track = f"{major}.x"
    development_dir = development_root / track
    if not development_dir.is_dir():
        available = sorted(
            child.name for child in development_root.iterdir() if child.is_dir()
        )
        raise SystemExit(
            f"Development track {track!r} does not exist under {development_root}. "
            f"Available: {', '.join(available) or '(none)'}"
        )
    return development_dir


def copy_composer_files(source_composer: Path, source_lock: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_composer, target_dir / "composer.json")
    shutil.copy2(source_lock, target_dir / "composer.lock")


def prepend_changelog(
    changelog_path: Path,
    version: str,
    lines: list[str],
) -> None:
    header = "# Changelog\n"
    entry = f"## {version}\n" + "\n".join(lines) + "\n"

    if changelog_path.exists():
        existing = changelog_path.read_text(encoding="utf-8")
        if existing.startswith(header):
            body = existing[len(header) :]
        else:
            body = existing
        changelog_path.write_text(header + entry + body, encoding="utf-8")
    else:
        changelog_path.write_text(header + entry, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Release a new wisski_base production composer version from the "
            "current Drupal installation."
        )
    )
    parser.add_argument(
        "--drupal-root",
        type=Path,
        default=Path("/opt/drupal"),
        help="Path to the Drupal installation (default: /opt/drupal)",
    )
    parser.add_argument(
        "--packages-repo",
        type=Path,
        default=Path("/opt/drupal/private-files/drupal_packages"),
        help="Path to the drupal_packages git repository",
    )
    parser.add_argument(
        "--package-path",
        default="wisski_base/production",
        help="Package path inside the repo (default: wisski_base/production)",
    )
    parser.add_argument(
        "--package-name",
        default="wisski_base",
        help="Package name for resolving development tracks (default: wisski_base)",
    )
    parser.add_argument(
        "--no-update-development",
        action="store_true",
        help="Do not update the matching development track (e.g. development/3.x)",
    )
    version_group = parser.add_mutually_exclusive_group()
    version_group.add_argument(
        "--new-major",
        action="store_const",
        const="major",
        dest="bump",
        help="Bump major version (e.g. 3.1.2 -> 4.0.0)",
    )
    version_group.add_argument(
        "--new-minor",
        action="store_const",
        const="minor",
        dest="bump",
        help="Bump minor version (e.g. 3.1.2 -> 3.2.0); default when omitted",
    )
    version_group.add_argument(
        "--new-patch",
        action="store_const",
        const="patch",
        dest="bump",
        help="Bump patch version (e.g. 3.1.2 -> 3.1.3)",
    )
    parser.set_defaults(bump="minor")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Create a new version even when composer.json has no changes",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the planned release without writing files",
    )
    args = parser.parse_args()

    drupal_root = args.drupal_root.resolve()
    packages_repo = args.packages_repo.resolve()
    production_dir = packages_repo / args.package_path
    changelog_path = packages_repo / "CHANGELOG"

    source_composer = drupal_root / "composer.json"
    source_lock = drupal_root / "composer.lock"

    for path in (source_composer, source_lock, production_dir):
        if not path.exists():
            raise SystemExit(f"Required path does not exist: {path}")

    latest_version = find_latest_version(production_dir)
    latest_composer_path = production_dir / latest_version / "composer.json"
    if not latest_composer_path.exists():
        raise SystemExit(f"Missing composer.json in {latest_composer_path.parent}")

    old_composer = load_json(latest_composer_path)
    new_composer = load_json(source_composer)
    changes = compare_composer(old_composer, new_composer)

    if not changes and not args.force:
        print(
            f"No composer changes compared to production/{latest_version}. "
            "Use --force to release anyway.",
            file=sys.stderr,
        )
        return 1

    if not changes:
        changes = [f"- no composer.json changes (released on {date.today().isoformat()})"]

    next_version = bump_version(latest_version, args.bump)
    target_dir = production_dir / next_version
    next_major = parse_version(next_version)[0]
    development_dir = (
        None
        if args.no_update_development
        else find_development_dir(packages_repo, args.package_name, next_major)
    )

    if target_dir.exists():
        raise SystemExit(f"Target version directory already exists: {target_dir}")

    print(f"Latest production version: {latest_version}")
    print(f"New production version:    {next_version}")
    print(f"Target directory:          {target_dir}")
    if development_dir is not None:
        print(f"Development track:         {development_dir}")
    print("Changelog entry:")
    for line in changes:
        print(f"  {line}")

    if args.dry_run:
        print("Dry run complete; no files were written.")
        return 0

    target_dir.mkdir(parents=True, exist_ok=False)
    copy_composer_files(source_composer, source_lock, target_dir)
    prepend_changelog(changelog_path, next_version, changes)

    print(f"Created {target_dir / 'composer.json'}")
    print(f"Created {target_dir / 'composer.lock'}")
    if development_dir is not None:
        copy_composer_files(source_composer, source_lock, development_dir)
        print(f"Updated {development_dir / 'composer.json'}")
        print(f"Updated {development_dir / 'composer.lock'}")
    print(f"Updated {changelog_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
