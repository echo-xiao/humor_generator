"""
joke_generator.py — 多路径笑话生成编排层
"""

from .knowledge.graph import load_graph, find_humor_slots, get_subgraph_triples, find_topic_node
from .knowledge.rag_retriever import load_memes, load_or_build_embeddings, retrieve as vector_retrieve
from .knowledge.graph_expander import expand_topic
from .methods import ALL_METHODS, rag_replace as rag_replace_fn

_rag_memes = None
_rag_embeddings = None


def _get_rag():
    global _rag_memes, _rag_embeddings
    if _rag_memes is None:
        _rag_memes = load_memes()
        _rag_embeddings = load_or_build_embeddings(_rag_memes)
    return _rag_memes, _rag_embeddings


def generate_jokes(topic: str, verbose: bool = True) -> list[dict]:
    if verbose:
        print(f"\n{'='*50}")
        print(f"话题：【{topic}】")

    G = load_graph()
    slots = find_humor_slots(G, topic, top_k=3)
    if not slots:
        if verbose:
            print(f"  图谱中未找到 [{topic}] 的 Humor Slot，触发图谱扩展...")
        G, _ = expand_topic(topic, G, methods=(1, 2, 3), verbose=verbose)
        slots = find_humor_slots(G, topic, top_k=3)
    has_slots = bool(slots)
    if not has_slots and verbose:
        print("  扩展后仍未找到 Humor Slot，跳过需要 slot 的方法")

    best_slot     = slots[0] if has_slots else None
    slot_name     = best_slot["slot"] if has_slots else ""
    slot_relation = best_slot["relation"] if has_slots else ""

    topic_node = find_topic_node(G, topic) or topic
    triples    = get_subgraph_triples(G, topic_node, slot_name, max_triples=6) if has_slots else []

    if has_slots and verbose:
        print(f"Humor Slot：【{slot_name}】  relation={slot_relation}")
        print(f"三元组数：{len(triples)}")

    triples_str = "\n".join(
        f"{'*' if t['high_value'] else '-'} ({t['subject']}, {t['relation']}, {t['object']})"
        for t in triples
    )

    memes, embeddings = _get_rag()
    retrieved = vector_retrieve(topic, slot_name or topic, top_k=3, memes=memes, embeddings=embeddings)
    memes_str = "\n---\n".join(retrieved) if retrieved else "（未找到相关梗）"

    context = {
        "G": G, "slots": slots, "slot_name": slot_name, "slot_relation": slot_relation,
        "triples": triples, "triples_str": triples_str, "memes": retrieved, "memes_str": memes_str,
    }

    candidates = []
    for method_name, method_fn in ALL_METHODS:
        if method_name in ("kg_contrast", "llm_assoc") and not has_slots:
            if verbose:
                print(f"  跳过 {method_name}（无 Humor Slot）")
            continue
        if verbose:
            print(f"\n生成方法 [{method_name}]...")
        results = method_fn(topic, context)
        for r in results:
            candidates.append(r)
            if verbose:
                print(f"  {r['joke']}")

    if verbose:
        print(f"\n生成方法 [rag_replace]...")
    context["candidates"] = candidates
    rag_results = rag_replace_fn(topic, context)
    if rag_results:
        candidates.extend(rag_results)
        if verbose:
            for r in rag_results:
                print(f"  {r['joke']}")
    elif verbose:
        print("  梗库无合适素材，跳过")

    return candidates
