#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════
#  MagNeurific / Webknossos — Local Development Startup Script
#  Usage: ./start.sh
#  Stop:  Ctrl+C (gracefully shuts down all services)
# ═══════════════════════════════════════════════════════════════════════
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ─── Colors ───────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log()  { echo -e "${CYAN}[$(date +%H:%M:%S)]${NC} $1"; }
ok()   { echo -e "${CYAN}[$(date +%H:%M:%S)]${NC} ${GREEN}✅ $1${NC}"; }
warn() { echo -e "${CYAN}[$(date +%H:%M:%S)]${NC} ${YELLOW}⚠️  $1${NC}"; }
fail() { echo -e "${CYAN}[$(date +%H:%M:%S)]${NC} ${RED}❌ $1${NC}"; }

# ─── Environment Setup ───────────────────────────────────────────────
log "Setting up environment..."

# NVM (Node Version Manager)
export NVM_DIR="$HOME/.nvm"
if [ -s "$NVM_DIR/nvm.sh" ]; then
    source "$NVM_DIR/nvm.sh"
else
    fail "NVM not found at $NVM_DIR. Install: curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash"
    exit 1
fi

# Homebrew
if [ -f /opt/homebrew/bin/brew ]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
elif [ -f /usr/local/bin/brew ]; then
    eval "$(/usr/local/bin/brew shellenv)"
else
    fail "Homebrew not found. Install: /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
    exit 1
fi

# Java 21
if [ -d /opt/homebrew/opt/openjdk@21 ]; then
    export PATH="/opt/homebrew/opt/openjdk@21/bin:$PATH"
    export JAVA_HOME="/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home"
elif [ -d /usr/local/opt/openjdk@21 ]; then
    export PATH="/usr/local/opt/openjdk@21/bin:$PATH"
    export JAVA_HOME="/usr/local/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home"
else
    fail "Java 21 not found. Install: brew install openjdk@21"
    exit 1
fi

# PostgreSQL 15
if [ -d /opt/homebrew/opt/postgresql@15 ]; then
    export PATH="/opt/homebrew/opt/postgresql@15/bin:$PATH"
elif [ -d /usr/local/opt/postgresql@15 ]; then
    export PATH="/usr/local/opt/postgresql@15/bin:$PATH"
else
    fail "PostgreSQL 15 not found. Install: brew install postgresql@15"
    exit 1
fi

ok "Environment ready  (Node $(node -v), Java $(java -version 2>&1 | head -1 | awk -F'"' '{print $2}'), psql $(psql --version | awk '{print $3}'))"

# ─── PID tracking & cleanup ──────────────────────────────────────────
FOSSIL_PID=""
SBT_PID=""
VITE_PID=""
SKEL_PID=""
INGEST_PID=""
ORCH_PID=""

cleanup() {
    echo ""
    log "${BOLD}Shutting down all services...${NC}"
    [ -n "$ORCH_PID" ] && kill "$ORCH_PID" 2>/dev/null && log "  Stopped Orchestrator Service"
    [ -n "$INGEST_PID" ] && kill "$INGEST_PID" 2>/dev/null && log "  Stopped Ingest Service"
    [ -n "$SKEL_PID" ] && kill "$SKEL_PID" 2>/dev/null && log "  Stopped Skeletonization Service"
    [ -n "$VITE_PID" ]  && kill "$VITE_PID"  2>/dev/null && log "  Stopped Frontend (Vite)"
    [ -n "$SBT_PID" ]   && kill "$SBT_PID"   2>/dev/null && log "  Stopped Backend (sbt)"
    [ -n "$FOSSIL_PID" ] && kill "$FOSSIL_PID" 2>/dev/null && log "  Stopped FossilDB"
    # Kill any remaining child processes
    jobs -p | xargs kill 2>/dev/null
    wait 2>/dev/null
    ok "All services stopped."
    exit 0
}
trap cleanup SIGINT SIGTERM EXIT

