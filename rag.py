"""
RAG product-insights assistant.

1. RETRIEVE  - PostgreSQL full-text search (Product.search_tsv / News.search_tsv, GIN-indexed)
               picks the relevant products; SQL aggregates build a context document per product.
2. GENERATE  - the LLM answers ONLY from those documents and must return JSON with citations.
3. GROUND    - check_grounding() rejects the answer unless:
                 a) it is valid JSON in the expected shape,
                 b) it cites at least one document, and every citation was actually retrieved,
                 c) every price / percentage / number it states appears in the cited documents
                    (or in the user's question).
               Rejected answers are never shown; the user gets a safe fallback instead.
Every question is logged to Rag_Queries with the grounding verdict.
"""
import json
import os
import re

import pandas as pd

import db_manager

MODEL = os.getenv("LLM_MODEL", "claude-haiku-4-5-20251001")
TOP_K = 4
FALLBACK = "I couldn't find enough supporting data in DealRadar's price history to answer that reliably."

SYSTEM_PROMPT = """You are DealRadar's product-insights assistant.
Answer the user's question using ONLY the context documents provided. Each document starts with an ID like [P12] or [N7].
Rules:
- Only state prices, percentages, dates and counts that appear verbatim in the context. Do NOT compute new numbers.
- If the context does not contain the answer, set "insufficient_context" to true.
- Cite the IDs of every document you used.
Respond with ONLY a JSON object, no markdown:
{"answer": "<2-4 sentences>", "citations": ["P12", "N7"], "insufficient_context": false}"""


# ---------------------------------------------------------------------------
# 1. Retrieval
# ---------------------------------------------------------------------------

def _terms(text):
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 1][:20]


def retrieve_products(question, uid=None, k=TOP_K):
    terms = _terms(question)
    scope = "AND p.pid IN (SELECT pid FROM Cart WHERE uid = %s)" if uid else ""
    params = []
    hits = None
    if terms:
        params = [" | ".join(terms)] + ([uid] if uid else []) + [k]
        hits = db_manager.run_query(f"""
            SELECT p.pid, p.pname, p.p_category, p.msrp, ts_rank(p.search_tsv, q) AS rank
            FROM Product p, to_tsquery('english', %s) q
            WHERE p.search_tsv @@ q {scope}
            ORDER BY rank DESC LIMIT %s""", tuple(params))
    if (hits is None or hits.empty) and uid:
        # Generic questions ("what dropped the most?") -> fall back to the user's own watchlist
        hits = db_manager.run_query("""
            SELECT p.pid, p.pname, p.p_category, p.msrp, 0.0 AS rank
            FROM Cart c JOIN Product p ON p.pid = c.pid WHERE c.uid = %s
            ORDER BY p.pid LIMIT 8""", (uid,))
    return hits if hits is not None else pd.DataFrame()


def _money(x):
    return f"${x:,.2f}"


def build_product_doc(product, uid=None):
    pid = int(product["pid"])
    stats = db_manager.run_query("""
        SELECT s.sname, COUNT(*) AS n, MIN(sp.price) AS low, MAX(sp.price) AS high,
               ROUND(AVG(sp.price), 2) AS avg,
               (ARRAY_AGG(sp.price ORDER BY sp.price_dt DESC, sp.spid DESC))[1] AS latest,
               (ARRAY_AGG(sp.price ORDER BY sp.price_dt ASC, sp.spid ASC))[1] AS first,
               MIN(sp.price_dt)::date AS first_seen, MAX(sp.price_dt)::date AS last_seen
        FROM Seller_Prices sp JOIN Sellers s ON s.sid = sp.sid
        WHERE sp.pid = %s GROUP BY s.sname ORDER BY latest""", (pid,))
    recent = db_manager.run_query("""
        SELECT sp.price_dt::date AS d, s.sname, sp.price FROM Seller_Prices sp JOIN Sellers s ON s.sid = sp.sid
        WHERE sp.pid = %s ORDER BY sp.price_dt DESC LIMIT 5""", (pid,))

    msrp = float(product["msrp"] or 0)
    lines = [f"[P{pid}] {product['pname']} (category: {product['p_category']}"
             + (f"; MSRP: {_money(msrp)}" if msrp > 0 else "") + ")"]
    if stats.empty:
        lines.append("- No price observations recorded yet.")
    for r in stats.itertuples():
        change = r.latest - r.first
        pct = (change / r.first * 100) if r.first else 0
        lines.append(
            f"- {r.sname}: latest {_money(r.latest)} (as of {r.last_seen}); lowest {_money(r.low)}; "
            f"highest {_money(r.high)}; average {_money(r.avg)}; {r.n} observations since {r.first_seen}; "
            f"change since first observation: {pct:+.1f}% ({'+' if change >= 0 else '-'}{_money(abs(change))})")
    if len(stats) > 1:
        cheap, dear = stats.iloc[0], stats.iloc[-1]
        lines.append(f"- Cheapest seller right now: {cheap.sname} at {_money(cheap.latest)}, "
                     f"{_money(dear.latest - cheap.latest)} below {dear.sname}")
    if uid:
        tgt = db_manager.run_query("SELECT cutoff FROM Cart WHERE uid=%s AND pid=%s", (uid, pid))
        if not tgt.empty and tgt.iloc[0]["cutoff"] and not stats.empty:
            cutoff, best = float(tgt.iloc[0]["cutoff"]), float(stats.iloc[0].latest)
            gap = best - cutoff
            lines.append(f"- Your target price: {_money(cutoff)}; best current price is "
                         + (f"{_money(gap)} above target" if gap > 0 else f"at or below target by {_money(-gap)}"))
    if not recent.empty:
        lines.append("- Recent observations: " + "; ".join(f"{r.d} {r.sname} {_money(r.price)}" for r in recent.itertuples()))
    return {"id": f"P{pid}", "text": "\n".join(lines)}


