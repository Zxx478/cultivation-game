"""业务错误码常量。

API 层使用这些错误码组装 ErrorResponse，便于客户端分类处理。
"""
# 通用
OK = 0
GENERIC_ERROR = 5000

# 任务接取相关 1xxx
ERR_QUEST_INVALID_LEVEL = 1001      # 任务等级参数非法
ERR_QUEST_INVALID_SECT = 1002         # 宗门 ID 非法
ERR_QUEST_DAILY_CAP_REACHED = 1003    # 该等级任务已达当日上限
ERR_QUEST_ROLE_NOT_READY = 1004       # 当前身份未达该任务接取资格（散修未到好感阈值、弟子未到解锁阈值）
ERR_QUEST_SANXIU_CANNOT_TAKE_HIGH = 1005    # 散修不可接 high 任务（硬规则）
ERR_QUEST_NOT_FOUND = 1006            # 任务不存在
ERR_QUEST_WRONG_OWNER = 1007          # 任务不属于该角色
ERR_QUEST_WRONG_STATUS = 1008         # 任务状态不可完成（已 completed / expired）
ERR_QUEST_QUEST_EXPIRED = 1009         # 任务已过期

# 兑换相关 2xxx
ERR_EXCHANGE_INVALID_ITEM = 2001
ERR_EXCHANGE_INSUFFICIENT_CONTRIBUTION = 2002    # 贡献点不足
ERR_EXCHANGE_INSUFFICIENT_REPUTATION = 2003       # 散修好感度不足
ERR_EXCHANGE_SACRED_TECH_FORBIDDEN_FOR_SANXIU = 2004    # 散修好感度门槛阻拦圣阶功法兑换
ERR_EXCHANGE_ITEM_NOT_FOUND = 2005                # v1.7 新增：物品在 technique/pill/equipment 表中不存在

# 禁地相关 3xxx
ERR_FORBIDDEN_LAND_INVALID_ID = 3001
ERR_FORBIDDEN_LAND_REALM_TOO_LOW = 3002
ERR_FORBIDDEN_LAND_CAP_REACHED = 3003     # 本游戏年进入次数已满
ERR_FORBIDDEN_LAND_IN_COOLDOWN = 3004      # 冷却未过
ERR_FORBIDDEN_LAND_INSUFFICIENT_CONTRIBUTION = 3005    # 贡献点不足支付进入消耗


class SectServiceError(Exception):
    """Service 层异常基类，携带错误码与提示。"""

    def __init__(self, code: int, message: str, detail: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail
