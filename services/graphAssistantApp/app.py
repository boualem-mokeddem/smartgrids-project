import os
import re
import json
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from neo4j import GraphDatabase
from google import genai



# ----------------------------
# Config
# ----------------------------
NEO4J_URI = os.environ.get("NEO4J_URI")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "")
NEO4J_DATABASE = os.environ.get("NEO4J_DATABASE", "neo4j")  # <-- your DB name

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")


client = genai.Client(api_key=GEMINI_API_KEY)



# Basic read-only guardrail (block write clauses)
WRITE_CYPHER_REGEX = re.compile(
    r"\b(CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|CALL\s+dbms|CALL\s+apoc\.|LOAD\s+CSV|IMPORT)\b",
    re.IGNORECASE,
)

# Ensure we don't return massive results
DEFAULT_LIMIT = int(os.environ.get("DEFAULT_LIMIT", "50"))

# ----------------------------
# App + Clients
# ----------------------------
app = FastAPI(title="Neo4j Text-to-Cypher QA")

neo4j_driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

# ----------------------------
# Models
# ----------------------------
class AskRequest(BaseModel):
    question: str = Field(..., min_length=3)
    # Optional: let user ask for "table" output
    mode: str = Field(default="answer", description="answer|raw")
    limit: Optional[int] = Field(default=None, ge=1, le=500)

class AskResponse(BaseModel):
    cypher: str
    params: Dict[str, Any] = Field(default_factory=dict)
    rows: List[Dict[str, Any]]
    answer: Optional[str] = None

# ----------------------------
# Neo4j helpers
# ----------------------------
def get_schema_summary() -> Dict[str, Any]:
    with neo4j_driver.session(database=NEO4J_DATABASE) as session:
        node_props = session.run("""
        CALL db.schema.nodeTypeProperties()
        YIELD nodeType, propertyName
        RETURN nodeType, collect(propertyName) AS properties
        """).data()

        rel_props = session.run("""
        CALL db.schema.relTypeProperties()
        YIELD relType, propertyName
        RETURN relType, collect(propertyName) AS properties
        """).data()

    return {
        "nodes": {r["nodeType"]: r["properties"] for r in node_props},
        "relationships": {r["relType"]: r["properties"] for r in rel_props},
    }

def run_cypher(cypher: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    with neo4j_driver.session(database=NEO4J_DATABASE) as session:
        # EXPLAIN first (cheap validation without executing writes)
        session.run("EXPLAIN " + cypher, params).consume()

        result = session.run(cypher, params)
        rows = [record.data() for record in result]
        return rows

def enforce_read_only(cypher: str) -> None:
    if WRITE_CYPHER_REGEX.search(cypher):
        raise HTTPException(status_code=400, detail="Blocked: query contains write/admin operations.")

def ensure_limit(cypher: str, limit: int) -> str:
    """
    If user/LLM forgot LIMIT, append a LIMIT at the end (simple heuristic).
    """
    if re.search(r"\bLIMIT\b", cypher, re.IGNORECASE):
        return cypher
    return cypher.rstrip().rstrip(";") + f" LIMIT {limit}"

# ----------------------------
# LLM helpers
# ----------------------------
def llm_generate_cypher(question: str, schema: Dict[str, Any], limit: int) -> Dict[str, Any]:
    prompt = f"""
You are a Neo4j Cypher assistant.

Generate a READ-ONLY Cypher query to answer the user's question.

Rules:
- Use ONLY: MATCH, OPTIONAL MATCH, WHERE, WITH, RETURN, ORDER BY, LIMIT
- NEVER use: CREATE, MERGE, SET, DELETE, DETACH, REMOVE, DROP, LOAD CSV, IMPORT, dbms.*, apoc.*
- Prefer compact results
- Target LIMIT {limit}

Output STRICT JSON ONLY, no markdown, no explanation:
{{
  "cypher": "<cypher_query>",
  "params": {{ }}
}}

Schema summary:
{json.dumps(schema, ensure_ascii=False)}

User question:
{question}
"""

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt
    )
    text = response.text.strip()

    # Remove markdown fences if present
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)


    try:
        obj = json.loads(text)
        if "cypher" not in obj:
            raise ValueError("Missing cypher key")
        if "params" not in obj or not isinstance(obj["params"], dict):
            obj["params"] = {}
        return obj
    except Exception:
        raise HTTPException(
            status_code=500,
            detail=f"Gemini did not return valid JSON. Raw output:\n{text[:500]}"
        )

def llm_summarize_answer(question: str, rows: List[Dict[str, Any]]) -> str:
    prompt = f"""
You are an assistant summarizing Neo4j query results.

Question:
{question}

Results (JSON):
{json.dumps(rows, ensure_ascii=False)}

Provide a clear, concise explanation for a human user.
"""

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt
    )

    return response.text.strip()

# ----------------------------
# Endpoint
# ----------------------------

@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest):
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY is not set.")

    limit = req.limit or DEFAULT_LIMIT

    schema = get_schema_summary()
    gen = llm_generate_cypher(req.question, schema, limit=limit)

    cypher = gen["cypher"].strip()
    params = gen.get("params", {}) or {}

    enforce_read_only(cypher)
    cypher = ensure_limit(cypher, limit)

    rows = run_cypher(cypher, params)

    if req.mode == "raw":
        return AskResponse(cypher=cypher, params=params, rows=rows, answer=None)

    answer = llm_summarize_answer(req.question, rows)
    return AskResponse(cypher=cypher, params=params, rows=rows, answer=answer)

@app.get("/health")
def health():
    return {"status": "ok", "neo4j_database": NEO4J_DATABASE}

@app.get("/")
def root():
    return {"status": "ok"}


## Run it : uvicorn app:app --reload --port 8000

## http://127.0.0.1:8000/docs#/default/ask_ask_post

## GDMvenv\Scripts\activate

## deactivate

'''
test it : 

{"question":"step 1 : We have a producer of electricity building 79, with its adjacent buildings 46, 38, 58, 2, step 2: Calculate the Energy balance for 79, 46, 38, 58, 2step 3: For a producer building 79, if its energy balance positive, check if he can share surplus energy with the neighbors with negative energy balance, for month June output : return the energy balance results for the producer building and its adjacent buildings","mode":"answer"}


curl -X POST "http://localhost:8000/ask" \
  -H "Content-Type: application/json" \
  -d '{"question":"Show me the top 10 buildings by total consumption in June","mode":"answer"}'


'''


