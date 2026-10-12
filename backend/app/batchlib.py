"""ComfyUI-style batch execution primitives: envelope, isolated loop, resume checkpoints."""
from __future__ import annotations
import asyncio, hashlib, json, os, tempfile, time, uuid
from collections import Counter
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional

STATUSES = {"pending", "running", "succeeded", "failed", "skipped", "cancelled"}
RETRYABLE = ("timeout", "timed out", "tempor", "network", "connection", "429", "rate limit", "busy", "process exited", "worker crashed")
PERMANENT = ("schema", "invalid", "missing", "unsupported", "permission", "signature", "contract", "manifest")

def fingerprint(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def stable_item_id(value: Any, index: int = 0) -> str:
    return "item_" + fingerprint({"index": index, "value": value})[:16]

def make_item(value: Any, index: int, item_id: str = "") -> Dict[str, Any]:
    return {"item_id": item_id or stable_item_id(value, index), "index": index, "input": value,
            "status": "pending", "attempt": 0, "fingerprint": fingerprint(value), "result": None,
            "error": None, "warnings": [], "started_at": None, "finished_at": None}

def new_batch(values: Iterable[Any] = (), *, batch_id: str = "", source: str = "", options: Optional[dict] = None) -> Dict[str, Any]:
    vals = list(values)
    items = [make_item(v, i) for i, v in enumerate(vals)]
    # batch_id 内容派生（稳定跨重跑）：同一 items+options 得到同一 batch_id，
    # 断点续跑可匹配；items/参数变化 → batch_id 变化 → 不会误恢复旧结果。
    if not batch_id:
        seed = {"options": options or {}, "items": [it["fingerprint"] for it in items]}
        batch_id = "batch_" + fingerprint(seed)[:24]
    return {"schema_version": 1, "batch_id": batch_id, "items": items,
            "meta": {"source": source, "created_at": time.time(), "options": options or {}}}

def normalize_batch(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict) and isinstance(value.get("items"), list):
        out = dict(value); out["schema_version"] = int(out.get("schema_version", 1)); out["items"] = [dict(x) for x in out["items"] if isinstance(x, dict)]
        for i, item in enumerate(out["items"]):
            item.setdefault("item_id", stable_item_id(item.get("input"), i)); item.setdefault("index", i); item.setdefault("status", "pending"); item.setdefault("attempt", 0); item.setdefault("fingerprint", fingerprint(item.get("input"))); item.setdefault("warnings", [])
        return out
    if isinstance(value, list): return new_batch(value)
    if value is None: return new_batch([])
    return new_batch([value])

def save_checkpoint(batch: Dict[str, Any], path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".batch-", suffix=".json", dir=os.path.dirname(os.path.abspath(path)))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f: json.dump(batch, f, ensure_ascii=False, indent=2, default=str); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try: os.remove(tmp)
            except OSError: pass

def load_checkpoint(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as f: return normalize_batch(json.load(f))
    except (OSError, ValueError, TypeError): return None

def classify_error(exc: BaseException) -> Dict[str, Any]:
    msg = str(exc); low = msg.lower()
    # 明确的结构/契约错误优先判为不可重试（防止 "invalid" 出现在一般文案里被误判）
    permanent = any(x in low for x in PERMANENT)
    retryable = (not permanent) and any(x in low for x in RETRYABLE)
    return {"category": "retryable" if retryable else "permanent",
            "message": msg[:500], "retryable": retryable, "type": type(exc).__name__}

def summarize(batch: Dict[str, Any]) -> Dict[str, Any]:
    items = batch.get("items") or []; counts = Counter(str(i.get("status", "pending")) for i in items)
    errors = Counter(str((i.get("error") or {}).get("category", "unknown")) if isinstance(i.get("error"), dict) else "unknown" for i in items if i.get("status") == "failed")
    return {"batch_id": batch.get("batch_id"), "total": len(items), "succeeded": counts["succeeded"], "failed": counts["failed"], "pending": counts["pending"], "skipped": counts["skipped"], "cancelled": counts["cancelled"], "error_categories": dict(errors), "items": items, "finished_at": time.time()}

async def run_items(batch: Dict[str, Any], worker: Callable[[Dict[str, Any]], Awaitable[Any]], *, concurrency: int = 2, checkpoint_path: str = "", progress: Optional[Callable[[float], None]] = None, cancel_event: Any = None, retries: int = 0, resume: bool = True) -> Dict[str, Any]:
    batch = normalize_batch(batch); old = load_checkpoint(checkpoint_path) if (resume and checkpoint_path) else None
    if old and old.get("batch_id") == batch.get("batch_id"):
        prior = {x.get("item_id"): x for x in old.get("items", [])}
        for item in batch["items"]:
            p = prior.get(item.get("item_id"));
            if p and p.get("fingerprint") == item.get("fingerprint") and p.get("status") == "succeeded": item.update(p)
    sem = asyncio.Semaphore(max(1, int(concurrency or 1))); total = len(batch["items"])
    done = sum(i.get("status") in ("succeeded", "failed", "skipped", "cancelled") for i in batch["items"])
    lock = asyncio.Lock()
    def report():
        if progress:
            try: progress(done / max(total, 1))
            except Exception: pass
    async def one(item):
        nonlocal done
        if item.get("status") == "succeeded": report(); return
        async with sem:
            attempts = 0
            while True:
                if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)(): item["status"] = "cancelled"; break
                attempts += 1; item["attempt"] = int(item.get("attempt", 0)) + 1; item["status"] = "running"; item["started_at"] = time.time()
                try:
                    item["result"] = await worker(item); item["status"] = "succeeded"; item["error"] = None; break
                except asyncio.CancelledError: item["status"] = "cancelled"; raise
                except Exception as exc:
                    info = classify_error(exc); item["error"] = info
                    if attempts <= retries and info["retryable"]: await asyncio.sleep(min(30, 0.25 * (2 ** (attempts - 1)))); continue
                    item["status"] = "failed"; break
                finally: item["finished_at"] = time.time()
            async with lock:
                done += 1; report()
                if checkpoint_path: save_checkpoint(batch, checkpoint_path)
    await asyncio.gather(*(one(i) for i in batch["items"]))
    if checkpoint_path: save_checkpoint(batch, checkpoint_path)
    return batch
