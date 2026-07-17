from __future__ import annotations

import os
from pathlib import Path


class FileMerger:
    @staticmethod
    def detect_changes(base_dir: Path, target_dir: Path) -> dict[str, str]:
        """
        Compare target_dir with base_dir, and return a dictionary of relative_path -> change_type.
        change_type can be 'added', 'modified', or 'deleted'.
        """
        changes = {}
        if not target_dir.exists():
            return changes

        # Check for added and modified files
        for root, _, files in os.walk(target_dir):
            for file in files:
                target_file_path = Path(root) / file
                rel_path = str(target_file_path.relative_to(target_dir))
                
                # Ignore .versions directory
                if rel_path.startswith(".versions") or ".versions\\" in rel_path or ".versions/" in rel_path:
                    continue

                base_file_path = base_dir / rel_path
                if not base_file_path.exists():
                    changes[rel_path] = "added"
                else:
                    try:
                        base_content = base_file_path.read_text(encoding="utf-8", errors="replace")
                        target_content = target_file_path.read_text(encoding="utf-8", errors="replace")
                        if base_content != target_content:
                            changes[rel_path] = "modified"
                    except Exception:
                        changes[rel_path] = "modified"

        # Check for deleted files
        if base_dir.exists():
            for root, _, files in os.walk(base_dir):
                for file in files:
                    base_file_path = Path(root) / file
                    rel_path = str(base_file_path.relative_to(base_dir))
                    if rel_path.startswith(".versions") or ".versions\\" in rel_path or ".versions/" in rel_path:
                        continue
                    target_file_path = target_dir / rel_path
                    if not target_file_path.exists():
                        changes[rel_path] = "deleted"

        return changes

    @staticmethod
    def merge_parallel_changes(
        base_dir: Path,
        workspace_dir: Path,
        agent_dirs: dict[str, tuple[str, Path]],  # agent_id -> (agent_name, agent_temp_dir)
    ) -> list[dict]:
        """
        Merge changes from each agent_temp_dir back to workspace_dir.
        Returns a list of conflict entries:
        [
            {
                "file": "relative/path/to/file",
                "base": "original base content",
                "agent_a": "agent A content",
                "agent_b": "agent B content",
                "agent_a_name": "Claude Code",
                "agent_b_name": "Codex"
            }
        ]
        """
        all_changes: dict[str, dict[str, str]] = {}  # agent_id -> rel_path -> change_type
        for agent_id, (_, temp_dir) in agent_dirs.items():
            all_changes[agent_id] = FileMerger.detect_changes(base_dir, temp_dir)

        # Find all files touched by any agent
        touched_files = set()
        for changes in all_changes.values():
            touched_files.update(changes.keys())

        conflicts = []

        for rel_path in touched_files:
            # Find which agents modified this file
            modifying_agents = []
            for agent_id, changes in all_changes.items():
                if rel_path in changes:
                    modifying_agents.append(agent_id)

            if len(modifying_agents) == 1:
                # Only one agent touched this file, apply directly
                agent_id = modifying_agents[0]
                _, temp_dir = agent_dirs[agent_id]
                change_type = all_changes[agent_id][rel_path]
                
                target_path = workspace_dir / rel_path
                temp_path = temp_dir / rel_path
                
                if change_type == "deleted":
                    if target_path.exists():
                        target_path.unlink()
                else:
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil_copy_file_or_dir(temp_path, target_path)

            elif len(modifying_agents) > 1:
                # Multiple agents touched the same file. Let's see if their modifications differ.
                contents = {}
                for agent_id in modifying_agents:
                    _, temp_dir = agent_dirs[agent_id]
                    temp_path = temp_dir / rel_path
                    if temp_path.exists():
                        try:
                            contents[agent_id] = temp_path.read_text(encoding="utf-8", errors="replace")
                        except Exception:
                            contents[agent_id] = ""
                    else:
                        contents[agent_id] = None  # means deleted by this agent

                # If all modifying agents produced the exact same content, no conflict!
                unique_contents = list(set(contents.values()))
                if len(unique_contents) == 1:
                    # Apply it
                    agent_id = modifying_agents[0]
                    _, temp_dir = agent_dirs[agent_id]
                    target_path = workspace_dir / rel_path
                    temp_path = temp_dir / rel_path
                    if unique_contents[0] is None:
                        if target_path.exists():
                            target_path.unlink()
                    else:
                        target_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil_copy_file_or_dir(temp_path, target_path)
                else:
                    # Conflict detected!
                    # For simplicty, we support 2-way conflict details. If more than 2, we take the first two.
                    agent_a_id = modifying_agents[0]
                    agent_b_id = modifying_agents[1]
                    name_a, _ = agent_dirs[agent_a_id]
                    name_b, _ = agent_dirs[agent_b_id]

                    content_base = ""
                    base_path = base_dir / rel_path
                    if base_path.exists():
                        content_base = base_path.read_text(encoding="utf-8", errors="replace")

                    content_a = contents[agent_a_id] or ""
                    content_b = contents[agent_b_id] or ""

                    conflicts.append({
                        "file": rel_path,
                        "base": content_base,
                        "agent_a": content_a,
                        "agent_b": content_b,
                        "agent_a_name": name_a,
                        "agent_b_name": name_b,
                        "agent_a_id": agent_a_id,
                        "agent_b_id": agent_b_id,
                    })

                    # Write conflict file to main workspace
                    target_path = workspace_dir / rel_path
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    
                    conflict_text = (
                        f"<<<<<<< {name_a}\n"
                        f"{content_a}\n"
                        f"=======\n"
                        f"{content_b}\n"
                        f">>>>>>> {name_b}\n"
                    )
                    target_path.write_text(conflict_text, encoding="utf-8")

        return conflicts


def shutil_copy_file_or_dir(src: Path, dst: Path) -> None:
    import shutil
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    elif src.is_file():
        shutil.copy2(src, dst)
