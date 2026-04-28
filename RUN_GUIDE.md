# MagNeurific / Webknossos — Local Development Run Guide

## Quick Start

```bash
cd ~/MagNeurific
./start.sh
```

That's it. The script handles everything. Open **http://localhost:9000** once it says "ready".

---

## Default Login Credentials

| Field    | Value            |
|----------|------------------|
| Email    | `sample@scm.io`  |
| Password | `secret`         |

There's also a non-admin account: `sample2@scm.io` / `secret`

---

## Prerequisites (One-Time Setup)

If `./start.sh` fails, you may need to install prerequisites manually.

### 1. Homebrew

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

> [!NOTE]
> Requires your Mac password (sudo). Run in your terminal directly.

### 2. NVM + Node.js

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
# Restart terminal, then:
nvm install 22
```

### 3. Java 21, sbt, PostgreSQL

```bash
brew install openjdk@21 sbt postgresql@15
```

### 4. Shell Configuration

Add to your `~/.zshrc`:

```bash
# Webknossos dependencies
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && source "$NVM_DIR/nvm.sh"
eval "$(/opt/homebrew/bin/brew shellenv)"
export PATH="/opt/homebrew/opt/openjdk@21/bin:/opt/homebrew/opt/postgresql@15/bin:$PATH"
export JAVA_HOME="/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home"
```

### 5. Yarn + Node Dependencies

```bash
corepack enable
cd ~/MagNeurific
yarn install
```

---

## Services Overview

| Service    | Port  | Purpose                           |
|------------|-------|-----------------------------------|
| Backend    | 9000  | Scala/Play app — serves the UI    |
| FossilDB   | 7155  | Key-value store for annotations   |
| PostgreSQL | 5432  | Relational database               |

---

## Common Issues & Workarounds

### FossilDB won't start — "Address already in use"

FossilDB is already running from a previous session.

```bash
# Kill the old instance
kill $(lsof -t -i:7155)
# Or just let start.sh detect it — it skips if already running
```

### FossilDB won't start — RocksDB lock error

Stale lock file from an unclean shutdown.

```bash
rm -f fossildb/data/LOCK
```

> [!TIP]
> `start.sh` automatically removes the lock file on every startup.

### Backend won't start — port 9000 in use

```bash
# Find and kill whatever is on port 9000
kill -9 $(lsof -t -i:9000)
```

### Backend shows "Could not load main view template"

Frontend assets haven't been built yet.

```bash
yarn build
```

> [!TIP]
> `start.sh` checks for this and runs the build automatically if needed.

### Backend takes forever to start

The **first** sbt compile downloads dependencies and compiles the full Scala codebase. This can take **3–5 minutes**. Subsequent starts are much faster (~30 seconds).

### `yarn`, `node`, `java`, or `sbt` not found

Your shell isn't loading the correct PATH. Either:
- Restart your terminal (to pick up `~/.zshrc` changes)
- Or manually source the environment:

```bash
source ~/.zshrc
```

### PostgreSQL won't start

```bash
# Check if it's already running
pg_isready

# Start it
brew services start postgresql@15

# If data directory is corrupted, reinitialize:
rm -rf /opt/homebrew/var/postgresql@15
initdb --locale=en_US.UTF-8 -E UTF-8 /opt/homebrew/var/postgresql@15
brew services start postgresql@15
```

### Database schema needs refresh

```bash
cd tools/postgres
node dbtool.js refresh-schema
```

> [!WARNING]
> This drops and recreates all tables. All data will be lost.

---

## Manual Startup (3 Terminals)

If you prefer to run services individually:

**Terminal 1 — FossilDB:**
```bash
cd ~/MagNeurific
rm -f fossildb/data/LOCK
bash fossildb/run.sh
```

**Terminal 2 — Backend:**
```bash
cd ~/MagNeurific
sbt "run 9000" -J-XX:MaxMetaspaceSize=1024m -J-Xmx4g
```

**Terminal 3 — Frontend (optional, for hot-reload during dev):**
```bash
cd ~/MagNeurific
yarn dev
```

---

## Useful Commands

```bash
# Kill all related processes
yarn kill-listeners

# Check what's running on relevant ports
yarn listening

# Rebuild frontend assets
yarn build

# Reset database schema (DESTRUCTIVE)
cd tools/postgres && node dbtool.js refresh-schema

# View FossilDB logs
cat fossildb/logs

# Run frontend type checker
yarn typecheck

# Run tests
yarn test
```

---

## Database Access

Query the database directly:

```bash
psql -U postgres webknossos
```

Useful queries:

```sql
-- List all users
SELECT m.email, m.firstname, m.lastname, m.issuperuser, m.created
FROM webknossos.multiusers m ORDER BY m.created;

-- List all datasets
SELECT * FROM webknossos.datasets;

-- List all organizations
SELECT * FROM webknossos.organizations;
```
