# MagNeurific Spin-Up Procedure

This guide outlines the steps to spin up the local development environment for MagNeurific.

---

### Prerequisites
Before starting, ensure PostgreSQL is running:
```bash
pg_isready || brew services start postgresql@15
```

If FossilDB is not running, start it in a separate terminal:
```bash
rm -f fossildb/data/LOCK
bash fossildb/run.sh
```

---

## 1. Spin Up Backend
Open a new terminal tab/window and run the following command to compile and start the Scala/Play backend server:

```bash
# 1. Export required environment variables
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && source "$NVM_DIR/nvm.sh"
eval "$(/opt/homebrew/bin/brew shellenv)" 2>/dev/null || eval "$(/usr/local/bin/brew shellenv)"
export PATH="/opt/homebrew/opt/openjdk@21/bin:/opt/homebrew/opt/postgresql@15/bin:$PATH"
export JAVA_HOME="/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home"

# 2. Run sbt backend on port 9001
sbt "run 9001" -J-XX:MaxMetaspaceSize=1024m -J-Xmx4g
```

> [!NOTE]  
> The first compilation can take **3–5 minutes** to download dependencies and build. Subsequent starts are much faster (~30 seconds).

---

## 2. Spin Up Frontend
Open another terminal tab/window and start the frontend development server for hot-reloading:

```bash
# 1. Export required environment variables
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && source "$NVM_DIR/nvm.sh"
eval "$(/opt/homebrew/bin/brew shellenv)" 2>/dev/null || eval "$(/usr/local/bin/brew shellenv)"

# 2. Start the development server
yarn dev
```

---

## 3. Access the Application
Once both services are running and the backend shows a successful bind, navigate to:

👉 **[http://localhost:9000](http://localhost:9000)**

### Default Login Credentials

| Username | Password | Role |
| :--- | :--- | :--- |
| `sample@scm.io` | `secret` | Administrator |
| `sample2@scm.io` | `secret` | Regular User |
