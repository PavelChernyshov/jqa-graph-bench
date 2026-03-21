#!/usr/bin/env python3
"""MCP server for querying the code constraint graph in Neo4j.

Fixes over the generic mcp-neo4j-cypher server:
- No buggy write-query regex — uses Neo4j RoutingControl.READ instead
- Server-side path derivation — adds _source_file to results automatically
- Framework type filtering — strips java.*, springframework.*, etc. by default
- Semantic tool names — query_graph, get_graph_schema
"""

import asyncio
import datetime
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from fastmcp import FastMCP
from neo4j import AsyncGraphDatabase, Query, RoutingControl
from neo4j.exceptions import ClientError, Neo4jError

logger = logging.getLogger("code-graph-mcp")
logging.basicConfig(level=logging.INFO)

# Performance logging — writes JSONL to logs/calls.jsonl next to this file
_LOG_DIR = Path(__file__).parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)
_perf_log_path = _LOG_DIR / "calls.jsonl"


def _log_call(tool: str, params: dict, duration_ms: float, response_chars: int, queries: int | None = None):
    """Append one JSONL line per tool call."""
    entry = {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "tool": tool,
        "params": params,
        "duration_ms": round(duration_ms, 1),
        "response_chars": response_chars,
    }
    if queries is not None:
        entry["queries"] = queries
    with open(_perf_log_path, "a") as f:
        f.write(json.dumps(entry) + "\n")

# Framework prefixes to filter out by default
FRAMEWORK_PREFIXES = (
    "java.",
    "javax.",
    "jakarta.",
    "org.springframework.",
    "org.slf4j.",
    "org.apache.logging.",
    "ch.qos.logback.",
    "lombok.",
    "com.google.common.",
    "com.google.protobuf.",
    "io.netty.",
)


def derive_source_path(artifact_filename: str, fqn: str) -> str:
    """Derive a Java source file path from an artifact JAR path and a fully qualified name.

    Example:
        artifact: .../application/target/myapp-1.0-SNAPSHOT.jar
        fqn: com.example.MyApplication
        -> .../application/src/main/java/com/example/MyApplication.java
    """
    # Split on /target/ to get the module root
    parts = artifact_filename.split("/target/")
    if len(parts) < 2:
        return None
    module_root = parts[0]
    # Convert FQN to path
    rel_path = fqn.replace(".", "/")
    return f"{module_root}/src/main/java/{rel_path}.java"


def _has_framework_fqn(record: dict) -> bool:
    """Check if a result record contains a framework type FQN."""
    for key, value in record.items():
        if isinstance(value, str) and (key == "fqn" or key.endswith(".fqn") or key == "type"):
            if value.startswith(FRAMEWORK_PREFIXES):
                return True
    return False


def _enrich_with_source_paths(record: dict) -> dict:
    """Add _source_file fields to a result record by deriving paths from fqn + artifact data.

    Handles two patterns:
    1. Record has 'file' field with the split/replace expression already computed -> skip
    2. Record has 'fqn'/'type' field and we can find the artifact -> derive path
    """
    # If the query already computed a 'file' field, don't touch it
    if "file" in record or "_source_file" in record:
        return record

    # Look for fqn-like fields and artifact fileName fields
    # The graph queries typically alias these, so we check common patterns
    fqn_value = record.get("fqn") or record.get("type")
    artifact_file = record.get("artifact_file") or record.get("artifactFileName")

    if fqn_value and artifact_file:
        path = derive_source_path(artifact_file, fqn_value)
        if path:
            record["_source_file"] = path

    return record


def _sanitize_value(d: Any, list_limit: int = 128) -> Any:
    """Remove embedding-like values and oversized lists from results."""
    if isinstance(d, dict):
        new_dict = {}
        for key, value in d.items():
            if isinstance(value, dict):
                sanitized = _sanitize_value(value)
                if sanitized is not None:
                    new_dict[key] = sanitized
            elif isinstance(value, list):
                if len(value) < list_limit:
                    sanitized = _sanitize_value(value)
                    if sanitized is not None:
                        new_dict[key] = sanitized
            else:
                new_dict[key] = value
        return new_dict
    elif isinstance(d, list):
        if len(d) < list_limit:
            return [_sanitize_value(item) for item in d if _sanitize_value(item) is not None]
        return None
    return d


