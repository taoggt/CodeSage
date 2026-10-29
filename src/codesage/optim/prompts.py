"""Prompt 版本管理：内容哈希作为版本号，效果绑定 Harness 评测。

用途：
- 每次修改 prompt 模板（含注入的经验块）时登记一个版本；
- Harness 评测结果标注使用的版本 → `codesage eval --compare` 时
  能回答"v2 相比 v1 的检出率/误报率变化"；
- 有数据支撑的回滚决策（而不是拍脑袋）。
"""

from __future__ import annotations

import hashlib

from codesage.memory.store import Store


class PromptRegistry:
    """Prompt 注册表。"""

    def __init__(self, store: Store) -> None:
        self._store = store

    @staticmethod
    def version_of(template: str) -> str:
        """内容哈希（前 8 位）作为版本号——内容相同即同版本。

        非安全用途的短标识，usedforsecurity=False。
        """
        return hashlib.sha1(
            template.encode("utf-8"), usedforsecurity=False
        ).hexdigest()[:8]

    def register(self, profile: str, template: str, note: str = "") -> str:
        """登记模板（幂等）。返回版本号。"""
        version = self.version_of(template)
        self._store.add_prompt_version(
            profile=profile, version=version, template=template, note=note
        )
        return version

    def versions(self, profile: str, limit: int = 20) -> list[dict]:
        return self._store.prompt_versions(profile, limit=limit)

    def latest(self, profile: str) -> dict | None:
        versions = self.versions(profile, limit=1)
        return versions[0] if versions else None