# ═══════════════════════════════════════════════════════════════════════
#  STEP 1: PostgreSQL
# ═══════════════════════════════════════════════════════════════════════
log "🐘 ${BOLD}Step 1/6: PostgreSQL${NC}"

if pg_isready -q 2>/dev/null; then
    ok "PostgreSQL already running"
else
    log "  Starting PostgreSQL..."
    brew services start postgresql@15 >/dev/null 2>&1 || true
    sleep 2
    if pg_isready -q 2>/dev/null; then
        ok "PostgreSQL started"
    else
        fail "PostgreSQL failed to start. Run: brew services start postgresql@15"
        exit 1
    fi
fi

# Ensure database and user exist
if ! psql -U postgres -lqt 2>/dev/null | cut -d '|' -f 1 | grep -qw webknossos; then
    log "  Creating database 'webknossos'..."
    createuser -s postgres 2>/dev/null || true
    createdb -U postgres webknossos 2>/dev/null || true
    ok "Database created"
    
    # Initialize schema
    log "  Initializing database schema..."
    cd "$SCRIPT_DIR/tools/postgres" && node dbtool.js refresh-schema 2>&1 | tail -1
    cd "$SCRIPT_DIR"
    ok "Schema initialized"
else
    ok "Database 'webknossos' exists"
fi

# ═══════════════════════════════════════════════════════════════════════
#  STEP 2: Enable Jobs (config flip only — worker registration deferred)
# ═══════════════════════════════════════════════════════════════════════
log "⚙️  ${BOLD}Step 2/6: Enable Jobs (config)${NC}"

# Flip jobsEnabled=false → true (idempotent; no-op if already true).
# Must happen BEFORE backend boots. Worker row gets registered in Step 6
# (after backend has created the `localhost` datastore row it references).
if grep -q "jobsEnabled = false" "$SCRIPT_DIR/conf/application.conf" 2>/dev/null; then
    sed -i '' -e 's/jobsEnabled = false/jobsEnabled = true/g' "$SCRIPT_DIR/conf/application.conf"
    ok "jobsEnabled flipped to true in conf/application.conf"
else
    ok "jobsEnabled already true"
fi

# ═══════════════════════════════════════════════════════════════════════
#  STEP 3: FossilDB
# ═══════════════════════════════════════════════════════════════════════
log "🦴 ${BOLD}Step 3/6: FossilDB${NC}"

if lsof -i:7155 -sTCP:LISTEN >/dev/null 2>&1; then
    ok "FossilDB already running on port 7155"
else
    # Clear stale lock file (common issue after unclean shutdown)
    rm -f "$SCRIPT_DIR/fossildb/data/LOCK"
    
    bash "$SCRIPT_DIR/fossildb/run.sh" > "$SCRIPT_DIR/fossildb/logs" 2>&1 &
    FOSSIL_PID=$!
    
    # Wait for FossilDB to be ready (up to 15 seconds)
    for i in $(seq 1 15); do
        if lsof -i:7155 -sTCP:LISTEN >/dev/null 2>&1; then
            ok "FossilDB running (PID: $FOSSIL_PID, port 7155)"
            break
        fi
        sleep 1
        if [ $i -eq 15 ]; then
            fail "FossilDB failed to start. Check fossildb/logs"
            cat "$SCRIPT_DIR/fossildb/logs" | grep -i "error\|exception" | tail -5
            exit 1
        fi
    done
fi

# ═══════════════════════════════════════════════════════════════════════
#  STEP 4: Frontend Build (if needed)
# ═══════════════════════════════════════════════════════════════════════
log "📦 ${BOLD}Step 4/6: Frontend Build${NC}"

if [ -f "$SCRIPT_DIR/public/assets/index.html" ] || ls "$SCRIPT_DIR/public/assets/"*.js >/dev/null 2>&1; then
    ok "Frontend assets already built"
else
    log "  Building frontend (first time takes ~60s)..."
    yarn build 2>&1 | tail -3
    ok "Frontend built"
fi