_REPO_PREFIX_RE = re.compile(r"^.*/subjects/thingsboard/repo/")


def _simplify_fqn(fqn: str) -> str:
    """Extract simple name from a fully qualified name."""
    return fqn.rsplit(".", 1)[-1] if fqn else fqn


def _simplify_signature(sig: str) -> tuple[str, str]:
    """Strip package prefixes from a method signature, extract return type.

    "com.example.data.User saveUser(com.example.data.User,boolean)"
    → ("saveUser(User, boolean)", "User")
    """
    if not sig:
        return (sig or "", "")

    # Split into "return_and_name(params)" — the first '(' separates them
    paren_idx = sig.find("(")
    if paren_idx == -1:
        # No parens — just simplify the whole thing
        return (_simplify_fqn(sig), "")

    prefix = sig[:paren_idx]  # e.g. "com...User saveUser"
    params_str = sig[paren_idx + 1:].rstrip(")")  # e.g. "com...User,boolean"

    # prefix is "ReturnType methodName" — split on last space
    space_idx = prefix.rfind(" ")
    if space_idx != -1:
        return_fqn = prefix[:space_idx].strip()
        method_name = prefix[space_idx + 1:]
        return_simple = _simplify_fqn(return_fqn)
    else:
        # No return type (constructors)
        method_name = _simplify_fqn(prefix)
        return_simple = ""

    # Simplify each param
    if params_str.strip():
        params = [_simplify_fqn(p.strip()) for p in params_str.split(",")]
        params_simplified = ", ".join(params)
    else:
        params_simplified = ""

    simplified = f"{method_name}({params_simplified})"
    return (simplified, return_simple)


def _shorten_path(path: str) -> str:
    """Make file paths repo-relative by stripping the configured repo prefix."""
    if not path:
        return path or ""
    return _REPO_PREFIX_RE.sub("", path)


def _shorten_module(module: str) -> str:
    """Shorten module path to just the module directory name."""
    if not module:
        return module or ""
    # Module is like ".../repo/common/data"
    shortened = _REPO_PREFIX_RE.sub("", module)
    return shortened if shortened != module else module.rsplit("/", 1)[-1]


def _dedup_hierarchy(rows: list[dict]) -> dict:
    """Merge duplicate hierarchy rows from multi-artifact scanning.

    Union the extends, implements, subtypes lists, dedup by fqn.
    Filter out java.lang.Object from extends.
    """
    extends = set()
    implements = set()
    subtypes = {}  # fqn -> {fqn, name, file}

    for row in rows:
        for e in row.get("extends", []):
            if e and e != "java.lang.Object":
                extends.add(e)
        for i in row.get("implements", []):
            if i:
                implements.add(i)
        for s in row.get("subtypes", []):
            sfqn = s.get("fqn")
            if sfqn and sfqn not in subtypes:
                subtypes[sfqn] = s

    return {
        "extends": sorted(extends),
        "implements": sorted(implements),
        "subtypes": sorted(subtypes.values(), key=lambda s: s.get("name", "")),
    }


