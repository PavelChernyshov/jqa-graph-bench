You have access to a Neo4j code graph via MCP tools. The graph contains 15.6K types, 124K methods, and their relationships — extracted from **compiled bytecode**.

## Tools

### find_types — batch type discovery

Find types by name pattern, exact names, package, kind, or annotation. Returns fqn, file, kind, module, method count, hierarchy, annotations, caller/dependency counts.

Use this first to discover which types exist for a concept. Examples:
- `find_types(pattern="User")` — all types starting with "User"
- `find_types(names=["UserController", "UserService"])` — exact lookup
- `find_types(annotated_with="org.springframework.web.bind.annotation.RestController")` — REST controllers

### get_type_context — rich type profile

Get full structural context for a single type: methods with signatures/lines, hierarchy (extends/implements/subtypes), top 30 callers with files, top 30 dependencies with files, annotations. All in one call.

Use this after find_types to investigate the most relevant types. Examples:
- `get_type_context(fqn="com.example.controller.UserController")` — full profile
- `get_type_context(fqn="...", include=["callers", "hierarchy"])` — only callers and hierarchy

### query_graph — custom Cypher

For structural questions not covered by the above tools.

Graph model:
- Containment: Artifact -[:CONTAINS]-> Package -[:CONTAINS]-> Type -[:DECLARES]-> Method/Field
- Relationships: INVOKES (has lineNumber), EXTENDS, IMPLEMENTS, ANNOTATED_BY (follow -[:OF_TYPE]-> for annotation Type), READS/WRITES, DEPENDS_ON
- Properties: Type has fqn/name/visibility; Method has name/signature/firstLineNumber/lastLineNumber
- File path expression: `split(a.fileName, '/target/')[0] + '/src/main/java/' + replace(t.fqn, '.', '/') + '.java'`

Example — entry points (REST controllers reaching a type via call chain):
```cypher
MATCH (ctrl:Type)-[:ANNOTATED_BY]->()-[:OF_TYPE]->(anno:Type)
WHERE anno.fqn = 'org.springframework.web.bind.annotation.RestController'
MATCH (a:Artifact)-[:CONTAINS]->(:Package)-[:CONTAINS]->(ctrl)
MATCH (ctrl)-[:DECLARES]->(m:Method)-[:INVOKES*1..5]->(deep:Method)<-[:DECLARES]-(target:Type)
WHERE target.fqn = $fqn
RETURN DISTINCT
  split(a.fileName, '/target/')[0] + '/src/main/java/' + replace(ctrl.fqn, '.', '/') + '.java' AS file,
  ctrl.fqn AS controller, m.name AS endpoint, m.firstLineNumber AS from_line
ORDER BY file, from_line
```

### get_graph_schema — inspect available labels and relationships

Call this if you need to discover what node labels or relationship types exist.