# ═══════════════════════════════════════════════════════════════════════
#  STEP 5: Backend (sbt / Play Framework)
# ═══════════════════════════════════════════════════════════════════════
log "☕ ${BOLD}Step 5/6: Backend (sbt)${NC}"

if lsof -i:9000 -sTCP:LISTEN >/dev/null 2>&1; then
    warn "Port 9000 already in use. Kill it first: kill -9 \$(lsof -t -i:9000)"
    exit 1
fi

log "  Starting sbt (first compile can take 3-5 minutes)..."
sbt "run 9000" -J-XX:MaxMetaspaceSize=1024m -J-Xmx4g 2>&1 &
SBT_PID=$!

# Wait for backend to be ready (up to 10 minutes)
log "  ⏳ Waiting for backend to compile and start..."
WAITED=0
MAX_WAIT=600
while [ $WAITED -lt $MAX_WAIT ]; do
    if lsof -i:9000 -sTCP:LISTEN >/dev/null 2>&1; then
        ok "Backend running (PID: $SBT_PID, port 9000)"
        break
    fi
    # Check if sbt crashed
    if ! kill -0 "$SBT_PID" 2>/dev/null; then
        fail "Backend (sbt) crashed. Check output above for errors."
        exit 1
    fi
    sleep 5
    WAITED=$((WAITED + 5))
    MINS=$((WAITED / 60))
    SECS=$((WAITED % 60))
    printf "\r  ⏳ Compiling... %dm %ds elapsed" $MINS $SECS
done

if [ $WAITED -ge $MAX_WAIT ]; then
    fail "Backend timed out after 10 minutes"
    exit 1
fi

# ═══════════════════════════════════════════════════════════════════════
#  STEP 6: Register Worker + Skeletonization Service
# ═══════════════════════════════════════════════════════════════════════
log "🧬 ${BOLD}Step 6/6: Worker + Skeletonization Service${NC}"

# Wait for backend to insert the `localhost` datastore row (up to 60s).
# Without this, workers table FK constraint fails.
log "  Waiting for datastore row to be created by backend..."
DS_WAIT=0
while [ $DS_WAIT -lt 60 ]; do
    DS_COUNT=$(psql -U postgres webknossos -tAc "SELECT COUNT(*) FROM webknossos.datastores WHERE name='localhost';" 2>/dev/null || echo 0)
    if [ "$DS_COUNT" -ge 1 ]; then
        ok "Datastore 'localhost' row present"
        break
    fi
    sleep 2
    DS_WAIT=$((DS_WAIT + 2))
done
if [ "$DS_COUNT" -lt 1 ]; then
    warn "Datastore row never appeared — worker registration will fail. Skipping skeletonization service."
else
    # Register dev worker (idempotent via ON CONFLICT)
    WORKER_CMDS=$(psql -U postgres webknossos -tAc "SELECT COALESCE(array_to_string(supportedJobCommands,','),'') FROM webknossos.workers WHERE _id='6194dc03040200b0027f28a1';" 2>/dev/null || echo "")
    if echo "$WORKER_CMDS" | grep -q "skeletonize_segmentation"; then
        ok "Dev worker already registered with skeletonize_segmentation"
    else
        log "  Registering dev worker..."
        if (cd "$SCRIPT_DIR/tools/postgres" && node dbtool.js enable-jobs >/dev/null 2>&1); then
            ok "Dev worker registered (key=secretWorkerKey)"
        else
            warn "Could not register dev worker. Skipping skeletonization service."
            DS_COUNT=0  # short-circuit service start below
        fi
    fi
fi

if [ "$DS_COUNT" -ge 1 ]; then
    # Install uv if missing
    if ! command -v uv >/dev/null 2>&1; then
        log "  Installing uv (one-time)..."
        if brew install uv >/dev/null 2>&1; then
            ok "uv installed"
        else
            warn "Could not install uv. Skipping skeletonization service."
            DS_COUNT=0
        fi
    fi

    if ! command -v cmake >/dev/null 2>&1; then
        warn "cmake not found — kimimaro native build may fail. Install: brew install cmake"
    fi
fi

