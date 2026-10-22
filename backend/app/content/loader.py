"""内容加载器：读 manifest → 按 order 加载 16 个 YAML → 递归冻结 → 建索引。

约定（system design §2.2）：
- 单一入口 ``load_all()``，结果进程内缓存（``reload_all()`` 清缓存，测试用）；
- 所有返回结构**只读**：``MappingProxyType`` + ``tuple``，
  防止 domain/services 误改内容；
- ``content_hash`` = 按加载顺序拼接各文件原始字节后的 SHA-256，
  写入存档导出体，用于「同内容版本但内容被改」的检测；
- 缺失文件 / YAML 语法错 → ``ContentLoadError``，启动即失败（不静默降级）。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import yaml

# backend/app/content/loader.py → parents[3] = 项目根
_PROJECT_ROOT: Path = Path(__file__).resolve().parents[3]
CONTENT_DIR: Path = _PROJECT_ROOT / "data" / "content"

# 文件名（无扩展名）→ (顶层键, 是否为列表)
_SECTIONS: Dict[str, Tuple[str, bool]] = {
    "game": ("__self__", False),
    "realms": ("realms", True),
    "physiques": ("physiques", True),
    "talents": ("talents", True),
    "personalities": ("personalities", True),
    "techniques": ("techniques", True),
    "items": ("items", True),
    "equipments": ("__self__", False),
    "enemies": ("enemies", True),
    "regions": ("regions", True),
    "events": ("events", True),
    "quests_main": ("quests", True),
    "sects": ("sects", True),
    "sansxiu": ("__self__", False),
    "forbidden_lands": ("lands", True),
}


class ContentLoadError(RuntimeError):
    """内容文件缺失 / 解析失败 / 结构不符。"""


# ---------------- 冻结 ----------------

def _freeze(value: Any) -> Any:
    """递归冻结：dict → MappingProxyType，list/tuple → tuple。"""
    if isinstance(value, Mapping):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    return value


def _index(items: Sequence[Mapping[str, Any]],
           key: str) -> Mapping[str, Mapping[str, Any]]:
    """按某键建只读索引（重复键取首个，重复由 validators 报错）。"""
    out: Dict[str, Mapping[str, Any]] = {}
    for item in items:
        raw_key = item.get(key)
        if raw_key is None:
            continue
        out.setdefault(str(raw_key), item)
    return MappingProxyType(out)


def _index_int(items: Sequence[Mapping[str, Any]],
               key: str) -> Mapping[int, Mapping[str, Any]]:
    """按整数键建只读索引（重复键取首个，与 ``_index`` 保持一致）。"""
    out: Dict[int, Mapping[str, Any]] = {}
    for item in items:
        out.setdefault(int(item[key]), item)
    return MappingProxyType(out)


# ---------------- Content ----------------

@dataclass(frozen=True)
class Content:
    """全部内容层的冻结快照（字段见各类注释）。"""

    content_version: str
    schema_version: int
    content_hash: str
    content_dir: Path
    game: Mapping[str, Any]
    realms: Tuple[Mapping[str, Any], ...]
    realm_by_id: Mapping[str, Mapping[str, Any]]
    realm_by_index: Mapping[int, Mapping[str, Any]]
    physiques: Tuple[Mapping[str, Any], ...]
    physique_by_id: Mapping[str, Mapping[str, Any]]
    talents: Tuple[Mapping[str, Any], ...]
    talent_by_grade: Mapping[int, Mapping[str, Any]]
    personalities: Tuple[Mapping[str, Any], ...]
    personality_by_id: Mapping[str, Mapping[str, Any]]
    techniques: Tuple[Mapping[str, Any], ...]
    technique_by_id: Mapping[str, Mapping[str, Any]]
    items: Tuple[Mapping[str, Any], ...]
    item_by_id: Mapping[str, Mapping[str, Any]]
    equipments: Mapping[str, Any]
    equip_template_by_id: Mapping[str, Mapping[str, Any]]
    enemies: Tuple[Mapping[str, Any], ...]
    enemy_by_id: Mapping[str, Mapping[str, Any]]
    regions: Tuple[Mapping[str, Any], ...]
    region_by_id: Mapping[str, Mapping[str, Any]]
    events: Tuple[Mapping[str, Any], ...]
    event_by_id: Mapping[str, Mapping[str, Any]]
    quests: Tuple[Mapping[str, Any], ...]
    quest_by_id: Mapping[str, Mapping[str, Any]]
    sects: Tuple[Mapping[str, Any], ...]
    sect_by_id: Mapping[str, Mapping[str, Any]]
    directions: Tuple[Mapping[str, Any], ...]
    direction_by_id: Mapping[str, Mapping[str, Any]]
    direction_node_by_id: Mapping[str, Mapping[str, Any]]
    sansxiu: Mapping[str, Any]
    encounters: Tuple[Mapping[str, Any], ...]
    encounter_by_id: Mapping[str, Mapping[str, Any]]
    lands: Tuple[Mapping[str, Any], ...]
    land_by_id: Mapping[str, Mapping[str, Any]]
    counts: Mapping[str, Any]
    load_order: Tuple[str, ...]

    # ---------------- 便捷派生 ----------------

    def enabled_realms(self) -> Tuple[Mapping[str, Any], ...]:
        """MVP 开放境界（enabled=true）。"""
        return tuple(r for r in self.realms if r.get("enabled"))

    def enabled_regions(self) -> Tuple[Mapping[str, Any], ...]:
        """MVP 开放区域。"""
        return tuple(r for r in self.regions if r.get("enabled"))

    def enabled_lands(self) -> Tuple[Mapping[str, Any], ...]:
        """MVP 开放禁地。"""
        return tuple(x for x in self.lands if x.get("enabled"))

    def realm_of_level(self, level: int) -> Mapping[str, Any]:
        """按全局等级定位境界。

        Raises:
            ValueError: 等级越界。
        """
        for realm in self.realms:
            if int(realm["level_start"]) <= level <= int(realm["level_end"]):
                return realm
        raise ValueError(f"level {level} is out of configured realm range")

    def realm_index_of_level(self, level: int) -> int:
        """按全局等级取大境序号（1–12）。"""
        return int(self.realm_of_level(level)["order"])

    def realm_exp_k_bp(self, level: int) -> int:
        """按等级取修为曲线系数 K（bp）。"""
        return int(self.realm_of_level(level)["exp_k_bp"])


# ---------------- 读取 ----------------

def _read_yaml(path: Path) -> Any:
    if not path.exists():
        raise ContentLoadError(f"content file not found: {path.name}")
    try:
        with path.open("r", encoding="utf-8") as fh:
            docs = [d for d in yaml.safe_load_all(fh) if d is not None]
    except yaml.YAMLError as exc:  # pragma: no cover - 数据侧触发
        raise ContentLoadError(f"yaml parse error in {path.name}: {exc}") from exc
    if not docs:
        raise ContentLoadError(f"empty content file: {path.name}")
    if len(docs) > 1:
        raise ContentLoadError(f"multi-document yaml not supported: {path.name}")
    return docs[0]


def content_hash_of(content_dir: Path, filenames: Sequence[str]) -> str:
    """按给定顺序拼接文件字节并求 SHA-256。"""
    digest = hashlib.sha256()
    for name in filenames:
        path = Path(content_dir) / name
        if not path.exists():
            raise ContentLoadError(f"content file not found: {name}")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _manifest_files(manifest: Mapping[str, Any]) -> List[str]:
    files = manifest.get("files")
    if not isinstance(files, (list, tuple)) or not files:
        raise ContentLoadError("manifest.files must be a non-empty list")
    ordered = sorted(files, key=lambda f: int(f.get("order", 0)))
    names: List[str] = []
    for entry in ordered:
        name = str(entry["name"])
        if not name.endswith(".yaml"):
            name = f"{name}.yaml"
        names.append(name)
    return names


def _payload(raw: Dict[str, Any], stem: str) -> Any:
    """按 _SECTIONS 取某文件的「有效载荷」。"""
    if stem not in _SECTIONS:
        raise ContentLoadError(f"unknown content file: {stem}.yaml")
    key, _is_list = _SECTIONS[stem]
    doc = raw[stem]
    if key == "__self__":
        return doc
    if key not in doc:
        raise ContentLoadError(f"{stem}.yaml: missing top-level key '{key}'")
    return doc[key]


def load_all(content_dir: Optional[Path | str] = None,
             *, validate: bool = True) -> Content:
    """加载全部内容（进程内缓存）。

    Args:
        content_dir: 内容目录；None 用 ``data/content``。
        validate: 是否执行 validators 自检（小样本测试可关掉）。

    Returns:
        ``Content`` 冻结快照。

    Raises:
        ContentLoadError: 文件缺失 / 解析失败 / 结构不符。
        ContentValidationError: 自检不过（数量 / 权重 / 引用 / 覆盖）。
    """
    directory = Path(content_dir) if content_dir is not None else CONTENT_DIR
    cache_key = f"{directory}|{validate}"
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached

    manifest_raw = _read_yaml(directory / "manifest.yaml")
    manifest = _freeze(manifest_raw)
    filenames = _manifest_files(manifest_raw)

    raw: Dict[str, Any] = {}
    for name in filenames:
        raw[name[:-5]] = _read_yaml(directory / name)

    game = _freeze(_payload(raw, "game"))
    realms = tuple(sorted((_freeze(x) for x in _payload(raw, "realms")),
                          key=lambda r: int(r["order"])))
    physiques = tuple(_freeze(x) for x in _payload(raw, "physiques"))
    talents = tuple(sorted((_freeze(x) for x in _payload(raw, "talents")),
                           key=lambda t: int(t["grade"])))
    personalities = tuple(_freeze(x) for x in _payload(raw, "personalities"))
    techniques = tuple(_freeze(x) for x in _payload(raw, "techniques"))
    items = tuple(_freeze(x) for x in _payload(raw, "items"))
    equipments = _freeze(_payload(raw, "equipments"))
    enemies = tuple(_freeze(x) for x in _payload(raw, "enemies"))
    regions = tuple(_freeze(x) for x in _payload(raw, "regions"))
    events = tuple(_freeze(x) for x in _payload(raw, "events"))
    quests = tuple(sorted((_freeze(x) for x in _payload(raw, "quests_main")),
                          key=lambda q: int(q["order"])))
    sects = tuple(_freeze(x) for x in _payload(raw, "sects"))
    directions = tuple(_freeze(d) for d in raw["sects"]["directions"])
    sansxiu = _freeze(_payload(raw, "sansxiu"))
    encounters = tuple(_freeze(e) for e in sansxiu["encounters"])
    lands = tuple(_freeze(x) for x in _payload(raw, "forbidden_lands"))

    node_index: Dict[str, Mapping[str, Any]] = {}
    for direction in directions:
        for layer in direction["layers"]:
            for node in layer["nodes"]:
                node_index[str(node["id"])] = node

    content = Content(
        content_version=str(manifest["content_version"]),
        schema_version=int(manifest.get("schema_version", 1)),
        content_hash=content_hash_of(directory, filenames),
        content_dir=directory,
        game=game,
        realms=realms,
        realm_by_id=_index(realms, "id"),
        realm_by_index=_index_int(realms, "order"),
        physiques=physiques,
        physique_by_id=_index(physiques, "id"),
        talents=talents,
        talent_by_grade=_index_int(talents, "grade"),
        personalities=personalities,
        personality_by_id=_index(personalities, "id"),
        techniques=techniques,
        technique_by_id=_index(techniques, "id"),
        items=items,
        item_by_id=_index(items, "id"),
        equipments=equipments,
        equip_template_by_id=_index(equipments["templates"], "id"),
        enemies=enemies,
        enemy_by_id=_index(enemies, "id"),
        regions=regions,
        region_by_id=_index(regions, "id"),
        events=events,
        event_by_id=_index(events, "id"),
        quests=quests,
        quest_by_id=_index(quests, "id"),
        sects=sects,
        sect_by_id=_index(sects, "id"),
        directions=directions,
        direction_by_id=_index(directions, "id"),
        direction_node_by_id=MappingProxyType(node_index),
        sansxiu=sansxiu,
        encounters=encounters,
        encounter_by_id=_index(encounters, "id"),
        lands=lands,
        land_by_id=_index(lands, "id"),
        counts=_freeze(manifest.get("counts", {})),
        load_order=tuple(["manifest.yaml"] + filenames),
    )

    if validate:
        from .validators import validate as _validate

        _validate(content)

    _CACHE[cache_key] = content
    return content


def reload_all(content_dir: Optional[Path | str] = None,
               *, validate: bool = True) -> Content:
    """清缓存并重新加载（测试 / 热更用）。"""
    _CACHE.clear()
    return load_all(content_dir, validate=validate)


# 缓存键 → Content
_CACHE: Dict[str, Content] = {}
