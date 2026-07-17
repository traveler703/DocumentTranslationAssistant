from __future__ import annotations

import json
import shutil
import time
from pathlib import Path


class VersionManager:
    @staticmethod
    def _workspace_dir(conversation_id: str) -> Path:
        from .config import get_settings
        settings = get_settings()
        return settings.workspace / "projects" / conversation_id

    @staticmethod
    def _versions_dir(conversation_id: str) -> Path:
        return VersionManager._workspace_dir(conversation_id) / ".versions"

    @classmethod
    def save_version(cls, conversation_id: str, message: str) -> str:
        workspace = cls._workspace_dir(conversation_id)
        if not workspace.exists():
            workspace.mkdir(parents=True, exist_ok=True)
            
        versions_dir = cls._versions_dir(conversation_id)
        versions_dir.mkdir(parents=True, exist_ok=True)

        # Read existing metadata
        metadata_file = versions_dir / "versions.json"
        versions = []
        if metadata_file.exists():
            try:
                with open(metadata_file, "r", encoding="utf-8") as f:
                    versions = json.load(f)
            except Exception:
                versions = []

        # Generate version ID
        version_num = len(versions) + 1
        timestamp = int(time.time())
        version_id = f"v{version_num}_{timestamp}"

        # Create target snapshot directory
        snapshot_dir = versions_dir / version_id
        snapshot_dir.mkdir(parents=True, exist_ok=True)

        # Copy files from workspace root to snapshot
        for item in workspace.iterdir():
            if item.name == ".versions":
                continue
            if item.is_dir():
                # Skip temp directories (e.g. from parallel dispatches or deployers)
                if "_tmp_" in item.name or "_agent_" in item.name:
                    continue
                shutil.copytree(item, snapshot_dir / item.name, dirs_exist_ok=True)
            elif item.is_file():
                shutil.copy2(item, snapshot_dir / item.name)

        # Record metadata
        version_entry = {
            "id": version_id,
            "message": message,
            "timestamp": utc_iso_now(),
        }
        versions.append(version_entry)
        with open(metadata_file, "w", encoding="utf-8") as f:
            json.dump(versions, f, ensure_ascii=False, indent=2)

        return version_id

    @classmethod
    def list_versions(cls, conversation_id: str) -> list[dict]:
        versions_dir = cls._versions_dir(conversation_id)
        metadata_file = versions_dir / "versions.json"
        if not metadata_file.exists():
            return []
        try:
            with open(metadata_file, "r", encoding="utf-8") as f:
                return list(reversed(json.load(f)))
        except Exception:
            return []

    @classmethod
    def revert_version(cls, conversation_id: str, version_id: str) -> None:
        workspace = cls._workspace_dir(conversation_id)
        versions_dir = cls._versions_dir(conversation_id)
        snapshot_dir = versions_dir / version_id

        if not snapshot_dir.is_dir():
            raise FileNotFoundError(f"Version snapshot {version_id} not found.")

        # Clear current workspace root files (except .versions)
        for item in workspace.iterdir():
            if item.name == ".versions":
                continue
            if item.is_dir():
                shutil.rmtree(item)
            elif item.is_file():
                item.unlink()

        # Copy files back from snapshot
        for item in snapshot_dir.iterdir():
            if item.is_dir():
                shutil.copytree(item, workspace / item.name, dirs_exist_ok=True)
            elif item.is_file():
                shutil.copy2(item, workspace / item.name)

        # Save a new commit for the revert event
        cls.save_version(conversation_id, f"Rollback to {version_id}")


def utc_iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
