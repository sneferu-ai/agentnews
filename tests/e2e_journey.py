#!/usr/bin/env python3
"""End-to-end CLI journey for AgentNews."""
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

def run(cmd, cwd=REPO, env=None, timeout=60):
    print(f"$ {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=cwd, env=env, text=True, capture_output=True, timeout=timeout)
    if proc.stdout:
        print(proc.stdout)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)
    if proc.returncode != 0:
        print(f"EXIT {proc.returncode}")
        sys.exit(proc.returncode)
    return proc.stdout


def main():
    tdir = Path(tempfile.mkdtemp())
    env_file = tdir / ".env"
    db = tdir / "agentnews.db"
    sneferu_runs = tdir / "sneferu_runs"
    static = tdir / "static_build"
    sneferu_runs.mkdir()
    shutil.copytree(REPO / "tests" / "fixtures" / "run-good", sneferu_runs / "run-good")
    env_file.write_text(
        f"PUBLIC_URL=https://news.example.com\n"
        f"PAYMENT_LINK_URL=https://buy.example.com/agentnews\n"
        f"ADMIN_TOKEN=test-admin-token\n"
        f"AGENTNEWS_DB={db}\n"
        f"SNEFERU_RUNS_DIR={sneferu_runs}\n"
        f"AGENTNEWS_HOST=127.0.0.1\n"
        f"AGENTNEWS_PORT=8778\n"
        f"STATIC_OUTPUT_DIR={static}\n"
        f"LOG_SALT=test-salt-e2e\n"
        f"AGENTNEWS_ALLOW_INSECURE=false\n"
        f"RATE_LIMIT_PER_DAY=1000\n"
        f"BURST_ALLOWANCE=100\n"
        f"STAGING_DELAY_MINUTES=0\n"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO / "src")

    try:
        run([sys.executable, "-m", "agentnews", "init", "--env-file", str(env_file)], env=env)
        run([sys.executable, "-m", "agentnews", "migrate", "--env-file", str(env_file)], env=env)
        import_out = run([sys.executable, "-m", "agentnews", "import-run", str(sneferu_runs / "run-good"), "--env-file", str(env_file), "--skip-verify-urls"], env=env)
        # extract pid=... from the import output line
        pid = None
        for line in import_out.splitlines():
            if "pid=" in line:
                pid = line.split("pid=")[1].split()[0]
                break
        assert pid, f"could not extract pid from import output: {import_out}"
        run([sys.executable, "-m", "agentnews", "sample", "set", "--env-file", str(env_file), pid], env=env)
        key_out = run([sys.executable, "-m", "agentnews", "key", "create", "--env-file", str(env_file), "--label", "e2e"], env=env)
        key = None
        for line in key_out.splitlines():
            if line.startswith("key="):
                key = line.split("=", 1)[1]
                break
        assert key, f"could not extract key from output: {key_out}"
        print(f"KEY={key}")

        print("\n--- starting server ---")
        server = subprocess.Popen(
            [sys.executable, "-m", "agentnews", "serve", "--env-file", str(env_file)],
            cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            time.sleep(2)

            import urllib.request
            base = "http://127.0.0.1:8778"
            health = urllib.request.urlopen(f"{base}/healthz", timeout=5).read().decode()
            print(f"health: {health}")
            catalog = urllib.request.urlopen(f"{base}/v1/catalog", timeout=5).read().decode()
            print(f"catalog: {catalog[:200]}")
            req = urllib.request.Request(f"{base}/v1/packages", headers={"X-API-Key": key})
            pkgs = urllib.request.urlopen(req, timeout=5).read().decode()
            print(f"packages: {pkgs[:200]}")
            sample = urllib.request.urlopen(f"{base}/v1/sample", timeout=5).read().decode()
            print(f"sample: {sample[:200]}")
        finally:
            print("\n--- stopping server ---")
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
            if server.stdout:
                print(server.stdout.read()[:2000])
            if server.stderr:
                print(server.stderr.read()[:2000], file=sys.stderr)

        print("\n--- deploy-static ---")
        run([sys.executable, "-m", "agentnews", "deploy-static", "--env-file", str(env_file)], env=env)
        print(f"static files: {list(static.rglob('*.html'))[:10]}")
        assert (static / "index.html").exists()
        assert (static / "articles").exists()

        print("\nE2E OK")
    finally:
        shutil.rmtree(tdir, ignore_errors=True)


if __name__ == "__main__":
    main()
