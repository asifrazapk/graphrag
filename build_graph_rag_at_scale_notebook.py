"""Generates graph_rag_at_scale.ipynb (run once: python build_graph_rag_at_scale_notebook.py)."""
import json

cells = []


def md(s):
    cells.append({"cell_type": "markdown", "metadata": {}, "source": s.strip("\n").splitlines(keepends=True)})


def code(s):
    cells.append({"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [],
                  "source": s.strip("\n").splitlines(keepends=True)})


md('''
# Graph RAG at scale: millions of nodes

**Interview question:** "Your Graph RAG demo works on 5 documents. How do you make it process millions of nodes, and how do you optimize it?"

This notebook answers that with a structure plus small runnable demos. Everything runs **offline** (no API key, no database) unless you flip `RUN_NEO4J`.

## The answer in one table

| Stage | What breaks at scale | Fix | Demo below |
|---|---|---|---|
| 1. LLM extraction | Slow, costly, rate limits (we already hit a 429) | Parallel workers + rate limiter + retry/backoff, cheap model, batch API, **idempotent checkpointing** | sections 2-3 |
| 2. Entity resolution | "Acme", "ACME Inc", "Acme Corp." become separate nodes | Normalize, block, then fuzzy/embedding match; careful with false merges | section 4 |
| 3. Writing to the graph | One round trip per node/edge | **Batched `UNWIND ... MERGE`**, unique constraints first, partition writes, bulk import for initial load | sections 5-6 |
| 4. Graph shape | Supernodes make k-hop queries explode | Degree caps, relation filters, community summaries | section 7 |
| 5. Query time | Fulltext on every question, huge subgraphs | Indexes (fulltext/vector), limits, caching, hierarchical retrieval | section 8 |
| 6. Capacity planning | "How long and how much?" | Back-of-envelope math | section 9 |
''')

code('''
import os, time, random, hashlib, json, re, sqlite3, tempfile, threading
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from collections import Counter
import networkx as nx

random.seed(7)
N_DOCS = 400          # synthetic documents (scale this up to feel the difference)
LLM_LATENCY = 0.05    # seconds per simulated LLM call
RUN_NEO4J = False     # True = run section 6 against your Neo4j (writes/cleans only :BenchEntity nodes)
''')

md('''
## 1. Synthetic data
Companies appear under **messy aliases** ("Company 5", "COMPANY 5", "Co 5", "Company 5 Inc"), exactly the problem real text has.
''')

code('''
COMPANIES = [f"Company {i}" for i in range(300)]

def alias(n):
    return random.choice([n, n.upper(), n.lower(), n + " Inc", n.replace("Company", "Co")])

def make_docs(n):
    docs = []
    for i in range(n):
        a, b = random.sample(COMPANIES, 2)
        docs.append(f"doc{i}: {alias(a)} acquired {alias(b)}. {alias(a)} is located in City {(1 if random.random() < 0.3 else random.randint(2, 40))}.")
    return docs

docs = make_docs(N_DOCS)
print(len(docs), "docs; example:", docs[0])
''')

md('''
## 2. Extraction stage: parallelism + rate limiting + retries
The LLM call dominates runtime. Sequential is hopeless at millions of documents. The fix is a **worker pool** with a **shared rate limiter** (so you respect provider limits instead of crashing with 429) and **exponential backoff**.

`fake_llm_extract` stands in for a real LLM call (fixed latency, random 429s).
''')

code('''
class RateLimitError(Exception):
    pass

def fake_llm_extract(doc):
    time.sleep(LLM_LATENCY)
    if random.random() < 0.05:                      # simulate provider 429s
        raise RateLimitError("429")
    m = re.match(r"doc\\d+: (.+?) acquired (.+?)\\. (.+?) is located in (.+?)\\.", doc)
    a, b, c, city = m.groups()
    return [(a, "acquired", b), (c, "is located in", city)]

class RateLimiter:
    """Allow at most `rate` calls/second across all threads."""
    def __init__(self, rate):
        self.interval, self.lock, self.next = 1.0 / rate, threading.Lock(), 0.0
    def wait(self):
        with self.lock:
            now = time.monotonic()
            delay = max(0.0, self.next - now)
            self.next = max(now, self.next) + self.interval
        if delay:
            time.sleep(delay)

def extract_with_retry(doc, limiter, retries=5):
    for attempt in range(retries + 1):
        limiter.wait()
        try:
            return fake_llm_extract(doc)
        except RateLimitError:
            time.sleep(min(0.05 * 2 ** attempt, 1.0))   # backoff
    return []                                            # give up on this doc; log it in real life
''')

