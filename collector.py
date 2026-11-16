"""GitHub collection with durable progress, bounded retries and one writer."""
import fcntl
import math
import os
import signal
import sqlite3
import sys
import time
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import requests

from config import DB_PATH, env_int
from db import get_connection, init_db, utc_now
from descriptions import TranslationError, sync_descriptions

BASE = "https://api.github.com"
SEARCHES = {
    "Cybersecurity": [
        "malware analysis", "reverse engineering", "binary analysis",
        "cybersecurity", "sandbox malware", "threat intelligence",
    ],
    "AI & Agent": [
        "AI agent", "agent framework", "MCP server", "LLM security",
        "agentic workflow", "AI coding agent",
    ],
    "Systems": [
        "Linux systems programming", "Rust systems", "compiler",
        "debugger", "operating system",
    ],
    "Academic": [
        "research implementation", "paper implementation", "machine learning research",
    ],
}


class APIError(RuntimeError):
    def __init__(self, message, status=None, fatal=False):
        super().__init__(message)
        self.status = status
        self.fatal = fatal


class CollectionBusy(RuntimeError):
    pass


class CollectionInterrupted(Exception):
    pass


def safe_error(error):
    """Never persist raw HTTP exceptions, response bodies, URLs or credentials."""
    if isinstance(error, (APIError, TranslationError)):
        return str(error)
    if isinstance(error, (ValueError, TypeError, KeyError)):
        return "API 数据格式校验失败"
    if isinstance(error, sqlite3.Error):
        return "数据库操作失败"
    return f"操作失败（{type(error).__name__}）"


