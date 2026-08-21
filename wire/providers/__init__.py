"""Semantic providers. One interface, three implementations."""
from .rules import RulesSemanticProvider          # noqa: F401
from .claude import ClaudeSemanticProvider        # noqa: F401
from .openai import OpenAISemanticProvider        # noqa: F401

REGISTRY = {
    "rules": RulesSemanticProvider,
    "claude": ClaudeSemanticProvider,
    "openai": OpenAISemanticProvider,
}