code('''
sample = docs[:100]

# Sequential baseline (with the same retry logic, no concurrency)
limiter = RateLimiter(rate=1000)
t = time.time()
seq = [extract_with_retry(d, limiter) for d in sample]
t_seq = time.time() - t

# Parallel: 16 workers, 100 calls/second cap
limiter = RateLimiter(rate=100)
t = time.time()
with ThreadPoolExecutor(max_workers=16) as pool:
    par = list(pool.map(lambda d: extract_with_retry(d, limiter), sample))
t_par = time.time() - t

print(f"sequential: {t_seq:.1f}s   parallel: {t_par:.1f}s   speedup: {t_seq / t_par:.1f}x")
print("same triples count:", sum(map(len, seq)), sum(map(len, par)))
''')

md('''
**Interview points:** use the cheapest model that extracts reliably, send **batches** where the provider has a batch API (typically much cheaper, higher limits, asynchronous), and put documents on a **queue** (SQS/Kafka/Databricks job) so workers scale horizontally and failures are retried independently.
''')

md('''
## 3. Idempotent checkpointing
At millions of docs, jobs **will** crash halfway. Key every document by a content hash and record completion, so a rerun does zero duplicate work (and zero duplicate LLM spend).
''')

code('''
ckpt = sqlite3.connect(os.path.join(tempfile.mkdtemp(), "ckpt.db"))
ckpt.execute("CREATE TABLE IF NOT EXISTS done (h TEXT PRIMARY KEY)")

def doc_hash(d):
    return hashlib.sha256(d.encode()).hexdigest()

def process(docs_in):
    todo = [d for d in docs_in if not ckpt.execute("SELECT 1 FROM done WHERE h=?", (doc_hash(d),)).fetchone()]
    limiter = RateLimiter(rate=500)
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda d: extract_with_retry(d, limiter), todo))
    for d in todo:
        ckpt.execute("INSERT OR IGNORE INTO done VALUES (?)", (doc_hash(d),))
    ckpt.commit()
    return todo, results

todo, res1 = process(docs[:200])
print("first run processed:", len(todo))
todo2, _ = process(docs[:200])
print("rerun processed:    ", len(todo2), "(nothing repeated)")
todo3, res3 = process(docs[:300])
print("after adding docs:  ", len(todo3), "(only the new ones)")
''')

code('''
# Extract everything once for the rest of the notebook
all_triples = []
limiter = RateLimiter(rate=500)
with ThreadPoolExecutor(max_workers=16) as pool:
    for ts in pool.map(lambda d: extract_with_retry(d, limiter), docs):
        all_triples.extend(ts)
print(len(all_triples), "triples")
''')

md('''
## 4. Entity resolution (the quiet graph-quality killer)
Without it, one real company becomes many nodes, so multi-hop queries break. A scalable recipe:
1. **Normalize** (cheap, exact): lowercase, strip punctuation, expand/remove suffixes.
2. **Block**: only compare candidates that share a key (never all-pairs, since N² is impossible at millions).
3. **Fuzzy/embedding match inside blocks**, and send only *ambiguous* pairs to an LLM.

Careful: fuzzy matching creates **false merges**.
''')

code('''
def norm(name):
    s = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    s = re.sub(r"\\bco\\b", "company", s)
    s = re.sub(r"\\binc\\b", "", s)
    return re.sub(r"\\s+", " ", s).strip()

raw_entities = {x for s, _, o in all_triples for x in (s, o)}
resolved = {norm(e) for e in raw_entities}
print(f"raw entity strings: {len(raw_entities)}  ->  after normalization: {len(resolved)}")

print()
print("Fuzzy danger:", SequenceMatcher(None, "company 5", "company 50").ratio(),
      "similarity between two DIFFERENT companies, so a naive 0.9 threshold would merge them.")
''')

md('''
So: normalize aggressively (safe), but treat fuzzy matches as **candidates**, not truth. Verify with embeddings plus extra attributes, or an LLM, and keep a reversible `alias_of` mapping rather than deleting data.
''')

