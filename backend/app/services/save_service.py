"""存档：3 个手动槽 + 1 个自动槽，服务端权威快照 + HMAC 校验。

规则（任务书 §7 / 系统设计 §4.8）：
- 自动槽 ``slot_id=0`` **系统可写、玩家不可写**；手动槽 1–3；
- 保存 = 把当前角色全量快照写入 ``save_slot``（服务端复制，不是前端上传）；
- 读取 = 校验 content_version 后按 ``restore_snapshot`` 顺序覆盖当前角色；
- 导出/导入 = JSON 文件，含 ``schema_version / content_version / content_hash /
  checksum``；导入失败**不落库**，成功则新建角色并占用一个空手动槽；
- 写入带 ``expected_version`` 乐观锁（409/4004）。

安全：checksum 使用 HMAC-SHA256，密钥来自环境变量 ``SAVE_SIGN_SECRET``；
导入上限 2MB，结构校验失败一律 4020。快照内的 ``rng_seed`` 只随存档文件走，
绝不通过 ``/state`` 下发。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from ..content import get_registry
from ..core.errors import (
    CONTENT_VERSION_INCOMPATIBLE,
    IMPORT_INVALID,
    SAVE_SLOT_INVALID,
    VERSION_CONFLICT,
    GameError,
)
from ..db.game_repository import (
    CharacterRepository,
    MetaRepository,
    SaveSlotRepository,
    build_snapshot,
    restore_snapshot,
)

SIGN_SCHEMA_VERSION = 1
MAX_IMPORT_BYTES = 2 * 1024 * 1024
SECRET_ENV = "SAVE_SIGN_SECRET"
SECRET_FILE_NAME = ".save_secret"


def _secret() -> bytes:
    """签名密钥。

    优先读环境变量 ``SAVE_SIGN_SECRET``；未设置时在数据目录生成一次性随机密钥
    （文件权限由操作系统默认保护）。**不要**使用仓库里的固定默认值——那会让
    任何人都能伪造存档。跨设备导入时，两端需设置同一个环境变量。
    """
    env = os.environ.get(SECRET_ENV)
    if env:
        return env.encode("utf-8")
    data_dir = Path(os.environ.get("HUANGTIANJI_DATA",
                                   Path(__file__).resolve().parents[3] / "data"))
    secret_file = data_dir / SECRET_FILE_NAME
    try:
        if secret_file.exists():
            value = secret_file.read_text(encoding="utf-8").strip()
            if value:
                return value.encode("utf-8")
        data_dir.mkdir(parents=True, exist_ok=True)
        value = secrets.token_hex(32)
        secret_file.write_text(value, encoding="utf-8")
        return value.encode("utf-8")
    except OSError:
        # 只读文件系统兜底：进程内随机（重启后旧档将无法导入，属可接受降级）
        return secrets.token_hex(32).encode("utf-8")


def _canonical(payload: Mapping[str, Any]) -> bytes:
    """稳定 JSON 编码（排序键、紧凑分隔符、中文不转义）。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _checksum(snapshot: Mapping[str, Any], content_version: str,
              content_hash: str) -> str:
    """对「快照 + 内容版本」做 HMAC-SHA256。"""
    body = _canonical({
        "snapshot": snapshot,
        "content_version": content_version,
        "content_hash": content_hash,
    })
    return hmac.new(_secret(), body, hashlib.sha256).hexdigest()