def _format_type_context(identity: dict, sections: dict) -> str:
    """Format get_type_context output as compact text."""
    lines = []

    # Identity block
    kind_list = identity.get("kind", [])
    kind = kind_list[0] if kind_list else "Type"
    name = identity.get("name", "")
    file_path = _shorten_path(identity.get("file", ""))
    module = _shorten_module(identity.get("module", ""))

    lines.append(f"{name}  {kind}")
    lines.append(file_path)
    lines.append(f"module: {module}")

    # Hierarchy
    if "hierarchy" in sections:
        h = sections["hierarchy"]
        ext = ", ".join(_simplify_fqn(e) for e in h.get("extends", []))
        impl = ", ".join(_simplify_fqn(i) for i in h.get("implements", []))
        lines.append("")
        lines.append(f"extends: {ext or '—'}")
        lines.append(f"implements: {impl or '—'}")
        subtypes = h.get("subtypes", [])
        if subtypes:
            lines.append("subtypes:")
            for s in subtypes:
                sfile = _shorten_path(s.get("file", ""))
                lines.append(f"  {s.get('name', '')}  {sfile}")
        else:
            lines.append("subtypes: —")

    # Annotations
    if "annotations" in sections:
        annos = sections["annotations"]
        anno_names = ", ".join(a.get("name", "") for a in annos if a.get("name"))
        lines.append("")
        lines.append(f"annotations: {anno_names or '—'}")

    # Methods
    if "methods" in sections:
        methods = sections["methods"]
        lines.append("")
        lines.append(f"methods ({len(methods)}):")
        for m in methods:
            sig = m.get("signature", "")
            simplified, ret = _simplify_signature(sig)
            if not simplified:
                simplified = m.get("name", "")
            ret_str = f" → {ret}" if ret else ""
            vis = m.get("visibility", "")
            vis_str = f"  {vis}" if vis else ""

            from_line = m.get("from_line")
            to_line = m.get("to_line")
            line_str = f"  L{from_line}-{to_line}" if from_line and to_line else ""

            lines.append(f"  {simplified}{ret_str}{vis_str}{line_str}")

    # Callers
    if "callers" in sections:
        callers = sections["callers"]
        class_count = len(callers)
        total_calls = sum(len(c.get("call_details", [])) for c in callers)
        lines.append("")
        lines.append(f"callers ({class_count} classes, {total_calls} call sites):")
        for c in callers:
            cfile = _shorten_path(c.get("file", ""))
            lines.append(f"  {c.get('name', '')}  {cfile}")
            # Group call_details by caller method (format: "method → target:line")
            by_method: dict[str, list[str]] = {}
            for detail in c.get("call_details", []):
                # detail is like "methodName → targetMethod:123"
                parts = detail.split(" → ", 1)
                if len(parts) == 2:
                    caller_method = parts[0]
                    target_call = parts[1]
                    by_method.setdefault(caller_method, []).append(target_call)
                else:
                    by_method.setdefault("?", []).append(detail)
            for via, targets in by_method.items():
                lines.append(f"    {via} → {', '.join(targets)}")

    # Dependencies
    if "dependencies" in sections:
        deps = sections["dependencies"]
        lines.append("")
        lines.append(f"dependencies ({len(deps)} types):")
        for d in deps:
            dfile = _shorten_path(d.get("file", ""))
            methods_called = d.get("methods_called", [])
            call_count = d.get("call_count", 0)
            lines.append(f"  {d.get('name', '')}  {dfile}")
            lines.append(f"    → {', '.join(methods_called)}  ({call_count} calls)")

    return "\n".join(lines)


def _format_find_types(results: list[dict]) -> str:
    """Format find_types output as compact text."""
    if not results:
        return "found 0 types"

    lines = [f"found {len(results)} types:"]
    for r in results:
        name = r.get("name", "")
        kind = r.get("kind", "Type")
        module = _shorten_module(r.get("module", ""))
        method_count = r.get("method_count", 0)
        caller_count = r.get("caller_count", 0)
        dep_count = r.get("dependency_count", 0)

        extends = r.get("extends", [])
        ext_str = ", ".join(_simplify_fqn(e) for e in extends if e and e != "java.lang.Object")
        annos = r.get("annotations", [])
        anno_str = ", ".join(a for a in annos if a)

        parts = [f"{name}  {kind}  {module}  {method_count} methods, {caller_count} callers, {dep_count} deps"]
        if ext_str:
            parts.append(f"extends: {ext_str}")
        if anno_str:
            parts.append(f"@{anno_str}")
        lines.append("  " + "  ".join(parts))

    return "\n".join(lines)


