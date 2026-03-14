# neo4j_client.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError


@dataclass
class Neo4jConfig:
    uri: str
    user: str
    password: str
    database: str


class Neo4jClient:
    def __init__(self, cfg: Neo4jConfig):
        self.cfg = cfg
        self.driver = GraphDatabase.driver(cfg.uri, auth=(cfg.user, cfg.password))

    def close(self) -> None:
        self.driver.close()

    def query(self, cypher: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        params = params or {}
        try:
            with self.driver.session(database=self.cfg.database) as session:
                res = session.run(cypher, params)
                return [r.data() for r in res]
        except Neo4jError as e:
            raise RuntimeError(f"Neo4j error: {e}") from e