md('''
## 5. Write path: why batching is the biggest single win
Per-row writes pay a network round trip and a transaction commit **each**. Batching amortizes both. This demo uses SQLite as an analogy (the same effect, more dramatic over a network to Neo4j Aura).
''')

code('''
path = os.path.join(tempfile.mkdtemp(), "w.db")
conn = sqlite3.connect(path)
conn.execute("CREATE TABLE edges (s TEXT, r TEXT, o TEXT)")
rows = [(a, r, b) for a, r, b in all_triples][:1000]

t = time.time()
for row in rows:                                   # naive: commit per row
    conn.execute("INSERT INTO edges VALUES (?,?,?)", row)
    conn.commit()
t_naive = time.time() - t

conn.execute("DELETE FROM edges"); conn.commit()
t = time.time()
conn.executemany("INSERT INTO edges VALUES (?,?,?)", rows)   # batched: one transaction
conn.commit()
t_batch = time.time() - t
print(f"per-row commit: {t_naive:.2f}s   batched: {t_batch:.3f}s   -> {t_naive / max(t_batch, 1e-9):.0f}x faster")
''')

md('''
### The Neo4j version of the same idea
```cypher
// 1. ALWAYS create the uniqueness constraint first (it also gives you an index for MERGE)
CREATE CONSTRAINT entity_key IF NOT EXISTS FOR (e:Entity) REQUIRE e.key IS UNIQUE;

// 2. Batched, idempotent upsert: send ~5-10k rows per query
UNWIND $rows AS row
MERGE (a:Entity {key: row.sk}) ON CREATE SET a.name = row.s
MERGE (b:Entity {key: row.ok}) ON CREATE SET b.name = row.o
MERGE (a)-[:REL {type: row.r}]->(b)
```
**Interview details that score points**
- Without the constraint, every `MERGE` is a full label scan, so it gets slower as the graph grows (O(n²) overall).
- Parallel writers cause **deadlocks** on shared nodes. Partition work (e.g. by `hash(subject) % workers`) or write nodes first, then relationships.
- For the **initial load of tens of millions+**, skip the transactional path: use `neo4j-admin database import` (CSV, offline, orders of magnitude faster), then switch to incremental batched writes.
- `apoc.periodic.iterate` batches server-side work. Spark/Databricks can generate the node and edge CSVs at scale.
''')

md('''
## 6. (Optional) Benchmark against your Neo4j
Off by default. When `RUN_NEO4J = True` it writes **only** nodes labelled `:BenchEntity` and deletes them afterwards. It does not touch your hotel graph. Reads credentials from `.env`.
''')

code('''
if RUN_NEO4J:
    from dotenv import load_dotenv
    from neo4j import GraphDatabase
    load_dotenv()
    driver = GraphDatabase.driver(os.environ["NEO4J_URI"],
                                  auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]))
    DB = os.environ.get("NEO4J_DATABASE")
    N = 1000
    bench = [{"k": f"b{i}", "n": f"Bench {i}"} for i in range(N)]
    with driver.session(database=DB) as s:
        s.run("CREATE CONSTRAINT bench_key IF NOT EXISTS FOR (e:BenchEntity) REQUIRE e.key IS UNIQUE").consume()

        t = time.time()
        for r in bench[:200]:                                    # naive: one query per node
            s.run("MERGE (e:BenchEntity {key:$k}) SET e.name=$n", k=r["k"], n=r["n"]).consume()
        t_naive = (time.time() - t) / 200 * N                    # extrapolate to N

        s.run("MATCH (e:BenchEntity) DETACH DELETE e").consume()
        t = time.time()
        for i in range(0, N, 500):                               # batched UNWIND
            s.run("UNWIND $rows AS r MERGE (e:BenchEntity {key:r.k}) SET e.name=r.n", rows=bench[i:i + 500]).consume()
        t_batch = time.time() - t
        s.run("MATCH (e:BenchEntity) DETACH DELETE e").consume()   # clean up
    print(f"naive (extrapolated): {t_naive:.1f}s   batched: {t_batch:.2f}s   -> {t_naive / t_batch:.0f}x")
else:
    print("RUN_NEO4J is False - skipped (set it to True to benchmark your Neo4j).")
''')

