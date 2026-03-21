#!/usr/bin/env bash
set -euo pipefail

# ── Configuration ──────────────────────────────────────────────────────────────

# Embedded Neo4j memory: 2G heap + 1G page cache (default JVM heap is ~256MB,
# too small for 15K types / 124K methods and parallel queries)
export JQASSISTANT_OPTS="${JQASSISTANT_OPTS:-} -Xms1g -Xmx2g -Dserver.memory.pagecache.size=1g"

REPO_DIR="$(cd "$(dirname "$0")/repo" && pwd)"
GRAPH_DIR="$(cd "$(dirname "$0")/graph" && pwd)"
JQA="${JQA_BIN:-$HOME/.sdkman/candidates/jqassistant/current/bin/jqassistant}"
YML_PATH="$(cd "$(dirname "$0")" && pwd)/.jqassistant.yml"

# JAR classifiers to exclude (Maven classifier = suffix before .jar)
EXCLUDED_CLASSIFIERS=(boot sources tests javadoc)

# Modules to exclude entirely (matched against path components)
EXCLUDED_MODULES=(ui-ngx)

# ── Functions ──────────────────────────────────────────────────────────────────

find_jars() {
    # Build grep -v arguments for excluded classifiers
    local classifier_args=()
    for c in "${EXCLUDED_CLASSIFIERS[@]}"; do
        classifier_args+=(-e "-${c}.jar")
    done

    # Build grep -v arguments for excluded modules
    local module_args=()
    for m in "${EXCLUDED_MODULES[@]}"; do
        module_args+=(-e "/${m}/")
    done

    find "$REPO_DIR" -path "*/target/*.jar" -type f 2>/dev/null \
        | grep -v "${classifier_args[@]}" \
        | grep -v "${module_args[@]}"
}

build_scan_args() {
    local jars
    jars=$(find_jars)
    local args=""
    while IFS= read -r jar; do
        [[ -z "$jar" ]] && continue
        [[ -n "$args" ]] && args+=","
        args+="java:classpath::${jar}"
    done <<< "$jars"
    echo "$args"
}

# ── Commands ───────────────────────────────────────────────────────────────────

cmd_list() {
    echo "=== JARs to scan ==="
    find_jars
    echo ""
    echo "=== Excluded classifiers: ${EXCLUDED_CLASSIFIERS[*]} ==="
    echo "=== Excluded modules: ${EXCLUDED_MODULES[*]} ==="
    echo "=== Total: $(find_jars | wc -l | tr -d ' ') JARs ==="
}

cmd_scan() {
    local scan_args
    scan_args=$(build_scan_args)
    echo "Scanning $(find_jars | wc -l | tr -d ' ') JARs into ${GRAPH_DIR}..."
    echo ""
    "$JQA" scan \
        -C "$YML_PATH" \
        -f "$scan_args"
}

cmd_analyze() {
    echo "Running analysis (classpath resolution, package dependencies)..."
    "$JQA" analyze \
        -C "$YML_PATH"
}

cmd_server() {
    echo "Starting Neo4j browser at http://localhost:7474 (bolt://localhost:7687)..."
    "$JQA" server \
        -C "$YML_PATH"
}

cmd_reset() {
    echo "Resetting store at ${GRAPH_DIR}..."
    "$JQA" reset \
        -C "$YML_PATH"
}

# ── Main ───────────────────────────────────────────────────────────────────────

usage() {
    echo "Usage: $0 <command>"
    echo ""
    echo "Commands:"
    echo "  list      Show JARs that will be scanned"
    echo "  scan      Run jQAssistant scan"
    echo "  analyze   Run analysis (classpath:Resolve, dependencies)"
    echo "  server    Start Neo4j browser"
    echo "  reset     Reset the graph store"
    echo ""
    echo "Typical flow: list → scan → analyze → server"
}

case "${1:-}" in
    list)    cmd_list ;;
    scan)    cmd_scan ;;
    analyze) cmd_analyze ;;
    server)  cmd_server ;;
    reset)   cmd_reset ;;
    *)       usage ;;
esac