@contextmanager
def collector_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep the lock inode: unlinking it could allow two simultaneous writers.
    with open(str(path) + ".collector.lock", "a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CollectionBusy("已有采集任务运行，本次未启动") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class Run:
    def __init__(self, con):
        self.con = con
        self.id = con.execute("""INSERT INTO collection_runs
            (started_at,status,phase,heartbeat_at,message)
            VALUES(?,'running','starting',?,'准备采集')""", (utc_now(), utc_now())).lastrowid
        self.con.commit()

    def checkpoint(self, phase=None, done=None, total=None, item=None, message=None):
        changes = {"heartbeat_at": utc_now()}
        for key, value in (("phase", phase), ("progress_done", done),
                           ("progress_total", total), ("current_item", item), ("message", message)):
            if value is not None:
                changes[key] = value
        columns = ",".join(f"{key}=?" for key in changes)
        self.con.execute(f"UPDATE collection_runs SET {columns} WHERE id=?", (*changes.values(), self.id))
        self.con.commit()
        row = self.con.execute("SELECT * FROM collection_runs WHERE id=?", (self.id,)).fetchone()
        total = row["progress_total"]
        progress = f"{row['progress_done']}/{total}" if total else "准备中"
        percent = f" {row['progress_done']/total:.0%}" if total else ""
        print(f"[{row['phase']}] {progress}{percent} | 新增 {row['discovered']} | "
              f"快照 {row['updated']} | 错误 {row['errors']} | {row['message']} | {row['current_item'] or ''}", flush=True)

    def error(self, phase, item, error):
        message = safe_error(error)
        with self.con:
            self.con.execute("""INSERT INTO collection_errors(run_id,phase,item,message,created_at)
                VALUES(?,?,?,?,?)""", (self.id, phase, item, message, utc_now()))
            column = {"discovery": "search_errors", "refresh": "refresh_errors",
                      "translation": "translation_errors"}.get(phase, "system_errors")
            self.con.execute(f"UPDATE collection_runs SET errors=errors+1,{column}={column}+1 WHERE id=?", (self.id,))
        error.reported = True
        print(f"[错误] {phase} | {item} | {message}", flush=True)

    def finish(self, status, message):
        with self.con:
            self.con.execute("""UPDATE collection_runs SET status=?,finished_at=?,heartbeat_at=?,
                message=? WHERE id=?""", (status, utc_now(), utc_now(), message, self.id))
        self.checkpoint(message=message)


def cooperative_wait(seconds, run=None, reason="请求间隔"):
    end = time.monotonic() + max(0, seconds)
    if run is not None and seconds >= 2:
        run.checkpoint(message=f"{reason}，等待 {math.ceil(seconds)} 秒")
    while (remaining := end-time.monotonic()) > 0:
        time.sleep(min(5, remaining))
        if run is not None and remaining > 5:
            run.checkpoint(message=f"{reason}，剩余约 {math.ceil(max(0,end-time.monotonic()))} 秒")


class GitHubClient:
    def __init__(self, token, run=None):
        self.run = run
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"

    def close(self):
        self.session.close()

    def _wait_seconds(self, response, attempt):
        waits = [float(2 ** attempt)]
        retry = response.headers.get("Retry-After")
        if retry:
            try:
                waits.append(float(retry))
            except ValueError:
                try:
                    waits.append(parsedate_to_datetime(retry).timestamp()-time.time())
                except (ValueError, TypeError, OverflowError):
                    pass
        reset = response.headers.get("X-RateLimit-Reset")
        if reset and response.headers.get("X-RateLimit-Remaining") == "0":
            try:
                waits.append(float(reset)-time.time()+1)
            except ValueError:
                pass
        if response.status_code in (403, 429) and not retry and not reset:
            waits.append(60)
        wait = max(waits)
        if not math.isfinite(wait) or wait > 3600:
            raise APIError("服务端要求等待超过一小时，请稍后重试")
        return max(0, wait)

    def request_json(self, url, params=None):
        redirects = 0
        for attempt in range(4):
            parsed = urlparse(url)
            if parsed.scheme != "https" or parsed.netloc != "api.github.com":
                raise APIError("拒绝向非 GitHub API 地址发送凭据", fatal=True)
            try:
                response = self.session.get(url, params=params, timeout=(10, 30), allow_redirects=False)
            except (requests.Timeout, requests.ConnectionError):
                if attempt == 3:
                    raise APIError("网络连接或请求超时，重试已耗尽") from None
                cooperative_wait(2 ** attempt, self.run, "网络重试")
                continue
            status = response.status_code
            if status in (301, 302, 307, 308):
                redirects += 1
                if redirects > 3:
                    response.close()
                    raise APIError("API 重定向次数过多")
                url = response.headers.get("Location", "")
                params = None
                response.close()
                continue
            if status == 401:
                response.close()
                raise APIError("GitHub 认证失败，请检查 Token 的有效期及权限", status=401, fatal=True)
            limited = status == 429 or (status == 403 and (
                response.headers.get("X-RateLimit-Remaining") == "0" or
                "rate limit" in response.text.lower() or "secondary rate" in response.text.lower()))
            if limited or status in (500, 502, 503, 504):
                try:
                    wait = self._wait_seconds(response, attempt)
                finally:
                    response.close()
                if attempt == 3:
                    raise APIError(f"GitHub HTTP {status}，重试已耗尽", status=status)
                cooperative_wait(wait, self.run, "API 限流" if limited else "服务暂不可用")
                continue
            if status != 200:
                response.close()
                raise APIError(f"GitHub HTTP {status}", status=status)
            try:
                payload = response.json()
            except ValueError:
                raise APIError("GitHub 返回了无效 JSON") from None
            finally:
                response.close()
            if not isinstance(payload, dict):
                raise APIError("GitHub 返回的数据结构异常")
            return payload
        raise APIError("API 请求未完成")


def validate_repo(item, require_counts=False):
    if not isinstance(item, dict):
        raise ValueError("invalid repository payload")
    github_id = item.get("id")
    full_name = item.get("full_name")
    if type(github_id) is not int or github_id <= 0:
        raise ValueError("invalid repository id")
    if not isinstance(full_name, str) or len(full_name.split("/")) != 2:
        raise ValueError("invalid repository name")
    owner, name = full_name.split("/")
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
    if not owner or not name or any(ch not in allowed for ch in owner+name):
        raise ValueError("invalid repository name")
    if require_counts:
        for key in ("stargazers_count", "forks_count", "open_issues_count"):
            if type(item.get(key)) is not int or item[key] < 0:
                raise ValueError("invalid repository counts")
    if type(item.get("archived", False)) is not bool:
        raise ValueError("invalid archive flag")
    for key in ("description", "language", "created_at", "updated_at"):
        if item.get(key) is not None and not isinstance(item[key], str):
            raise ValueError("invalid repository field")
    for key in ("created_at", "updated_at"):
        if item.get(key) is not None:
            parsed = datetime.fromisoformat(item[key].replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("repository time must include timezone")
    topics = item.get("topics") or []
    if not isinstance(topics, list) or any(not isinstance(topic, str) for topic in topics):
        raise ValueError("invalid topics")


def upsert_repo(item, category, con, tracked_id=None):
    """Keep local identity/history across GitHub renames; never guess ambiguous merges."""
    validate_repo(item)
    github_id, full_name = item["id"], item["full_name"]
    by_id = con.execute("SELECT * FROM repos WHERE github_id=?", (github_id,)).fetchone()
    by_name = con.execute("SELECT * FROM repos WHERE full_name=?", (full_name,)).fetchone()
    tracked = con.execute("SELECT * FROM repos WHERE id=?", (tracked_id,)).fetchone() if tracked_id else None
    if tracked is not None and tracked["github_id"] not in (None, github_id):
        raise APIError("仓库路径对应的 GitHub ID 已变化，保留旧历史并停止本次更新")
    candidates = {r["id"]: r for r in (by_id, by_name, tracked) if r is not None}
    if len(candidates) > 1:
        raise APIError("仓库身份冲突，需要人工核对；本次未合并或覆盖历史")
    row = next(iter(candidates.values()), None)
    if row is not None and row["github_id"] not in (None, github_id):
        raise APIError("仓库名称被其他 GitHub ID 使用，本次未覆盖历史")
    # Treat all API text as data. Construct links ourselves rather than trusting response URLs.
    values = (github_id, full_name, item.get("description"), f"https://github.com/{full_name}",
              item.get("language"), item.get("created_at"), item.get("updated_at"),
              ",".join(item.get("topics") or []), int(bool(item.get("archived", False))))
    if row is None:
        cursor = con.execute("""INSERT INTO repos(github_id,full_name,description,html_url,language,
            created_at,updated_at,topics,is_archived,category) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (*values, category or "Other"))
        return cursor.lastrowid, True
    if (row["description"] or "") != (item.get("description") or ""):
        con.execute("UPDATE repos SET description_zh=NULL,description_zh_source=NULL WHERE id=?", (row["id"],))
    con.execute("""UPDATE repos SET github_id=?,full_name=?,description=?,html_url=?,language=?,
        created_at=?,updated_at=?,topics=?,is_archived=? WHERE id=?""", (*values, row["id"]))
    return row["id"], False


def record_snapshot(con, repo_id, run_id, item):
    validate_repo(item, require_counts=True)
    cursor = con.execute("""INSERT INTO snapshots(repo_id,run_id,stars,forks,open_issues,captured_at)
        VALUES(?,?,?,?,?,?) ON CONFLICT DO NOTHING""",
        (repo_id, run_id, item["stargazers_count"], item["forks_count"], item["open_issues_count"], utc_now()))
    if cursor.rowcount == 0:
        # Only an existing snapshot for this run is a valid idempotent no-op.
        if not con.execute("SELECT 1 FROM snapshots WHERE repo_id=? AND run_id=?", (repo_id, run_id)).fetchone():
            raise APIError("快照时间冲突，本次未覆盖历史")
        return False
    return True


def discover(con, client, run, per_page, max_pages):
    tasks = [(category, term) for category, terms in SEARCHES.items() for term in terms]
    total, done, matched = len(tasks)*max_pages, 0, set()
    cutoff = (datetime.now(timezone.utc).date()-timedelta(days=90)).isoformat()
    run.checkpoint("discovery", 0, total, "", "发现候选仓库")
    for category, term in tasks:
        for page in range(1, max_pages+1):
            label = f"{category} · {term} · 第 {page} 页"
            run.checkpoint(item=label, message="搜索中")
            try:
                payload = client.request_json(f"{BASE}/search/repositories", {
                    "q": f'"{term}" created:>={cutoff}', "sort": "stars", "order": "desc",
                    "per_page": per_page, "page": page,
                })
                items = payload.get("items")
                if not isinstance(items, list):
                    raise APIError("搜索结果缺少 items 列表")
                if payload.get("incomplete_results"):
                    run.error("discovery", label, APIError("GitHub 标记本页搜索结果不完整"))
                for item in items:
                    try:
                        with con:
                            repo_id, is_new = upsert_repo(item, category, con)
                            con.execute("UPDATE collection_runs SET discovered=discovered+? WHERE id=?", (int(is_new), run.id))
                        matched.add(repo_id)
                    except (APIError, ValueError, TypeError, KeyError) as error:
                        run.error("discovery", label, error)
                with con:
                    con.execute("UPDATE collection_runs SET matched=? WHERE id=?", (len(matched), run.id))
                done += 1
                stop = len(items) < per_page
            except (APIError, ValueError, TypeError) as error:
                run.error("discovery", label, error)
                if isinstance(error, APIError) and error.fatal:
                    raise
                done += 1
                stop = True
            if stop:
                done += max_pages-page
            run.checkpoint(done=done, message="当前搜索已处理（含无需请求的后续页）")
            if stop:
                break
            cooperative_wait(2.1, run)
        cooperative_wait(2.1, run)


def refresh_tracked(con, client, run):
    rows = con.execute("SELECT id,full_name FROM repos ORDER BY id").fetchall()
    run.checkpoint("refresh", 0, len(rows), "", "更新仓库与 Star 快照")
    for index, row in enumerate(rows, 1):
        run.checkpoint(item=row["full_name"], message="请求仓库最新数据")
        try:
            item = client.request_json(f"{BASE}/repos/{row['full_name']}")
            validate_repo(item, require_counts=True)
            with con:
                repo_id, _ = upsert_repo(item, None, con, tracked_id=row["id"])
                added = record_snapshot(con, repo_id, run.id, item)
                con.execute("UPDATE collection_runs SET updated=updated+? WHERE id=?", (int(added), run.id))
        except (APIError, ValueError, TypeError, KeyError) as error:
            run.error("refresh", row["full_name"], error)
            if isinstance(error, APIError) and error.fatal:
                raise
        run.checkpoint(done=index, message="仓库已处理")
        cooperative_wait(0.15, run)


def run_collection(path=None, client_factory=GitHubClient):
    path = path or DB_PATH
    per_page = env_int("PER_PAGE", 50, 1, 100)
    max_pages = env_int("MAX_PAGES", 2, 1, min(10, 1000//per_page))
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if not token:
        raise ValueError("未配置 GITHUB_TOKEN，请在项目 .env 中配置公开仓库读取 Token")
    with collector_lock(path):
        init_db(path)
        with closing(get_connection(path)) as con:
            # Holding the process lock proves no cooperating collector is still running.
            with con:
                con.execute("""UPDATE collection_runs SET status='interrupted',
                    message='先前任务未收尾；已由新任务检测到中断'
                    WHERE finished_at IS NULL AND status IN ('running','legacy')""")
            run = Run(con)
            client = None
            status, message = "failed", "采集失败"
            try:
                client = client_factory(token, run)
                discover(con, client, run, per_page, max_pages)
                refresh_tracked(con, client, run)
                sync_descriptions(con, run)
                row = con.execute("SELECT errors,updated FROM collection_runs WHERE id=?", (run.id,)).fetchone()
                status = "success" if not row["errors"] else ("partial" if row["updated"] else "failed")
                message = {"success": "采集完成", "partial": "部分完成，请检查错误记录", "failed": "采集失败，未获得新快照"}[status]
            except (KeyboardInterrupt, CollectionInterrupted):
                con.rollback()
                status, message = "interrupted", "采集已中断；已提交的数据保留"
            except Exception as error:
                con.rollback()
                if not getattr(error, "reported", False):
                    run.error("system", "采集任务", error)
                message = safe_error(error)
            finally:
                try:
                    run.finish(status, message)
                finally:
                    if client is not None:
                        client.close()
            return 0 if status == "success" else (130 if status == "interrupted" else 1)


def run_translation(path=None):
    path = path or DB_PATH
    with collector_lock(path):
        init_db(path)
        with closing(get_connection(path)) as con:
            run = Run(con)
            status, message = "failed", "中文简介更新失败"
            try:
                sync_descriptions(con, run)
                errors = con.execute("SELECT errors FROM collection_runs WHERE id=?", (run.id,)).fetchone()[0]
                status = "partial" if errors else "success"
                message = "部分简介翻译失败，下次运行会重试" if errors else "已有项目中文简介更新完成"
            except (KeyboardInterrupt, CollectionInterrupted):
                status, message = "interrupted", "中文简介更新已中断；已提交的译文保留"
            except Exception as error:
                con.rollback()
                run.error("translation", "中文简介更新", error)
            finally:
                run.finish(status, message)
            return 0 if status == "success" else (130 if status == "interrupted" else 1)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="GitHub 项目采集与中文简介更新")
    parser.add_argument("--translate-only", action="store_true", help="仅补齐已有项目中文简介，不请求 GitHub 或新增快照")
    args = parser.parse_args()
    def terminate(signum, frame):
        raise CollectionInterrupted()
    signal.signal(signal.SIGTERM, terminate)
    try:
        if args.translate_only:
            return run_translation()
        return run_collection()
    except (CollectionBusy, ValueError) as error:
        print(str(error), file=sys.stderr, flush=True)
        return 2
    except Exception as error:
        print(safe_error(error), file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