md('''
## 7. Graph shape: supernodes and community summaries
Real graphs have **hubs**: a few nodes (e.g. "United States") connect to a huge fraction of the graph. A 2-hop expansion from a hub returns a large part of the database. Mitigations: cap degree per hop, filter by relation type, rank by relevance, and **summarize communities** (the Microsoft GraphRAG idea) so broad questions read a few summaries instead of millions of edges.
''')

code('''
G = nx.Graph()
for s_, r, o in all_triples:
    G.add_edge(norm(s_), norm(o), rel=r)

deg = sorted(G.degree, key=lambda x: -x[1])
print("nodes:", G.number_of_nodes(), "edges:", G.number_of_edges())
print("top hubs (degree):", deg[:5])

hub = deg[0][0]
def reach(g, start, hops, cap=None):
    seen, frontier = {start}, {start}
    for _ in range(hops):
        nxt = set()
        for n in frontier:
            nbrs = list(g.neighbors(n))
            if cap:
                nbrs = nbrs[:cap]            # degree cap per node per hop
            nxt.update(nbrs)
        frontier = nxt - seen
        seen |= frontier
    return len(seen)

for h in (1, 2, 3):
    print(f"hops={h}: uncapped reach={reach(G, hub, h):>4}   capped(cap=5)={reach(G, hub, h, cap=5):>3}")

comms = nx.community.louvain_communities(G, seed=7)
print(f"\\n{len(comms)} communities; sizes of the biggest: {sorted(map(len, comms), reverse=True)[:5]}")
print("-> summarize each community once (offline, with an LLM), embed the summary, retrieve summaries first.")
''')

md('''
## 8. Query-time optimization
- **Entity linking**: a **fulltext or vector index** (Neo4j fulltext, Azure AI Search, Databricks Vector Search), never `CONTAINS` scans over all nodes.
- **Bound the traversal**: `LIMIT`, max hops 2-3, filter relationship types, rank facts before sending them to the LLM (context window = cost + latency).
- **Cache** repeated questions and their retrieved subgraphs (Redis).
- **Two-level retrieval**: community summaries for global questions, entity neighborhoods for local ones.
- **Read replicas / Aura scaling** for query load, separate from the batch ingestion path.
- **Cheaper models** for extraction, stronger models only for the final answer.
''')

md('''
## 9. Back-of-envelope capacity planning
Interviewers like numbers. State your assumptions and compute.
''')

code('''
docs_total      = 1_000_000
tokens_in_doc   = 1_000        # per chunk (assumption)
tokens_out_doc  = 200
workers         = 50           # parallel workers
calls_per_sec   = 50           # provider limit you negotiated / batch tier (assumption)
price_in_per_m  = 0.25         # USD per 1M input tokens  (placeholder - use your model's real price)
price_out_per_m = 1.25         # USD per 1M output tokens (placeholder)

hours = docs_total / calls_per_sec / 3600
cost = docs_total * tokens_in_doc / 1e6 * price_in_per_m + docs_total * tokens_out_doc / 1e6 * price_out_per_m
triples = docs_total * 5       # ~5 triples per doc (assumption)
print(f"extraction time  : ~{hours:.1f} hours at {calls_per_sec} calls/s")
print(f"extraction cost  : ~${cost:,.0f}  (placeholder prices)")
print(f"graph size       : ~{triples:,} triples before dedup; entity resolution typically cuts nodes 3-10x")
print(f"write time       : at 20k rows/s batched ~ {triples / 20_000 / 60:.0f} min (vs hours/days row-by-row)")
''')

md('''
## 10. Run the pipeline on the `scale_review` data
`make_scale_reviews.py` generated 2,000 hotel reviews in `scale_review/` (same shape as `reviews/`: title, "Hotel, City, Country", date, body), with messy hotel names ("The Coral Tower Hotel", "coral tower hotel", "Coral Tower Hotel & Suites") and Dubai as a deliberate hub city. `truth.csv` holds the correct triples, so we can **measure** extraction quality, which is exactly what question 9 in the interview list asks for.

Here a rule-based extractor stands in for the LLM (the reviews follow templates). In real use, swap it for your `chat()` call; the parallelism, evaluation and graph-building code stays the same.

Regenerate with a different size: `python make_scale_reviews.py 20000`.
''')