def retrieve_news(products, limit=3):
    terms = sorted({t for name in products["pname"] for t in _terms(str(name))[:4]})
    if not terms:
        return []
    news = db_manager.run_query("""
        SELECT nid, category, title, published_at::date AS d, ts_rank(search_tsv, q) AS rank
        FROM News, to_tsquery('english', %s) q WHERE search_tsv @@ q
        ORDER BY rank DESC, published_at DESC LIMIT %s""", (" | ".join(terms), limit))
    return [{"id": f"N{r.nid}", "text": f"[N{r.nid}] ({r.category}, {r.d}) {r.title}"} for r in news.itertuples()]


def retrieve(question, uid=None):
    products = retrieve_products(question, uid)
    if products.empty:
        return []
    docs = [build_product_doc(p, uid) for _, p in products.iterrows()]
    return docs + retrieve_news(products)


# ---------------------------------------------------------------------------
# 2. Generation
# ---------------------------------------------------------------------------

def call_claude(system, user):
    from anthropic import Anthropic  # needs ANTHROPIC_API_KEY in the environment
    resp = Anthropic().messages.create(model=MODEL, max_tokens=600, system=system,
                                       messages=[{"role": "user", "content": user}])
    return "".join(b.text for b in resp.content if b.type == "text")


def parse_llm_json(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 3. Grounding checks
# ---------------------------------------------------------------------------

_NUM_RE = re.compile(r"(?<![A-Za-z])(\$?)(\d[\d,]*(?:\.\d+)?)(%?)")


def extract_numbers(text):
    """[(value, is_strict)] - strict means money, percentage or decimal; bare small ints are lenient."""
    out = []
    for dollar, num, pct in _NUM_RE.findall(text or ""):
        try:
            v = float(num.replace(",", ""))
        except ValueError:
            continue
        strict = bool(dollar or pct or "." in num or v > 10)
        out.append((v, strict))
    return out


def _supported(value, allowed):
    for a in allowed:
        if abs(value - a) <= 0.011:            # exact to the cent
            return True
        if value == round(value) and round(a) == value:   # whole-number rounding of a context value
            return True
    return False


def check_grounding(parsed, docs, question=""):
    """Return (ok, reason). ok=False means the answer must not be shown."""
    if not isinstance(parsed, dict) or not isinstance(parsed.get("answer"), str):
        return False, "malformed_llm_output"
    if parsed.get("insufficient_context"):
        return False, "model_reported_insufficient_context"
    citations = parsed.get("citations") or []
    if not isinstance(citations, list) or not citations:
        return False, "no_citations"
    by_id = {d["id"]: d for d in docs}
    unknown = [c for c in citations if c not in by_id]
    if unknown:
        return False, f"cited_unretrieved_docs:{','.join(map(str, unknown))}"
    allowed = [v for c in citations for v, _ in extract_numbers(by_id[c]["text"])]
    allowed += [v for v, _ in extract_numbers(question)]
    unsupported = [v for v, strict in extract_numbers(parsed["answer"]) if strict and not _supported(v, allowed)]
    if unsupported:
        return False, "unsupported_numbers:" + ",".join(f"{v:g}" for v in unsupported)
    return True, None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def answer_question(question, uid=None, llm=call_claude, docs=None, log=True):
    docs = retrieve(question, uid) if docs is None else docs
    result = {"question": question, "answer": FALLBACK, "grounded": False,
              "citations": [], "sources": docs, "reason": None}
    if not docs:
        result["reason"] = "no_relevant_data_retrieved"   # never call the LLM with empty context
    else:
        context = "\n\n".join(d["text"] for d in docs)
        try:
            raw = llm(SYSTEM_PROMPT, f"<context>\n{context}\n</context>\n\nQuestion: {question}")
            parsed = parse_llm_json(raw)
            ok, reason = check_grounding(parsed, docs, question)
        except Exception as e:  # API/network errors -> safe fallback, not a crash
            ok, reason = False, f"llm_error:{type(e).__name__}"
        result["reason"] = reason
        if ok:
            result.update(answer=parsed["answer"], grounded=True, citations=parsed["citations"])
    if log:
        db_manager.log_rag_query(question, result["answer"], result["citations"], result["grounded"],
                                 result["reason"], MODEL)
    return result
