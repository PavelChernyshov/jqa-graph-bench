You are analyzing ThingsBoard, a large open-source IoT platform (~528K lines Java, 14 Maven modules, Spring Boot).

Given a structural question about the codebase, find ALL Java source files that answer the question. The question asks about code structure — implementations, callers, dependencies — not about runtime behavior.

Use the available tools to explore the codebase. Focus on Java source files only. Ignore:
- Test files (*Test.java, *Tests.java, src/test/)
- UI/frontend files (ui-ngx/)
- Build configuration (pom.xml, *.gradle)
- Configuration files (*.yml, *.xml, *.properties)
- Migration scripts (*.sql)

The codebase is at subjects/thingsboard/repo/ relative to your working directory.

Explore methodically:
1. Find the type(s) mentioned in the question
2. Use the structural relationship asked about (implementations, callers, dependencies)
3. For each result, verify it exists and get its source file path
4. Be thorough — the question asks for ALL matching files, not just a sample
5. Check for inner classes that live in their outer class's source file

When you have found all matching files, output your final answer in this exact format:

RELEVANT_FILES_START
path/to/File1.java | reason for inclusion
path/to/File2.java | reason for inclusion
RELEVANT_FILES_END

The paths should be relative to the repository root (e.g., application/src/main/java/org/thingsboard/...).
Include ONLY files that directly answer the structural question asked.