code('''
import csv
from pathlib import Path

REVIEW_DIR = Path("scale_review")
files = sorted(REVIEW_DIR.glob("*.txt"))
print(len(files), "review files in", REVIEW_DIR)

def split_list(s):
    return [x.strip() for x in re.split(r",\\s*|\\s+and\\s+", s) if x.strip()]

def norm_hotel(n):
    n = re.sub(r"\\s*&\\s*suites", "", n.lower())
    return re.sub(r"^the\\s+", "", n).strip()

def extract_review(path):
    time.sleep(0.01)                                    # simulated LLM latency
    lines = path.read_text(encoding="utf-8").splitlines()
    name, city = [x.strip() for x in lines[1].split(",")][:2]
    body = " ".join(lines[3:])
    hotel = norm_hotel(name)
    out = [(path.stem, hotel, "is_located_in", city.lower())]
    for rel, pat in (("has_facilities", r"The hotel has (.+?)\\."),
                     ("has_customers", r"popular with (.+?)\\."),
                     ("has_reviewer", r"Reviewed by (.+?)\\.")):
        m = re.search(pat, body)
        if m:
            vals = [m.group(1)] if rel == "has_reviewer" else split_list(m.group(1))
            out += [(path.stem, hotel, rel, v.lower()) for v in vals]
    return out

t = time.time()
with ThreadPoolExecutor(max_workers=16) as pool:
    extracted = [row for rows_ in pool.map(extract_review, files) for row in rows_]
print(f"extracted {len(extracted)} triples from {len(files)} files in {time.time() - t:.1f}s")

# --- evaluation against ground truth (only files we actually processed) ---
processed = {p.stem for p in files}
with open(REVIEW_DIR / "truth.csv", encoding="utf-8") as f:
    truth = {(r["file"], r["hotel"].lower(), r["relation"], r["object"].lower())
             for r in csv.DictReader(f) if r["file"] in processed}
pred = set(extracted)
tp = len(pred & truth)
print(f"precision={tp / len(pred):.3f}  recall={tp / len(truth):.3f}  (truth={len(truth)}, predicted={len(pred)})")
''')

code('''
# Build the graph from the extracted data and look at its shape
H = nx.Graph()
for _, h, rel, o in extracted:
    H.add_edge(h, o, rel=rel)

print("distinct hotels:", len({h for _, h, _, _ in extracted}), "(canonical hotels in the generator: 150)")
print("graph:", H.number_of_nodes(), "nodes,", H.number_of_edges(), "edges")
hubs = sorted(H.degree, key=lambda x: -x[1])[:5]
print("top hubs:", hubs)

unique_rows = {(h, rel, o) for _, h, rel, o in extracted}
batches = -(-len(unique_rows) // 5000)
print(f"{len(unique_rows)} unique edges -> {batches} write batch(es) of 5,000 for the batched UNWIND MERGE")
''')

md('''
## Interview summary (say this)
1. **Decouple stages** with a queue: ingest -> chunk -> extract -> resolve -> write -> summarize. Each scales and retries independently.
2. **Extraction**: parallel workers, shared rate limiter, backoff, cheap model, batch API, content-hash checkpoints for idempotency.
3. **Entity resolution**: normalize, block, fuzzy/embedding match with verification; keep aliases reversible.
4. **Writes**: unique constraints first, batched `UNWIND ... MERGE`, partitioned parallel writers (avoid deadlocks), `neo4j-admin import` for the initial bulk load.
5. **Graph shape**: handle supernodes (caps, relation filters), precompute community summaries (Leiden/Louvain).
6. **Queries**: indexes, bounded traversals, ranking, caching, two-level (global/local) retrieval.
7. **Operate it**: metrics (docs/s, 429 rate, queue depth, cost/doc), dead-letter queue for failures, evaluation set to catch quality regressions.
8. **Honest trade-off**: at a few million nodes a single Neo4j instance is fine; at billions, shard or use a distributed graph and pre-aggregate.
''')

nb = {
    "cells": cells,
    "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                 "language_info": {"name": "python"}},
    "nbformat": 4, "nbformat_minor": 5,
}
json.dump(nb, open("graph_rag_at_scale.ipynb", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
print("wrote graph_rag_at_scale.ipynb with", len(cells), "cells")
