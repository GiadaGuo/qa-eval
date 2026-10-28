from dataclasses import dataclass


class SkillNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class SkillDescriptor:
    key: str
    version: str
    name: str
    description: str
    dimension: str
    triggers: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    workflow: tuple[str, ...]


class SkillRegistry:
    """Small explicit registry; platform Metric Definitions remain authoritative."""

    def __init__(self) -> None:
        data_quality = SkillDescriptor(
            key="data-quality",
            version="1.0.0",
            name="Data Quality Skill",
            description="分析 Dataset Profile 与平台注册能力，组织数据质量评测流程。",
            dimension="DATA_QUALITY",
            triggers=("数据质量", "综合质量", "评测方案", "指标", "画像"),
            allowed_tools=(
                "get_dataset_profile",
                "discover_metrics",
                "recommend_evaluation_proposal",
            ),
            workflow=(
                "读取当前 Dataset Profile",
                "查询 DATA_QUALITY Metric、Calculator 与 Binding",
                "把确定性结果交给后续推荐服务，不自行编造候选或指标值",
            ),
        )
        self._skills = {data_quality.key: data_quality}

    def discover(self) -> list[SkillDescriptor]:
        return list(self._skills.values())

    def load(self, key: str) -> SkillDescriptor:
        try:
            return self._skills[key]
        except KeyError as exc:
            raise SkillNotFoundError(f"Skill not found: {key}") from exc

    def select(self, content: str) -> SkillDescriptor:
        normalized = content.casefold()
        for skill in self._skills.values():
            if any(trigger.casefold() in normalized for trigger in skill.triggers):
                return skill
        return self.load("data-quality")
