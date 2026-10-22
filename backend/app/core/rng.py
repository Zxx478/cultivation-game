"""可复现随机源（RngService）。

设计要点（system design §2.2 / 安全基线 §9）：
- 派生：``HMAC-SHA256(seed, f"{stream}:{counter}")`` → 32 字节摘要 → 整数；
- **counter 由调用方在同事务内自增并落库**，本类不隐式推进，
  因此"同一 (seed, counter, stream) 必得同一结果"——刷新页面不重 roll；
- 不同域用不同 ``stream``（event / combat / drop / breakthrough），互不干扰；
- 禁止使用标准库 ``random`` 生成奖励/突破结果。

用法::

    rng = RngService(seed="ab12...", counter=17)
    if rng.roll_bp(3500, stream="event"):
        ...
    counter = char.rng_counter + 1   # 由 service 在同事务内落库
"""
from __future__ import annotations

import hashlib
import hmac
import math
from typing import Iterable, List, Mapping, Sequence, Tuple, TypeVar

T = TypeVar("T")


class RngService:
    """基于 HMAC-SHA256 的确定性随机源。"""

    __slots__ = ("_seed", "_counter")

    def __init__(self, seed: str | bytes, counter: int = 0) -> None:
        """初始化。

        Args:
            seed: 角色级随机种子（32 字节 hex 字符串或 bytes），绝不下发前端。
            counter: 当前随机计数器；应与 ``character.rng_counter`` 保持一致。

        Raises:
            ValueError: seed 为空或 counter 为负。
        """
        if seed is None or (isinstance(seed, str) and not seed):
            raise ValueError("rng seed must be a non-empty str or bytes")
        if counter < 0:
            raise ValueError("rng counter must be >= 0")
        self._seed: bytes = seed.encode("utf-8") if isinstance(seed, str) else bytes(seed)
        self._counter: int = int(counter)

    # ---------------- 属性 ----------------

    @property
    def counter(self) -> int:
        """当前计数器值（只读）。"""
        return self._counter

    def advance(self, steps: int = 1) -> int:
        """推进计数器并返回**推进后**的值（供不便外部落库的场景使用）。"""
        if steps < 0:
            raise ValueError("steps must be >= 0")
        self._counter += steps
        return self._counter

    def with_counter(self, counter: int) -> "RngService":
        """返回同种子、指定计数器的派生实例（不改自身）。"""
        return RngService(self._seed, counter)

    # ---------------- 派生 ----------------

    def digest(self, stream: str = "default", counter: int | None = None) -> bytes:
        """派生 32 字节摘要。

        Args:
            stream: 随机域标识（event / combat / drop / breakthrough / ...）。
            counter: 覆盖计数器；None 表示用实例当前值。
        """
        use = self._counter if counter is None else int(counter)
        msg = f"{stream}:{use}".encode("utf-8")
        return hmac.new(self._seed, msg, hashlib.sha256).digest()

    def next_int(self, stream: str = "default", *, bits: int = 64,
                 counter: int | None = None) -> int:
        """取 ``bits`` 位无符号整数（确定性）。"""
        if bits <= 0 or bits > 256:
            raise ValueError("bits must be within 1..256")
        raw = int.from_bytes(self.digest(stream, counter), "big")
        return raw >> (256 - bits)

    def next_bp(self, stream: str = "default", counter: int | None = None) -> int:
        """取 [0, 10000) 的基点整数（10000bp = 100%）。"""
        return self.next_int(stream, bits=64, counter=counter) % 10000

    def next_modulo(self, modulo: int, stream: str = "default",
                    counter: int | None = None) -> int:
        """取 [0, modulo) 的整数（通用取模，供抽签/索引复用）。

        Raises:
            ValueError: modulo <= 0。
        """
        if modulo <= 0:
            raise ValueError("modulo must be > 0")
        return self.next_int(stream, bits=64, counter=counter) % int(modulo)

    def roll_bp(self, bp: int, stream: str = "default",
                counter: int | None = None) -> bool:
        """按基点概率掷一次骰子。

        Args:
            bp: 概率基点（0–10000）；<=0 恒 False，>=10000 恒 True。
        """
        if bp <= 0:
            return False
        if bp >= 10000:
            return True
        return self.next_bp(stream, counter) < bp

    def random_float(self, stream: str = "default",
                     counter: int | None = None) -> float:
        """取 [0, 1) 浮点数（53 位精度）。"""
        value = self.next_int(stream, bits=64, counter=counter) >> 11
        return value / float(1 << 53)

    def pick(self, seq: Sequence[T], stream: str = "default",
             counter: int | None = None) -> T:
        """等概率取一个元素。

        Raises:
            ValueError: 序列为空。
        """
        if not seq:
            raise ValueError("cannot pick from empty sequence")
        idx = self.next_int(stream, bits=64, counter=counter) % len(seq)
        return seq[idx]

    def weighted_pick(self, items: Iterable[Tuple[T, int]], stream: str = "default",
                      counter: int | None = None) -> T:
        """按权重抽取元素。

        Args:
            items: ``[(key, weight), ...]``，权重为整数，非正值会被跳过。

        Raises:
            ValueError: 有效权重合计 <= 0。
        """
        pool: List[Tuple[T, int]] = [(k, int(w)) for k, w in items if int(w) > 0]
        total = sum(w for _, w in pool)
        if total <= 0:
            raise ValueError("weighted_pick requires positive total weight")
        point = self.next_int(stream, bits=64, counter=counter) % total
        acc = 0
        for key, weight in pool:
            acc += weight
            if point < acc:
                return key
        return pool[-1][0]

    # ---------------- 标准库兼容适配器 ----------------

    def randint(self, a: int, b: int, stream: str = "default",
                counter: int | None = None) -> int:
        """闭区间整数（等价 ``random.randint``）。"""
        if b < a:
            raise ValueError("b must be >= a")
        return a + self.next_int(stream, bits=64, counter=counter) % (b - a + 1)

    def uniform(self, a: float, b: float, stream: str = "default",
                counter: int | None = None) -> float:
        """区间浮点（等价 ``random.uniform``）。"""
        return a + (b - a) * self.random_float(stream, counter=counter)

    def gauss(self, mu: float, sigma: float, stream: str = "default",
              counter: int | None = None) -> float:
        """近似正态（Box-Muller，确定性）。"""
        u1 = max(self.random_float(f"{stream}:g1", counter=counter), 1e-12)
        u2 = self.random_float(f"{stream}:g2", counter=counter)
        z = math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
        return mu + sigma * z

    # ---------------- 调试辅助 ----------------

    def shuffle(self, seq: Sequence[T], stream: str = "default",
                counter: int | None = None) -> List[T]:
        """确定性洗牌（Fisher-Yates，使用派生流）。"""
        arr = list(seq)
        for i in range(len(arr) - 1, 0, -1):
            j = self.next_int(f"{stream}:shuffle:{i}", bits=64,
                              counter=counter) % (i + 1)
            arr[i], arr[j] = arr[j], arr[i]
        return arr

    def snapshot(self) -> Mapping[str, int]:
        """返回可落库的快照（**不含种子**）。"""
        return {"counter": self._counter}

    def __repr__(self) -> str:  # pragma: no cover - 仅调试
        return f"RngService(counter={self._counter})"
