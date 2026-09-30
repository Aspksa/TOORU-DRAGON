"""TOORU · DRAGON updater: GitHub ZIP -> local portable installation."""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

REPOSITORY = "Aspksa/TOORU-DRAGON"
BRANCH = "main"
API_BRANCH = f"https://api.github.com/repos/{REPOSITORY}/branches/{BRANCH}"
API_COMPARE = f"https://api.github.com/repos/{REPOSITORY}/compare"
ZIP_URL = f"https://github.com/{REPOSITORY}/archive/refs/heads/{BRANCH}.zip"
USER_AGENT = "TOORU-DRAGON-Updater/0.0.0"
STATE_REL = Path("data") / "update_state.json"
LOG_REL = Path("data") / "logs" / "update.log"
MANIFEST_REL = Path("data") / "update_manifest.json"
BACKUPS_REL = Path("backups")
PROTECTED_TOP_LEVEL = {"data", "python", "backups", ".git"}
PROTECTED_FILES = {"UpdateTooruDragon.bat"}
TRANSIENT_PREFIXES = (".update-", ".setup-")


def log(root: Path, message: str) -> None:
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    line = f"{stamp} {message}"
    print(message, flush=True)
    try:
        path = root / LOG_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def request_json(url: str, timeout: int = 20) -> dict:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def latest_revision() -> dict:
    payload = request_json(API_BRANCH)
    commit = payload.get("commit") if isinstance(payload, dict) else None
    sha = commit.get("sha", "") if isinstance(commit, dict) else ""
    nested = commit.get("commit") if isinstance(commit, dict) else None
    message = nested.get("message", "") if isinstance(nested, dict) else ""
    if not isinstance(sha, str) or len(sha) < 7:
        raise RuntimeError("GitHub не вернул SHA ветки main.")
    clean_message = str(message).strip()
    lines = clean_message.splitlines()
    title = lines[0][:200] if lines else ""
    description = "\n".join(line.strip() for line in lines[1:] if line.strip())[:1200]
    return {"sha": sha, "message": title, "description": description}


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def atomic_write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    os.replace(temporary, path)


def revision_changes(installed: str, latest: str) -> list[dict]:
    if not installed or installed == latest:
        return []
    if not re.fullmatch(r"[0-9a-fA-F]{40}", installed or ""):
        return []
    payload = request_json(f"{API_COMPARE}/{installed}...{latest}")
    files = payload.get("files") if isinstance(payload, dict) else []
    result = []
    labels = {
        "added": "добавлен",
        "modified": "изменён",
        "removed": "удалён",
        "renamed": "переименован",
        "copied": "скопирован",
        "changed": "изменён",
        "unchanged": "без изменений",
    }
    for item in files if isinstance(files, list) else []:
        if not isinstance(item, dict):
            continue
        filename = item.get("filename", "")
        if not isinstance(filename, str) or not filename:
            continue
        managed = is_managed(Path(filename))
        status = str(item.get("status", "modified"))
        result.append({
            "path": filename,
            "status": status,
            "status_label": labels.get(status, status),
            "will_update": managed,
            "previous_path": item.get("previous_filename", "") if status == "renamed" else "",
        })
    return result


def local_status(root: Path) -> dict:
    remote = latest_revision()
    state = read_json(root / STATE_REL, {})
    installed = state.get("revision", "") if isinstance(state, dict) else ""
    version = ""
    try:
        version = (root / "VERSION").read_text("utf-8").strip()
    except OSError:
        pass
    changes = revision_changes(installed, remote["sha"]) if installed else []
    return {
        "version": version or "неизвестно",
        "installed_revision": installed,
        "latest_revision": remote["sha"],
        "latest_message": remote["message"],
        "latest_description": remote.get("description", ""),
        "files": changes,
        "files_available": bool(installed) and bool(re.fullmatch(r"[0-9a-fA-F]{40}", installed or "")),
        "update_available": not installed or installed != remote["sha"],
        "tracked": bool(installed),
    }