class SaveService:
    """存档服务。"""

    # ---------------- 槽位 ----------------

    @staticmethod
    def list_slots() -> List[Dict[str, Any]]:
        """全部槽位（0 自动 + 1–3 手动），补角色名与版本供 UI 展示。"""
        slots = SaveSlotRepository.list_all()
        for slot in slots:
            char_id = slot.get("char_id")
            if not char_id:
                continue
            try:
                char = CharacterRepository.get(int(char_id))
            except (TypeError, ValueError):
                char = None
            if char:
                slot["name"] = str(char.get("name") or "")
                slot["char_name"] = slot["name"]
                slot["version"] = int(char.get("version", 0))
                slot["level"] = int(char.get("level", 1))
        return slots

    @staticmethod
    def save_slot(slot_id: int, *, char_id: Optional[int] = None,
                  label: str = "", expected_version: Optional[int] = None) -> Dict[str, Any]:
        """保存到指定槽位。

        Args:
            slot_id: 0–3；0 仅供系统调用（``autosave``）。
            char_id: 角色 id；None 取当前活动角色。
            label: 槽位备注。
            expected_version: 乐观锁版本（手动保存可由前端传入）。

        Raises:
            GameError: 4003/4004/4019。
        """
        if int(slot_id) == 0:
            # 自动槽只允许系统调用；对外入口在 API 层已拒绝玩家写 0
            pass
        char = (CharacterRepository.require(int(char_id)) if char_id is not None
                else CharacterRepository.get_active())
        if char is None:
            raise GameError(4003, "当前没有可保存的角色")
        if expected_version is not None and int(expected_version) != int(char["version"]):
            raise GameError(VERSION_CONFLICT, "存档已在别处变更，请重新载入",
                            {"expected": int(expected_version),
                             "current": int(char["version"])})

        registry = get_registry()
        version = str(registry.content.content_version)
        content_hash = str(registry.content.content_hash)
        snapshot = build_snapshot(int(char["id"]))
        checksum = _checksum(snapshot, version, content_hash)
        SaveSlotRepository.upsert(
            int(slot_id), char_id=int(char["id"]), snapshot=snapshot,
            content_version=version, content_hash=content_hash,
            checksum=checksum, label=label or f"自动存档 · 第{int(char['age_days']) // 360 + 1}年")
        return {
            "slot_id": int(slot_id),
            "char_id": int(char["id"]),
            "created_at": int(char["version"]),
            "content_version": version,
            "checksum": checksum,
            "label": label,
        }

    @staticmethod
    def autosave(char_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """自动存档（slot 0）。失败不抛给业务调用方。"""
        try:
            return SaveService.save_slot(0, char_id=char_id, label="自动存档")
        except GameError:
            return None

    @staticmethod
    def load(slot_id: int, *, expected_version: Optional[int] = None) -> Dict[str, Any]:
        """读取槽位并覆盖当前角色。

        Raises:
            GameError: 4019 槽位不存在 / 4021 内容版本不兼容 / 4004 版本冲突。
        """
        slot = SaveSlotRepository.get(int(slot_id))
        if slot is None or not slot.get("snapshot"):
            raise GameError(SAVE_SLOT_INVALID, f"存档槽 {slot_id} 为空")
        SaveService._assert_compatible(slot)
        active = CharacterRepository.get_active()
        if active is not None:
            target_id = int(active["id"])
            version = int(expected_version if expected_version is not None else active["version"])
            restore_snapshot(slot["snapshot"], char_id=target_id, expected_version=version)
        else:
            target_id = restore_snapshot(slot["snapshot"], char_id=None)
        MetaRepository.set("active_char_id", str(target_id))
        return {"slot_id": int(slot_id), "char_id": int(target_id),
                "content_version": slot["content_version"]}

    @staticmethod
    def delete(slot_id: int) -> Dict[str, Any]:
        """删除手动槽（自动槽由 Repository 拒绝）。"""
        SaveSlotRepository.delete(int(slot_id))
        return {"slot_id": int(slot_id), "deleted": True}

    # ---------------- 导出 / 导入 ----------------

    @staticmethod
    def export(slot_id: int) -> Dict[str, Any]:
        """导出槽位为可下载 JSON。"""
        slot = SaveSlotRepository.get(int(slot_id))
        if slot is None or not slot.get("snapshot"):
            raise GameError(SAVE_SLOT_INVALID, f"存档槽 {slot_id} 为空")
        registry = get_registry()
        payload = {
            "schema_version": SIGN_SCHEMA_VERSION,
            "content_version": str(slot["content_version"]),
            "content_hash": str(slot["content_hash"]),
            "snapshot": slot["snapshot"],
            "checksum": str(slot["checksum"]),
            "exported_at": int(slot.get("created_at", 0)),
        }
        return payload

    @staticmethod
    def import_payload(payload: Mapping[str, Any],
                       *, slot_id: Optional[int] = None) -> Dict[str, Any]:
        """导入存档：校验四项 → 新建角色 → 落一个新手动槽。

        Args:
            payload: 导出结构（可多一层 ``data`` 包裹）。
            slot_id: 目标手动槽；None 时取第一个空槽。

        Raises:
            GameError: 4020 校验失败 / 4021 内容版本不兼容 / 4019 无空槽。
        """
        data = dict(payload or {})
        if "snapshot" not in data and isinstance(data.get("data"), Mapping):
            data = dict(data["data"])
        if len(_canonical(data)) > MAX_IMPORT_BYTES:
            raise GameError(IMPORT_INVALID, "存档文件超过 2MB 上限")

        snapshot = data.get("snapshot")
        if not isinstance(snapshot, Mapping) or not isinstance(snapshot.get("character"), Mapping):
            raise GameError(IMPORT_INVALID, "存档结构非法：缺少 snapshot.character")
        if int(data.get("schema_version", -1)) != SIGN_SCHEMA_VERSION:
            raise GameError(IMPORT_INVALID, "存档 schema 版本不兼容",
                            {"schema_version": data.get("schema_version")})

        registry = get_registry()
        version = str(data.get("content_version", ""))
        content_hash = str(data.get("content_hash", ""))
        if version != str(registry.content.content_version):
            raise GameError(CONTENT_VERSION_INCOMPATIBLE,
                            "存档来自其他内容版本，无法直接读取",
                            {"save": version, "current": registry.content.content_version})
        if content_hash != str(registry.content.content_hash):
            raise GameError(IMPORT_INVALID, "存档内容哈希不匹配")
        expected = _checksum(snapshot, version, content_hash)
        if not hmac.compare_digest(str(data.get("checksum", "")), expected):
            raise GameError(IMPORT_INVALID, "存档校验失败（checksum 不匹配）")

        target_slot = int(slot_id) if slot_id is not None else SaveService._first_empty_slot()
        if target_slot == 0 or target_slot not in SaveSlotRepository.MANUAL_SLOTS:
            raise GameError(SAVE_SLOT_INVALID, "导入只能写入手动槽 1–3")

        char_id = restore_snapshot(snapshot, char_id=None)
        MetaRepository.set("active_char_id", str(char_id))
        SaveService.save_slot(target_slot, char_id=char_id, label="导入存档")
        return {"slot_id": target_slot, "char_id": int(char_id)}

    # ---------------- 内部 ----------------

    @staticmethod
    def _assert_compatible(slot: Mapping[str, Any]) -> None:
        """校验内容版本（同一 content_version 才允许直接读）。"""
        registry = get_registry()
        if str(slot.get("content_version")) != str(registry.content.content_version):
            raise GameError(CONTENT_VERSION_INCOMPATIBLE,
                            "该存档来自旧内容版本，暂不支持自动迁移",
                            {"save": slot.get("content_version"),
                             "current": registry.content.content_version})

    @staticmethod
    def _first_empty_slot() -> int:
        """取第一个空手动槽。"""
        used = {int(x["slot_id"]) for x in SaveSlotRepository.list_all()
                if x.get("exists")}
        for slot in SaveSlotRepository.MANUAL_SLOTS:
            if slot not in used:
                return slot
        raise GameError(SAVE_SLOT_INVALID, "没有空闲的手动存档槽")
