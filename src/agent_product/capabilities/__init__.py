from agent_product.capabilities.base import CapabilityRegistry
from agent_product.capabilities.common import PACK as COMMON_PACK
from agent_product.capabilities.knowledge import PACK as KNOWLEDGE_PACK
from agent_product.capabilities.skills import PACK as SKILLS_PACK
from agent_product.capabilities.support import PACK as SUPPORT_PACK
from agent_product.capabilities.web import PACK as WEB_PACK
from agent_product.capabilities.workspace import READ_PACK, WRITE_PACK


def build_capability_registry() -> CapabilityRegistry:
    return CapabilityRegistry(
        (
            COMMON_PACK,
            WEB_PACK,
            KNOWLEDGE_PACK,
            SKILLS_PACK,
            READ_PACK,
            WRITE_PACK,
            SUPPORT_PACK,
        )
    )


__all__ = ["build_capability_registry"]
