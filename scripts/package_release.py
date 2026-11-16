"""Create a verified source-only release without touching the restricted .git."""
import argparse
import hashlib
from pathlib import Path
import re
import tarfile

from scripts.data_safety import SafetyError, file_hash, now, private_directory, sync_directory, write_json

ROOT_FILES = ["README.md", "VERIFICATION.md", "HANDOFF.md", "requirements.txt", ".gitignore",
              "app.py", "collector.py", "db.py", "config.py", "analytics.py", "descriptions.py",
              "backend/__init__.py", "backend/manage.py", "backend/maintenance.py", "backend/pyproject.toml", "backend/requirements.in",
              "backend/requirements.lock", "backend/alembic.ini", "frontend/package.json",
              "frontend/package-lock.json", "frontend/tsconfig.json", "frontend/vite.config.ts", "frontend/index.html"]
SOURCE_DIRECTORIES = ["scripts", "tests", "docs", "backend/app", "backend/migrations", "backend/tests", "frontend/src", "frontend/tests"]
SOURCE_EXTENSIONS = {".py", ".md", ".vue", ".ts", ".css", ".mako", ".mjs", ".cjs"}


def _anonymous(info):
    """Source bundles carry no local account name or numeric owner."""
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


def source_files(root: Path) -> list[Path]:
    files = [root / name for name in ROOT_FILES if (root / name).is_file()]
    for name in SOURCE_DIRECTORIES:
        directory = root / name
        if directory.is_dir():
            files.extend(p for p in directory.rglob("*") if p.is_file() and p.suffix in SOURCE_EXTENSIONS
                         and "__pycache__" not in p.parts)
    if any(p.is_symlink() for p in files):
        raise SafetyError("SOURCE_SYMLINK", "源码含符号链接，未打包。")
    return sorted(set(files))


def package_release(root: Path, output: Path, version: str) -> dict:
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise SafetyError("VERSION_INVALID", "版本号必须为major.minor.patch。")
    files = source_files(root)
    manifest = {"format": "personal-hub-source-release", "format_version": 1, "version": version,
                "created_at": now(), "excludes": ["secrets", "user_data", "backups", "dependencies", "build_outputs"],
                "files": {str(p.relative_to(root)): file_hash(p) for p in files}}
    directory = private_directory(output)
    archive = directory / "source.tar.gz"
    with archive.open("xb") as handle:
        archive.chmod(0o600)
        with tarfile.open(fileobj=handle, mode="w:gz") as bundle:
            for path in files:
                bundle.add(path, arcname=str(path.relative_to(root)), recursive=False, filter=_anonymous)
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle.getmembers():
            extracted = bundle.extractfile(member)
            if extracted is None or hashlib.sha256(extracted.read()).hexdigest() != manifest["files"][member.name]:
                raise SafetyError("RELEASE_MISMATCH", "源码在打包过程中改变，请保留输出并重试到新目录。")
    manifest["archive_sha256"] = file_hash(archive)
    write_json(directory / "manifest.json", manifest)
    sync_directory(directory)
    return manifest


def main():
    parser = argparse.ArgumentParser(description="生成不含个人数据/密钥的源码版本快照，不覆盖输出。")
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    result = package_release(root, args.output, args.version)
    print(f"Source release {result['version']}: verified {len(result['files'])} files (no user data).")


if __name__ == "__main__":
    main()