def download_zip(root: Path, destination: Path) -> None:
    request = urllib.request.Request(ZIP_URL, headers={"User-Agent": USER_AGENT})
    log(root, "Скачивание обновления с GitHub...")
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
        total_header = response.headers.get("Content-Length")
        total = int(total_header) if total_header and total_header.isdigit() else 0
        downloaded = 0
        next_report = 0
        while True:
            chunk = response.read(1024 * 256)
            if not chunk:
                break
            output.write(chunk)
            downloaded += len(chunk)
            if downloaded >= next_report:
                if total:
                    percent = min(100, int(downloaded * 100 / total))
                    log(root, f"Загружено {downloaded / 1048576:.1f} МБ из {total / 1048576:.1f} МБ ({percent}%).")
                else:
                    log(root, f"Загружено {downloaded / 1048576:.1f} МБ.")
                next_report = downloaded + 5 * 1048576
    if destination.stat().st_size == 0:
        raise RuntimeError("GitHub вернул пустой ZIP.")


def safe_extract(zip_path: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        roots = set()
        for info in archive.infolist():
            name = info.filename.replace("\\", "/")
            parts = PurePosixPath(name).parts
            if not parts:
                continue
            if name.startswith("/") or ".." in parts:
                raise RuntimeError("ZIP содержит небезопасный путь.")
            roots.add(parts[0])
            target = destination.joinpath(*parts)
            resolved = target.resolve()
            if destination.resolve() not in resolved.parents and resolved != destination.resolve():
                raise RuntimeError("ZIP пытается выйти за временную папку.")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
    if len(roots) != 1:
        raise RuntimeError("Неожиданная структура ZIP GitHub.")
    source_root = destination / next(iter(roots))
    required = ("app.py", "StartTooruDragon.bat", "VERSION", "web")
    if not all((source_root / item).exists() for item in required):
        raise RuntimeError("В ZIP отсутствуют обязательные файлы TOORU.")
    return source_root


def is_managed(relative: Path) -> bool:
    if not relative.parts:
        return False
    top = relative.parts[0]
    if relative.as_posix() in PROTECTED_FILES:
        return False
    if top in PROTECTED_TOP_LEVEL:
        return False
    if any(top.startswith(prefix) for prefix in TRANSIENT_PREFIXES):
        return False
    return True


def source_manifest(source_root: Path) -> list[str]:
    files = []
    for path in source_root.rglob("*"):
        if path.is_file():
            rel = path.relative_to(source_root)
            if is_managed(rel):
                files.append(rel.as_posix())
    return sorted(files)


def copy_backup(root: Path, backup: Path, paths: set[str]) -> None:
    for rel_text in sorted(paths):
        rel = Path(rel_text)
        if not is_managed(rel):
            continue
        source = root / rel
        if source.is_file():
            target = backup / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def apply_source(root: Path, source_root: Path, revision: str) -> Path:
    new_manifest = source_manifest(source_root)
    previous = read_json(root / MANIFEST_REL, [])
    previous_manifest = set(previous) if isinstance(previous, list) else set()
    new_set = set(new_manifest)

    changed_or_removed = set()
    for rel_text in new_manifest:
        source = source_root / Path(rel_text)
        target = root / Path(rel_text)
        if not target.exists():
            continue
        try:
            same = (
                source.stat().st_size == target.stat().st_size
                and hashlib.sha256(source.read_bytes()).digest()
                == hashlib.sha256(target.read_bytes()).digest()
            )
        except OSError:
            same = False
        if not same:
            changed_or_removed.add(rel_text)
    changed_or_removed.update(previous_manifest - new_set)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = root / BACKUPS_REL / f"system-before-{stamp}"
    backup.mkdir(parents=True, exist_ok=False)
    copy_backup(root, backup, changed_or_removed)
    created = []

    try:
        for rel_text in sorted(previous_manifest - new_set):
            rel = Path(rel_text)
            if is_managed(rel):
                target = root / rel
                if target.is_file():
                    target.unlink()

        for rel_text in new_manifest:
            rel = Path(rel_text)
            source = source_root / rel
            target = root / rel
            existed = target.exists()
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".tooru-new")
            shutil.copy2(source, temporary)
            os.replace(temporary, target)
            if not existed:
                created.append(rel_text)

        manifest_path = root / MANIFEST_REL
        state = {
            "revision": revision,
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "repository": REPOSITORY,
            "branch": BRANCH,
        }
        atomic_write_json(manifest_path, new_manifest)
        atomic_write_json(root / STATE_REL, state)
    except Exception:
        for rel_text in created:
            target = root / Path(rel_text)
            try:
                if target.is_file():
                    target.unlink()
            except OSError:
                pass
        for path in backup.rglob("*"):
            if path.is_file():
                rel = path.relative_to(backup)
                target = root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
        raise
    return backup


