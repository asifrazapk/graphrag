"""Draws graph_rag_architecture.png (run: python make_graph_rag_architecture.py)."""
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

BG = "#000000"
fig, ax = plt.subplots(figsize=(14, 8.4), dpi=100)
fig.patch.set_facecolor(BG)
ax.set_facecolor(BG)
ax.set_xlim(0, 100)
ax.set_ylim(0, 60)
ax.axis("off")

C_FILE, C_LLM, C_DB, C_USER, C_SCALE = "#1f6fb2", "#3b8f7b", "#1b7bb5", "#555b66", "#c98a1a"


def box(x0, x1, yc, h, text, color, fs=10.5, edge=None):
    p = FancyBboxPatch((x0, yc - h / 2), x1 - x0, h, boxstyle="round,pad=0.25,rounding_size=1.2",
                       fc=color, ec=edge or color, lw=1.5)
    ax.add_patch(p)
    ax.text((x0 + x1) / 2, yc, text, ha="center", va="center", color="white", fontsize=fs, fontweight="bold",
            wrap=True)


def arrow(x1, y1, x2, y2, label=None, color="#9fb3c8"):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle="-|>", color=color, lw=1.8, shrinkA=0, shrinkB=0))
    if label:
        ax.text((x1 + x2) / 2, (y1 + y2) / 2 + 1.1, label, ha="center", color=color, fontsize=8.5, style="italic")


def title(x, y, s, color="#e6e6e6"):
    ax.text(x, y, s, color=color, fontsize=12.5, fontweight="bold", ha="left", va="center")


ax.text(50, 58.3, "Graph RAG: Neo4j Aura + LLM (Groq, swappable)", color="white", fontsize=17,
        fontweight="bold", ha="center")

# ---------------- Ingest ----------------
title(2, 53.2, "1. INGEST  - build the graph")
box(2, 14, 45, 7, "reviews/*.txt\nscale_review/*.txt", C_FILE, 9.5)
box(20, 39, 45, 9, "LLM\nExtract entities and\nrelationships (triples)", C_LLM)
box(45, 64, 45, 9, "LLM\nGenerate Cypher\n(CREATE / MERGE)", C_LLM)
arrow(14.4, 45, 19.6, 45)
arrow(39.4, 45, 44.6, 45, "triples")
arrow(64.4, 45, 84.6, 45, "Cypher")

# ---------------- Neo4j (shared) ----------------
box(85, 98, 33, 30, "Neo4j Aura\n\nGraph database\n\nHotel, Location,\nFacilities,\nCustomerType,\nReviewer", C_DB, 10.5, edge="#6cc24a")

# ---------------- Query ----------------
title(2, 36.7, "2. QUERY  - answer questions")
box(2, 14, 29, 8, "User question\n\"Which hotels do\nbusinessmen visit?\"", C_USER, 9)
box(20, 39, 29, 9, "LLM\nQuestion -> Cypher", C_LLM)
arrow(14.4, 29, 19.6, 29)
arrow(39.4, 29, 84.6, 29, "MATCH ... RETURN (bounded hops, LIMIT)")
box(62, 80, 19, 9, "LLM\nSummarise the\nquery results", C_LLM)
arrow(85, 19, 80.4, 19, "records")
box(36, 54, 19, 7, "Friendly answer\nto the user", C_USER)
arrow(61.6, 19, 54.4, 19)

# ---------------- Scale layer ----------------
ax.add_patch(FancyBboxPatch((1, 2), 98, 10.2, boxstyle="round,pad=0.2,rounding_size=1.2", fc="none",
                            ec=C_SCALE, lw=1.4, ls="--"))
ax.text(2.2, 10.8, "AT SCALE (graph_rag_at_scale.ipynb): millions of nodes", color=C_SCALE, fontsize=11,
        fontweight="bold", va="center")
xs = [(3, 19, "Queue\n(SQS / Kafka / jobs)"),
      (23, 41, "Parallel workers\n+ rate limiter + retry"),
      (45, 62, "Entity resolution\n(normalize, block, verify)"),
      (66, 84, "Batched UNWIND MERGE\n+ unique constraints"),
      ]
for x0, x1, t in xs:
    box(x0, x1, 5.6, 4.6, t, "#3a2a08", 9, edge=C_SCALE)
for (a, b) in zip(xs[:-1], xs[1:]):
    arrow(a[1] + 0.3, 5.6, b[0] - 0.3, 5.6, color=C_SCALE)
box(87, 97.5, 5.6, 4.6, "neo4j-admin\nimport (bulk)", "#3a2a08", 8.5, edge=C_SCALE)
arrow(84.3, 5.6, 86.7, 5.6, color=C_SCALE)

fig.savefig("graph_rag_architecture.png", facecolor=BG, bbox_inches="tight", pad_inches=0.2)
print("wrote graph_rag_architecture.png")