def create_server(
    neo4j_driver,
    database: str = "neo4j",
    read_timeout: int = 30,
) -> FastMCP:
    mcp = FastMCP("code-graph")

    @mcp.tool(name="get_graph_schema")
    async def get_graph_schema() -> str:
        """Return the code graph schema: node labels, relationship types, and property keys.

        Call this first if you are unsure what labels or relationships exist.
        The result is a JSON object keyed by label/relationship name, with counts,
        property types, and connectivity info.
        """
        t0 = time.perf_counter()
        query = "CALL apoc.meta.schema({sample: 1000}) YIELD value RETURN value"
        try:
            results = await neo4j_driver.execute_query(
                query,
                routing_control=RoutingControl.READ,
                database_=database,
                result_transformer_=lambda r: r.data(),
            )
            schema = results[0].get("value", {})

            # Clean schema: keep only types, counts, properties, relationships
            cleaned = {}
            for key, entry in schema.items():
                new_entry = {"type": entry.get("type")}
                if "count" in entry:
                    new_entry["count"] = entry["count"]
                labels = entry.get("labels", [])
                if labels:
                    new_entry["labels"] = labels

                props = entry.get("properties", {})
                clean_props = {}
                for pname, pinfo in props.items():
                    cp = {}
                    if "indexed" in pinfo:
                        cp["indexed"] = pinfo["indexed"]
                    if "type" in pinfo:
                        cp["type"] = pinfo["type"]
                    if cp:
                        clean_props[pname] = cp
                if clean_props:
                    new_entry["properties"] = clean_props

                rels = entry.get("relationships", {})
                if rels:
                    rels_out = {}
                    for rel_name, rel in rels.items():
                        cr = {}
                        if "direction" in rel:
                            cr["direction"] = rel["direction"]
                        rlabels = rel.get("labels", [])
                        if rlabels:
                            cr["labels"] = rlabels
                        rprops = rel.get("properties", {})
                        clean_rprops = {}
                        for rpname, rpinfo in rprops.items():
                            crp = {}
                            if "indexed" in rpinfo:
                                crp["indexed"] = rpinfo["indexed"]
                            if "type" in rpinfo:
                                crp["type"] = rpinfo["type"]
                            if crp:
                                clean_rprops[rpname] = crp
                        if clean_rprops:
                            cr["properties"] = clean_rprops
                        if cr:
                            rels_out[rel_name] = cr
                    if rels_out:
                        new_entry["relationships"] = rels_out

                cleaned[key] = new_entry

            result = json.dumps(cleaned, default=str)
            _log_call("get_graph_schema", {}, (time.perf_counter() - t0) * 1000, len(result), queries=1)
            return result

        except ClientError as e:
            if "ProcedureNotFound" in str(e):
                raise RuntimeError(
                    "APOC plugin not installed. Install APOC to use get_graph_schema."
                )
            raise RuntimeError(f"Neo4j error: {e}")
        except Neo4jError as e:
            raise RuntimeError(f"Neo4j error: {e}")

    @mcp.tool(name="query_graph")
    async def query_graph(
        query: str,
        params: Optional[dict[str, Any]] = None,
        filter_external: bool = True,
    ) -> str:
        """Execute a read-only Cypher query against the code graph.

        ## Graph model

        Containment hierarchy:
            Artifact (Maven module JAR, has fileName property)
              -[:CONTAINS]-> Package
                -[:CONTAINS]-> Type (Class, Interface, Enum, Annotation)
                  -[:DECLARES]-> Method
                  -[:DECLARES]-> Field

        Key relationships:
            INVOKES      — method calls method (has lineNumber property)
            EXTENDS      — type extends type
            IMPLEMENTS   — type implements interface
            ANNOTATED_BY — type/method has annotation (follow -[:OF_TYPE]-> to get annotation Type)
            READS/WRITES — method accesses field
            DEPENDS_ON   — artifact depends on artifact

        Key properties:
            Type:    fqn (fully qualified name), name (simple name), visibility
            Method:  name, signature, firstLineNumber, lastLineNumber
            INVOKES: lineNumber (call site in caller's source)

        ## Path derivation

        To get source file paths, include the Artifact node and use:
            split(a.fileName, '/target/')[0] + '/src/main/java/' + replace(t.fqn, '.', '/') + '.java' AS file

        ## Filtering

        Framework types (java.*, javax.*, org.springframework.*, org.slf4j.*, lombok.*,
        com.google.common.*, io.netty.*, etc.) are filtered from results by default.
        Set filter_external=false to include them.

        Args:
            query: Cypher query string. All queries run as read-only transactions.
            params: Optional query parameters (e.g. {"fqn": "com.example.MyClass"}).
            filter_external: If true (default), remove results where the type FQN
                matches a known framework prefix.
        """
        if params is None:
            params = {}

        t0 = time.perf_counter()
        try:
            query_obj = Query(query, timeout=float(read_timeout))
            results = await neo4j_driver.execute_query(
                query_obj,
                parameters_=params,
                routing_control=RoutingControl.READ,
                database_=database,
                result_transformer_=lambda r: r.data(),
            )

            # Sanitize (remove oversized lists, embedding-like values)
            sanitized = [_sanitize_value(record) for record in results]

            # Filter framework types
            if filter_external:
                sanitized = [r for r in sanitized if not _has_framework_fqn(r)]

            # Enrich with source paths
            enriched = [_enrich_with_source_paths(r) for r in sanitized]

            result = json.dumps(enriched, default=str)
            _log_call("query_graph", params, (time.perf_counter() - t0) * 1000, len(result), queries=1)
            return result

        except Neo4jError as e:
            raise RuntimeError(f"Neo4j error: {e}\nQuery: {query}\nParams: {params}")
        except Exception as e:
            raise RuntimeError(f"Error: {e}\nQuery: {query}\nParams: {params}")

    async def _run_query(cypher: str, params: dict | None = None) -> list[dict]:
        """Execute a read-only Cypher query and return raw records."""
        if params is None:
            params = {}
        query_obj = Query(cypher, timeout=float(read_timeout))
        return await neo4j_driver.execute_query(
            query_obj,
            parameters_=params,
            routing_control=RoutingControl.READ,
            database_=database,
            result_transformer_=lambda r: r.data(),
        )

    @mcp.tool(name="find_types")
    async def find_types(
        pattern: Optional[str] = None,
        names: Optional[list[str]] = None,
        package_prefix: Optional[str] = None,
        kind: Optional[str] = None,
        annotated_with: Optional[str] = None,
        limit: int = 25,
    ) -> str:
        """Batch type discovery — find types by name pattern, exact names, package, kind, or annotation.

        Use this to bootstrap investigation: find all types matching a concept,
        then call get_type_context on the most relevant ones.

        Args:
            pattern: Name prefix match (e.g. "User" finds UserController, UserService, ...).
            names: Exact simple name list (e.g. ["UserController", "UserService"]).
            package_prefix: Filter by package FQN prefix (e.g. "com.example.controller").
            kind: Filter by type kind: "Class", "Interface", "Enum", or "Annotation".
            annotated_with: Filter to types annotated with this annotation FQN
                (e.g. "org.springframework.web.bind.annotation.RestController").
            limit: Max results (default 25).

        Returns one line per type with: name, kind, module, method/caller/dep counts,
            parent types (extends), and annotations (@). Ordered by caller+dep count descending.
        """
        if not pattern and not names and not package_prefix and not kind and not annotated_with:
            raise ValueError("Provide at least one filter: pattern, names, package_prefix, kind, or annotated_with.")

        t0 = time.perf_counter()
        where_clauses = []
        params = {}

        if pattern:
            where_clauses.append("t.name STARTS WITH $pattern")
            params["pattern"] = pattern
        if names:
            where_clauses.append("t.name IN $names")
            params["names"] = names
        if package_prefix:
            where_clauses.append("pkg.fqn STARTS WITH $packagePrefix")
            params["packagePrefix"] = package_prefix
        if kind:
            where_clauses.append(f"t:{kind}")
        if annotated_with:
            where_clauses.append(
                "EXISTS { MATCH (t)-[:ANNOTATED_BY]->()-[:OF_TYPE]->(annoType:Type) "
                "WHERE annoType.fqn = $annotatedWith }"
            )
            params["annotatedWith"] = annotated_with

        params["resultLimit"] = limit
        where = " AND ".join(where_clauses)

        cypher = f"""
MATCH (a:Artifact)-[:CONTAINS]->(pkg:Package)-[:CONTAINS]->(t:Type)
WHERE {where}
OPTIONAL MATCH (t)-[:EXTENDS|IMPLEMENTS]->(parent:Type)
OPTIONAL MATCH (t)-[:ANNOTATED_BY]->()-[:OF_TYPE]->(anno:Type)
OPTIONAL MATCH (t)-[:DECLARES]->(m:Method)
WITH t, a, pkg,
  [l IN labels(t) WHERE l IN ['Class', 'Interface', 'Enum', 'Annotation']] AS kind,
  collect(DISTINCT parent.fqn) AS extends,
  collect(DISTINCT anno.name) AS annotations,
  count(DISTINCT m) AS method_count
OPTIONAL MATCH (caller:Method)-[:INVOKES]->(cm:Method)<-[:DECLARES]-(t)
WITH t, a, pkg, kind, extends, annotations, method_count,
  count(DISTINCT caller) AS caller_count
OPTIONAL MATCH (t)-[:DECLARES]->(om:Method)-[:INVOKES]->(dm:Method)<-[:DECLARES]-(dep:Type)
WHERE dep <> t
WITH t, a, pkg, kind, extends, annotations, method_count, caller_count,
  count(DISTINCT dep) AS dependency_count
RETURN
  t.fqn AS fqn,
  t.name AS name,
  split(a.fileName, '/target/')[0] + '/src/main/java/' + replace(t.fqn, '.', '/') + '.java' AS file,
  kind[0] AS kind,
  split(a.fileName, '/target/')[0] AS module,
  method_count,
  extends,
  annotations,
  caller_count,
  dependency_count
ORDER BY caller_count + dependency_count DESC
LIMIT $resultLimit
"""

        try:
            results = await _run_query(cypher, params)
            filtered = [r for r in results if not _has_framework_fqn(r)]
            result = _format_find_types(filtered)
            log_params = {k: v for k, v in {"pattern": pattern, "names": names, "package_prefix": package_prefix, "kind": kind}.items() if v}
            _log_call("find_types", log_params, (time.perf_counter() - t0) * 1000, len(result), queries=1)
            return result
        except Neo4jError as e:
            raise RuntimeError(f"Neo4j error: {e}")

    @mcp.tool(name="get_type_context")
    async def get_type_context(
        fqn: str,
        include: Optional[list[str]] = None,
    ) -> str:
        """Rich type profile — returns identity, methods, hierarchy, callers, and dependencies in one call.

        Use after find_types to deeply investigate a specific type.

        Args:
            fqn: Fully qualified type name (e.g. "com.example.controller.UserController").
            include: Optional list of sections to include. Any subset of:
                ["methods", "hierarchy", "callers", "dependencies", "annotations"].
                Defaults to all sections.

        Returns compact text. Identity block: name, kind, file, module. Optional sections:
            methods — simplified signatures with return types, visibility, line ranges;
            hierarchy — extends, implements, subtypes with files;
            callers — grouped by caller class, showing method→target:line call sites (up to 30 classes);
            dependencies — grouped by dep type with methods called (up to 30 types);
            annotations — simple annotation names.
        """
        t0 = time.perf_counter()
        all_sections = {"methods", "hierarchy", "callers", "dependencies", "annotations"}
        sections = set(include) if include else all_sections

        params = {"fqn": fqn}

        # Identity query — always runs
        identity_q = """
MATCH (a:Artifact)-[:CONTAINS]->(:Package)-[:CONTAINS]->(t:Type)
WHERE t.fqn = $fqn
RETURN
  t.fqn AS fqn,
  t.name AS name,
  split(a.fileName, '/target/')[0] + '/src/main/java/' + replace(t.fqn, '.', '/') + '.java' AS file,
  [l IN labels(t) WHERE l IN ['Class', 'Interface', 'Enum', 'Annotation']] AS kind,
  split(a.fileName, '/target/')[0] AS module
LIMIT 1
"""

        methods_q = """
MATCH (t:Type)-[:DECLARES]->(m:Method)
WHERE t.fqn = $fqn AND m.name IS NOT NULL
RETURN m.name AS name, m.signature AS signature,
  m.firstLineNumber AS from_line, m.lastLineNumber AS to_line, m.visibility AS visibility
ORDER BY m.firstLineNumber
"""

        hierarchy_q = """
MATCH (t:Type) WHERE t.fqn = $fqn
OPTIONAL MATCH (t)-[:EXTENDS]->(parent:Type)
OPTIONAL MATCH (t)-[:IMPLEMENTS]->(iface:Type)
OPTIONAL MATCH (sub:Type)-[:EXTENDS|IMPLEMENTS]->(t)
OPTIONAL MATCH (sa:Artifact)-[:CONTAINS]->(:Package)-[:CONTAINS]->(sub)
WITH t,
  collect(DISTINCT parent.fqn) AS extends,
  collect(DISTINCT iface.fqn) AS implements,
  collect(DISTINCT {
    fqn: sub.fqn,
    name: sub.name,
    file: split(sa.fileName, '/target/')[0] + '/src/main/java/' + replace(sub.fqn, '.', '/') + '.java'
  }) AS subtypes
RETURN extends, implements,
  [s IN subtypes WHERE s.fqn IS NOT NULL] AS subtypes
"""

        callers_q = """
MATCH (a:Artifact)-[:CONTAINS]->(:Package)-[:CONTAINS]->(caller:Type)
      -[:DECLARES]->(cm:Method)-[inv:INVOKES]->(callee:Method)<-[:DECLARES]-(t:Type)
WHERE t.fqn = $fqn AND caller <> t AND callee.name IS NOT NULL
WITH caller, a,
  collect(DISTINCT cm.name + ' \u2192 ' + callee.name + ':' + toString(inv.lineNumber)) AS call_details,
  count(DISTINCT inv) AS total_calls
RETURN
  caller.fqn AS fqn,
  caller.name AS name,
  split(a.fileName, '/target/')[0] + '/src/main/java/' + replace(caller.fqn, '.', '/') + '.java' AS file,
  call_details,
  total_calls
ORDER BY total_calls DESC
LIMIT 30
"""

        dependencies_q = """
MATCH (a:Artifact)-[:CONTAINS]->(:Package)-[:CONTAINS]->(dep:Type)-[:DECLARES]->(dm:Method)<-[inv:INVOKES]-(m:Method)<-[:DECLARES]-(t:Type)
WHERE t.fqn = $fqn AND dep <> t AND dm.name IS NOT NULL
WITH DISTINCT dep, a,
  collect(DISTINCT dm.name) AS methods_called,
  count(DISTINCT inv) AS call_count
RETURN
  dep.fqn AS fqn,
  dep.name AS name,
  split(a.fileName, '/target/')[0] + '/src/main/java/' + replace(dep.fqn, '.', '/') + '.java' AS file,
  methods_called,
  call_count
ORDER BY call_count DESC
LIMIT 30
"""

        annotations_q = """
MATCH (t:Type)-[:ANNOTATED_BY]->(a)-[:OF_TYPE]->(annoType:Type)
WHERE t.fqn = $fqn
RETURN DISTINCT annoType.fqn AS fqn, annoType.name AS name
"""

        try:
            # Build list of queries to run in parallel
            tasks = [_run_query(identity_q, params)]
            section_order = []

            if "methods" in sections:
                tasks.append(_run_query(methods_q, params))
                section_order.append("methods")
            if "hierarchy" in sections:
                tasks.append(_run_query(hierarchy_q, params))
                section_order.append("hierarchy")
            if "callers" in sections:
                tasks.append(_run_query(callers_q, params))
                section_order.append("callers")
            if "dependencies" in sections:
                tasks.append(_run_query(dependencies_q, params))
                section_order.append("dependencies")
            if "annotations" in sections:
                tasks.append(_run_query(annotations_q, params))
                section_order.append("annotations")

            results = await asyncio.gather(*tasks)

            # Identity is always first
            identity_rows = results[0]
            if not identity_rows:
                return f"Type not found: {fqn}"

            identity = dict(identity_rows[0])
            sections_data = {}

            # Map section results
            for i, section_name in enumerate(section_order):
                section_results = results[i + 1]
                if section_name == "hierarchy":
                    sections_data["hierarchy"] = _dedup_hierarchy(section_results)
                elif section_name in ("callers", "dependencies"):
                    filtered = [r for r in section_results if not _has_framework_fqn(r)]
                    sections_data[section_name] = filtered
                else:
                    sections_data[section_name] = section_results

            result = _format_type_context(identity, sections_data)
            log_params = {"fqn": fqn}
            if include:
                log_params["include"] = include
            _log_call("get_type_context", log_params, (time.perf_counter() - t0) * 1000, len(result), queries=len(tasks))
            return result

        except Neo4jError as e:
            raise RuntimeError(f"Neo4j error: {e}")

    return mcp


def main():
    """Entry point: read config from env vars and run the MCP server."""
    db_url = os.environ.get("NEO4J_URI", os.environ.get("NEO4J_URL", "bolt://localhost:7687"))
    username = os.environ.get("NEO4J_USERNAME", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "")
    database = os.environ.get("NEO4J_DATABASE", "neo4j")

    neo4j_driver = AsyncGraphDatabase.driver(db_url, auth=(username, password))
    logger.info(f"Connecting to Neo4j at {db_url}")

    mcp = create_server(neo4j_driver, database=database)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