def local_app_running(root: Path) -> bool:
    state = read_json(root / "data" / "server.json", {})
    port = state.get("port") if isinstance(state, dict) else None
    if type(port) is not int or not 1 <= port <= 65535:
        return False
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
            health = json.load(response)
        return isinstance(health, dict) and health.get("app") == "TOORU-DRAGON"
    except (OSError, ValueError, urllib.error.URLError):
        return False


def wait_for_pid(pid: int, timeout: float = 60.0) -> None:
    if pid <= 0:
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.25)
    raise RuntimeError("TOORU не завершилась перед обновлением.")


def restart_app(root: Path) -> None:
    launcher = root / "StartTooruDragon.bat"
    if os.name == "nt":
        os.startfile(str(launcher))
    else:
        subprocess.Popen([sys.executable, str(root / "app.py")], cwd=root)


def start_background_update(root: Path, wait_pid: int, expected_revision: str) -> None:
    command = [
        sys.executable, str(root / "updater.py"),
        "--root", str(root), "--apply",
        "--wait-pid", str(wait_pid), "--restart",
        "--expected-revision", expected_revision,
    ]
    kwargs = {
        "cwd": root,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    subprocess.Popen(command, **kwargs)


def perform_update(root: Path, expected_revision: str = "") -> dict:
    root = root.resolve()
    remote = latest_revision()
    revision = remote["sha"]
    if expected_revision and expected_revision != revision:
        log(root, "Ветка main изменилась после проверки; будет установлена самая свежая версия.")
    stage = Path(tempfile.mkdtemp(prefix=".update-", dir=root))
    try:
        zip_path = stage / "update.zip"
        extract_dir = stage / "extract"
        download_zip(root, zip_path)
        source_root = safe_extract(zip_path, extract_dir)
        backup = apply_source(root, source_root, revision)
        log(root, f"Обновление установлено: {revision[:12]}.")
        log(root, f"Резервная копия изменённых системных файлов: {backup.relative_to(root)}")
        return {"revision": revision, "backup": str(backup)}
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--wait-pid", type=int, default=0)
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--expected-revision", default="")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        if args.check:
            print(json.dumps(local_status(root), ensure_ascii=False))
            return 0
        if not args.apply:
            parser.error("укажите --check или --apply")
        if not args.wait_pid and local_app_running(root):
            raise RuntimeError("TOORU сейчас запущена. Закройте программу или обновляйте через раздел «Система обновления».")
        wait_for_pid(args.wait_pid)
        perform_update(root, args.expected_revision)
        if args.restart:
            restart_app(root)
        return 0
    except urllib.error.HTTPError as exc:
        log(root, f"Ошибка GitHub HTTP {exc.code}.")
    except urllib.error.URLError:
        log(root, "Нет связи с GitHub. Проверьте интернет.")
    except Exception as exc:
        log(root, "Обновление не выполнено: " + str(exc))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
