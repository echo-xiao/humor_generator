"""
methods/ — 幽默生成方法注册表

每个方法模块暴露统一接口：
    generate(topic: str, context: dict = None) -> list[dict]

返回值格式：
    [{"method": str, "joke": str, "slot": str, "triples": list}]
"""

from .kg_contrast import generate as kg_contrast
from .rag_replace import generate as rag_replace
from .llm_assoc import generate as llm_assoc
from .semantic_dist import generate as semantic_dist
from .context_shift import generate as context_shift
from .expectation import generate as expectation
from .name_analysis import generate as name_analysis
from .homophone import generate as homophone
from .ambiguity import generate as ambiguity
from .ironic_reversal import generate as ironic_reversal
from .false_analogy import generate as false_analogy
from .self_contradiction import generate as self_contradiction
from .hyperbolic_deflation import generate as hyperbolic_deflation
from .self_deprecation import generate as self_deprecation
from .xiehouyu_gen import generate as xiehouyu_gen
from .concretize import generate as concretize

# 15路并行生成方法
# rag_replace 不在此列表中，由 joke_generator 在生成后单独调用
ALL_METHODS = [
    ("kg_contrast", kg_contrast),
    ("llm_assoc", llm_assoc),
    ("semantic_dist", semantic_dist),
    ("context_shift", context_shift),
    ("expectation", expectation),
    ("name_analysis", name_analysis),
    ("homophone", homophone),
    ("ambiguity", ambiguity),
    ("ironic_reversal", ironic_reversal),
    ("false_analogy", false_analogy),
    ("self_contradiction", self_contradiction),
    ("hyperbolic_deflation", hyperbolic_deflation),
    ("self_deprecation", self_deprecation),
    ("xiehouyu_gen", xiehouyu_gen),
    ("concretize", concretize),
]

__all__ = ["ALL_METHODS", "rag_replace"]
