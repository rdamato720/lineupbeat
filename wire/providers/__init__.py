"""Active semantic providers. Anthropic is retained only as legacy code."""
from .rules import RulesSemanticProvider          # noqa: F401
from .openai import OpenAISemanticProvider        # noqa: F401

REGISTRY = {
    "rules": RulesSemanticProvider,
    "openai": OpenAISemanticProvider,
}
