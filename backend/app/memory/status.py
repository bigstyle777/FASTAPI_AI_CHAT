"""记忆生命周期状态常量（Phase 5-6）。

memories.status 列在 Phase 1-3 只建列、写死默认 "active"，没有逻辑。
从 Phase 5（Consolidation 标记原文）开始真正读写它，所有模块必须引用
这里的常量，不要散落魔法字符串 —— 状态值将来出现在 SQL WHERE、
检索过滤、任务日志里，一旦各处手写就容易拼错且改不动。
"""

# 默认状态：正常使用中的记忆（models.py 的列默认值也是它）
ACTIVE = "active"

# Phase 6：被反复访问（reinforce 累计达阈值）的记忆，比普通 ACTIVE
# 更"热"。检索把它和 ACTIVE 同等对待，生命周期上只是更晚才考虑退化。
REINFORCED = "reinforced"

# Phase 5：已被 consolidate 吸收进更高层总结。
# 原文不物理删除（可溯源），但不再是独立检索对象。
CONSOLIDATED = "consolidated"

# Phase 6：进入退化观察 / 已归档。DECAYING 仍可被检索到（给用户
# 最后一次机会把它救回来），ARCHIVED 不再进入任何检索。
DECAYING = "decaying"
ARCHIVED = "archived"
