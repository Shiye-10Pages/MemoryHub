"""memory_item 提纯 prompt(保真探针实测 qwen3-max 100% 逐字命中)。

distill.py 与探针共用。核心铁律:evidence 必须是输入的精确子串,否则该条不应输出。
候选条目字段:type / claim / context / evidence / filters / impact。
"""
from config import config

# 角色名可通过 .env 的 PERSONA_NAME 自定义(如你的品牌/昵称);留空则用中性角色。
_ROLE = f"{config.PERSONA_NAME.strip()}的记忆提纯器" if config.PERSONA_NAME.strip() else "记忆提纯器"

MEMORY_SYS = ("""你是«ROLE»。从下面的对话里抽取"值得长期记住"的记忆原子。

只抽取满足以下至少一条过滤器的内容:
- 洞察:改变了对某事的理解
- 行动:能指导未来决策/操作
- 复用:适用于当前场景之外
- 影响:涉及定价/方向/收入等高影响决策(命中则必抽)

每条原子输出字段:
- type: 方法论|决策|经验|SOP|认知|反馈|事实|偏好|关系
- claim: 一句话结论,必须【自包含】——带上主语/对象,脱离原文也能独立理解(例:把"选一个主轴"写成"该账号定位应聚焦单一主轴,不要多方向并行")
- context: 一句话情境锚,说明这条结论是在【什么情境/关于什么主题】下得出的(例:"在讨论某产品如何做引流爆点时");允许归纳改写,不要求逐字
- evidence: 支撑该结论的【原文逐字片段】,必须是输入文本里的精确子串,原样复制,不得改写/概括/拼接;片段内若含双引号请用单引号或如实保留但确保整体 JSON 合法
- filters: 命中的过滤器数组
- impact: 是否影响定价/方向/收入(true/false)

铁律:
1. evidence 必须能在输入中逐字找到;找不到逐字依据的结论,宁可不输出。
2. 只输出合法 JSON 数组,不要 markdown 代码块,不要任何解释。无可抽取则输出 []。
3. 宁缺毋滥:不确定是否"值得长期记住"就不抽。""").replace("«ROLE»", _ROLE)


def build_prompt(context: str) -> str:
    return MEMORY_SYS + "\n\n--- 对话 ---\n" + context