if [ "$DS_COUNT" -ge 1 ]; then
    SKEL_LOG="$SCRIPT_DIR/tools/skeletonization-service/service.log"
    log "  Starting skeletonization-service (first run compiles kimimaro, ~1-2 min)..."
    log "  Logs: $SKEL_LOG"

    WORKER_KEY=secretWorkerKey \
    BINARY_DATA_DIR="$SCRIPT_DIR/binaryData" \
    KIMIMARO_PARALLEL=1 \
        uv run "$SCRIPT_DIR/tools/skeletonization-service/main.py" \
        --wk-uri http://localhost:9000 \
        --polling-interval-seconds 5 \
        > "$SKEL_LOG" 2>&1 &
    SKEL_PID=$!

    # Wait up to 180s for service to start polling
    WAITED=0
    while [ $WAITED -lt 180 ]; do
        if grep -q "Polling" "$SKEL_LOG" 2>/dev/null; then
            ok "Skeletonization service running (PID: $SKEL_PID)"
            break
        fi
        if ! kill -0 "$SKEL_PID" 2>/dev/null; then
            fail "Skeletonization service crashed. Check $SKEL_LOG"
            tail -20 "$SKEL_LOG" 2>/dev/null
            warn "Continuing without skeletonization service"
            SKEL_PID=""
            break
        fi
        sleep 2
        WAITED=$((WAITED + 2))
    done
    if [ $WAITED -ge 180 ] && [ -n "$SKEL_PID" ]; then
        warn "Skeletonization service slow to start — still compiling kimimaro? Check $SKEL_LOG"
    fi

    # --- Start Ingest Service ---
    INGEST_LOG="$SCRIPT_DIR/tools/ingest-service/service.log"
    log "  Starting ingest-service..."
    
    WORKER_KEY=secretWorkerKey \
    BINARY_DATA_DIR="$SCRIPT_DIR/binaryData" \
        uv run "$SCRIPT_DIR/tools/ingest-service/main.py" \
        --wk-uri http://localhost:9000 \
        --polling-interval-seconds 5 \
        > "$INGEST_LOG" 2>&1 &
    INGEST_PID=$!
    log "  Ingest service running (PID: $INGEST_PID)"

    # --- Start Orchestrator Service ---
    ORCH_LOG="$SCRIPT_DIR/tools/orchestrator-service/service.log"
    log "  Starting orchestrator-service..."
    
    WORKER_KEY=secretWorkerKey \
    BINARY_DATA_DIR="$SCRIPT_DIR/binaryData" \
        uv run "$SCRIPT_DIR/tools/orchestrator-service/main.py" \
        --wk-uri http://localhost:9000 \
        --polling-interval-seconds 5 \
        > "$ORCH_LOG" 2>&1 &
    ORCH_PID=$!
    log "  Orchestrator service running (PID: $ORCH_PID)"

fi

# ═══════════════════════════════════════════════════════════════════════
#  Ready!
# ═══════════════════════════════════════════════════════════════════════
echo ""
echo -e "${GREEN}${BOLD}"
echo "  ═══════════════════════════════════════════════════════"
echo "   🚀  Webknossos is running!"
echo "  ═══════════════════════════════════════════════════════"
echo ""
echo "   🌐  App:             http://localhost:9000"
echo "   🦴  FossilDB:        port 7155"
echo "   🐘  PostgreSQL:      port 5432"
echo "   🧬  Skeletonization: polling every 5s (logs: tools/skeletonization-service/service.log)"
echo "   📥  Ingest:          polling every 5s (logs: tools/ingest-service/service.log)"
echo "   🎼  Orchestrator:    polling every 5s (logs: tools/orchestrator-service/service.log)"
echo ""
echo "   📧  Login:      sample@scm.io"
echo "   🔑  Password:   secret"
echo ""
echo "   Press Ctrl+C to stop all services"
echo "  ═══════════════════════════════════════════════════════"
echo -e "${NC}"

# ─── Keep alive ───────────────────────────────────────────────────────
wait
