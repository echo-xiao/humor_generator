"""
visualize.py — 知识图谱可视化

用法：
  python -m src.knowledge.visualize --topic 结婚
  python -m src.knowledge.visualize --topic 结婚 --hops 2 --min-hw 3
  python -m src.knowledge.visualize --topic 上班 --only-humor

生成交互式 HTML 文件，浏览器打开可缩放、拖拽、悬停看详情。
"""

import argparse
import os
from .graph import load_graph, find_topic_node, SOURCE_WEIGHTS, YOUTUBE_SOURCE_WEIGHT


def visualize_topic(topic, hops=1, min_hw=0, only_humor=False, output=None):
    from pyvis.network import Network

    G = load_graph()
    center = find_topic_node(G, topic, semantic_fallback=False)
    if not center:
        print(f"Topic '{topic}' not found in graph")
        return

    # 收集周围节点
    nodes_to_show = {center}
    edges_to_show = []

    def collect(node, depth):
        if depth > hops:
            return
        for _, obj, data in G.out_edges(node, data=True):
            hw = data.get("humor_weight", 0)
            if hw < min_hw:
                continue
            sources = data.get("sources", set())
            if isinstance(sources, list):
                sources = set(sources)
            if only_humor:
                humor_srcs = {s for s, w in SOURCE_WEIGHTS.items() if w >= 3.0}
                has_yt = any(s.startswith("youtube_") for s in sources)
                if not (sources & humor_srcs) and not has_yt:
                    continue
            nodes_to_show.add(obj)
            edges_to_show.append((node, obj, data))
            if depth < hops:
                collect(obj, depth + 1)

        for subj, _, data in G.in_edges(node, data=True):
            hw = data.get("humor_weight", 0)
            if hw < min_hw:
                continue
            sources = data.get("sources", set())
            if isinstance(sources, list):
                sources = set(sources)
            if only_humor:
                humor_srcs = {s for s, w in SOURCE_WEIGHTS.items() if w >= 3.0}
                has_yt = any(s.startswith("youtube_") for s in sources)
                if not (sources & humor_srcs) and not has_yt:
                    continue
            nodes_to_show.add(subj)
            edges_to_show.append((subj, node, data))
            if depth < hops:
                collect(subj, depth + 1)

    collect(center, 1)

    print(f"Topic: {topic} -> {len(nodes_to_show)} nodes, {len(edges_to_show)} edges")

    # 构建 pyvis 网络
    net = Network(height="800px", width="100%", bgcolor="#1a1a2e", font_color="white",
                  directed=True, notebook=False)
    net.barnes_hut(gravity=-3000, central_gravity=0.3, spring_length=200)

    # 颜色映射
    def node_color(n):
        if n == center:
            return "#f03e3e"  # 中心话题：红色
        sent = G.nodes[n].get("sentiment", None)
        if sent is not None:
            if sent > 0.3:
                return "#51cf66"  # 正面：绿色
            elif sent < -0.3:
                return "#ff6b6b"  # 负面：红色
            else:
                return "#ffa94d"  # 中性：橙色
        return "#868e96"  # 无标注：灰色

    def node_size(n):
        if n == center:
            return 40
        return max(15, min(35, G.degree(n) * 0.5))

    def edge_color(data):
        sources = data.get("sources", set())
        if isinstance(sources, list):
            sources = set(sources)
        if "妈的欧洲账本" in sources:
            return "#f03e3e"  # 红：最高价值
        if sources & {"脱口秀大咖", "脱口秀集锦"}:
            return "#fab005"  # 黄：脱口秀
        if any(s.startswith("youtube_") for s in sources):
            return "#fab005"
        if "歇后语" in sources:
            return "#f783ac"  # 粉：歇后语
        if "homophone" in sources:
            return "#748ffc"  # 蓝：谐音
        return "#495057"  # 灰：ConceptNet/成语

    # 添加节点
    for n in nodes_to_show:
        sent = G.nodes[n].get("sentiment", "?")
        domain = G.nodes[n].get("domain", "?")
        srcs = G.nodes[n].get("sources", set())
        if isinstance(srcs, list):
            srcs = set(srcs)
        title = f"{n}\nsentiment={sent}\ndomain={domain}\nsources={','.join(srcs)}\ndegree={G.degree(n)}"
        net.add_node(n, label=n, color=node_color(n), size=node_size(n), title=title)

    # 添加边
    for u, v, data in edges_to_show:
        hw = data.get("humor_weight", 0)
        rels = data.get("relations", ["?"])
        sources = data.get("sources", set())
        if isinstance(sources, list):
            sources = set(sources)
        rel = rels[0] if rels else "?"
        title = f"{rel}\nhw={hw:.1f}\nsrc={','.join(sources)}"
        width = max(1, min(6, hw / 2))
        net.add_edge(u, v, label=rel, title=title, color=edge_color(data),
                     width=width, arrows="to")

    # 输出
    if output is None:
        output = f"graph_{topic}.html"
    net.save_graph(output)
    abs_path = os.path.abspath(output)
    print(f"Saved: {abs_path}")
    print(f"Open in browser: file://{abs_path}")
    return abs_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Visualize knowledge graph around a topic")
    parser.add_argument("--topic", required=True, help="Center topic node")
    parser.add_argument("--hops", type=int, default=1, help="Number of hops from center (default: 1)")
    parser.add_argument("--min-hw", type=float, default=0, help="Minimum humor_weight to show (default: 0)")
    parser.add_argument("--only-humor", action="store_true", help="Only show edges from humor sources")
    parser.add_argument("--output", type=str, default=None, help="Output HTML file path")
    args = parser.parse_args()

    visualize_topic(args.topic, hops=args.hops, min_hw=args.min_hw,
                    only_humor=args.only_humor, output=args.output)
