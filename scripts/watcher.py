#!/usr/bin/env python3
"""
目录监听脚本
监听网易云下载目录，检测到新的 .ncm 文件后自动解密 + 整理到音乐库。

用法:
  python watcher.py --watch ~/Music/CloudMusic/ --output music_library/
"""

import argparse
import hashlib
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# 导入同目录下的解密和整理模块。
# 因为本脚本既可能被 python scripts/watcher.py 直接运行，也可能被 python -m 调用，
# 所以将脚本所在目录加入 sys.path；加入前显式解析为绝对路径，避免重复或相对路径歧义。
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
from ncm_decrypt import decrypt_file
from organize import organize_file


# 已处理文件记录
PROCESSED_FILE = Path(__file__).resolve().parent.parent / "config" / "processed.json"
MAX_PROCESSED_AGE_DAYS = 90  # processed.json 中保留 90 天记录
MAX_PROCESSED_ENTRIES = 10000  # 最多保留 10000 条记录

# 临时文件、重试队列
_MAX_RETRY_ATTEMPTS = 3
_RETRY_DELAY_SECONDS = 10
_OBSERVER_JOIN_TIMEOUT = 3600  # observer.join 超时时间

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def load_processed() -> dict:
    """加载已处理文件记录"""
    if not PROCESSED_FILE.exists():
        return {}
    try:
        with open(PROCESSED_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return _expire_processed(data)
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"读取 processed.json 失败: {e}")
    return {}


def save_processed(processed: dict):
    """保存已处理文件记录，写之前先过期清理"""
    processed = _expire_processed(processed)
    PROCESSED_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PROCESSED_FILE, "w", encoding="utf-8") as f:
        json.dump(processed, f, ensure_ascii=False, indent=2)


def _expire_processed(processed: dict) -> dict:
    """清理过期和超限的已处理记录"""
    if not isinstance(processed, dict):
        return {}

    cutoff = time.time() - MAX_PROCESSED_AGE_DAYS * 86400
    expired = [k for k, v in processed.items()
               if not isinstance(v, dict) or v.get("timestamp", 0) < cutoff]
    for k in expired:
        processed.pop(k, None)

    # 限制最大条目数：按 timestamp 排序保留最新的
    if len(processed) > MAX_PROCESSED_ENTRIES:
        sorted_items = sorted(
            processed.items(),
            key=lambda item: item[1].get("timestamp", 0),
            reverse=True,
        )
        processed = dict(sorted_items[:MAX_PROCESSED_ENTRIES])

    return processed


def get_file_md5(file_path: str) -> str:
    """计算文件 MD5"""
    hasher = hashlib.md5()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


class NcmHandler(FileSystemEventHandler):
    """监听 .ncm 文件创建事件"""

    def __init__(self, output_dir: str, temp_dir: str):
        self.output_dir = output_dir
        self.temp_dir = temp_dir
        self.processed = load_processed()
        os.makedirs(temp_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        # 防止 on_created / on_modified 同时触发同一文件的处理
        self._processing_lock = threading.Lock()
        self._processing: set[str] = set()

        # 重试队列：记录超时后未能立即处理的文件
        self._retry_queue: list[tuple[str, int]] = []
        self._retry_lock = threading.Lock()
        self._retry_thread: Optional[threading.Thread] = None
        self._stop_retry = threading.Event()

    def start_retry_worker(self):
        """启动后台重试线程"""
        self._retry_thread = threading.Thread(target=self._retry_worker, daemon=True)
        self._retry_thread.start()

    def stop_retry_worker(self):
        """停止后台重试线程"""
        self._stop_retry.set()
        if self._retry_thread and self._retry_thread.is_alive():
            self._retry_thread.join(timeout=5.0)

    def _retry_worker(self):
        """后台重试队列处理"""
        while not self._stop_retry.is_set():
            time.sleep(_RETRY_DELAY_SECONDS)
            with self._retry_lock:
                queue = self._retry_queue[:]
                self._retry_queue = []
            for file_path, attempts in queue:
                if self._stop_retry.is_set():
                    break
                if attempts < _MAX_RETRY_ATTEMPTS:
                    logger.info(f"[重试 {attempts + 1}/{_MAX_RETRY_ATTEMPTS}] {file_path}")
                    self._process_file(file_path, from_retry=True)
                else:
                    logger.error(f"[死信] 超过最大重试次数，放弃处理: {file_path}")

    def _enqueue_retry(self, file_path: str, attempts: int):
        """将文件加入重试队列"""
        with self._retry_lock:
            self._retry_queue.append((file_path, attempts))

    def on_created(self, event):
        if event.is_directory:
            return
        if not event.src_path.lower().endswith(".ncm"):
            return
        self._process_file(event.src_path)

    def on_modified(self, event):
        if event.is_directory:
            return
        if not event.src_path.lower().endswith(".ncm"):
            return
        self._process_file(event.src_path)

    def _process_file(self, file_path: str, from_retry: bool = False):
        """处理单个 .ncm 文件：解密 → 整理"""
        # 防止并发处理同一文件
        with self._processing_lock:
            if file_path in self._processing:
                return
            self._processing.add(file_path)

        try:
            self._do_process_file(file_path, from_retry=from_retry)
        finally:
            with self._processing_lock:
                self._processing.discard(file_path)

    def _do_process_file(self, file_path: str, from_retry: bool = False):
        """处理单个 .ncm 文件的实际逻辑"""
        # 等文件写入完成（网易云下载可能还在写）
        if not self._wait_for_file_ready(file_path):
            if not from_retry:
                self._enqueue_retry(file_path, 0)
            return

        # 检查是否已处理
        try:
            file_md5 = get_file_md5(file_path)
        except IOError as e:
            logger.warning(f"计算 MD5 失败，跳过: {file_path}: {e}")
            return

        if file_md5 in self.processed:
            logger.info(f"[跳过] 已处理过: {os.path.basename(file_path)}")
            return

        filename = os.path.basename(file_path)
        logger.info(f"[发现新文件] {filename}")

        decrypted: Optional[str] = None
        try:
            # 解密到临时目录
            logger.info("[解密中]...")
            decrypted = decrypt_file(file_path, self.temp_dir)
            if not decrypted:
                logger.error(f"[失败] 解密失败: {filename}")
                return

            # 整理到音乐库
            logger.info("[整理中]...")
            organized = organize_file(decrypted, self.output_dir, move=True)
            if organized:
                # 记录已处理
                self.processed[file_md5] = {
                    "filename": filename,
                    "output": organized.path,
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "timestamp": int(time.time()),
                }
                save_processed(self.processed)
                logger.info(f"[完成] → {organized.path}")
            else:
                logger.warning(f"[警告] 整理失败: {organized.message}")
        except Exception as e:
            logger.error(f"[错误] 处理失败 {filename}: {e}", exc_info=True)
        finally:
            # 确保临时文件被清理
            if decrypted and os.path.exists(decrypted):
                try:
                    os.remove(decrypted)
                except OSError as e:
                    logger.warning(f"[警告] 清理临时文件失败: {decrypted}: {e}")

    def _wait_for_file_ready(self, file_path: str, timeout: float = 30.0) -> bool:
        """等待文件写入完成"""
        start = time.time()
        last_size = -1
        stable_count = 0
        while time.time() - start < timeout:
            if not os.path.exists(file_path):
                time.sleep(0.5)
                continue
            current_size = os.path.getsize(file_path)
            if current_size > 0 and current_size == last_size:
                stable_count += 1
                # 连续两次大小稳定才认为写入完成，避免写入抖动
                if stable_count >= 2:
                    return True
            else:
                stable_count = 0
            last_size = current_size
            time.sleep(1.0)
        logger.warning(f"[超时] 等待文件写入超时: {file_path}")
        return False

    def scan_existing(self, watch_dir: str):
        """启动时扫描已有但未处理的 .ncm 文件"""
        ncm_files = list(Path(watch_dir).rglob("*.ncm"))
        if not ncm_files:
            return
        logger.info(f"[扫描] 发现 {len(ncm_files)} 个 .ncm 文件，检查是否有未处理的...")
        for f in ncm_files:
            self._process_file(str(f))


def main():
    parser = argparse.ArgumentParser(
        description="目录监听 - 自动解密整理网易云下载的 .ncm 文件"
    )
    parser.add_argument("--watch", required=True,
                        help="监听目录 (网易云下载目录)")
    parser.add_argument("-o", "--output", default="music_library",
                        help="音乐库输出目录 (默认 music_library)")
    parser.add_argument("--temp", default=None,
                        help="临时解密目录 (默认 output_dir/.tmp)")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        help="日志级别 (默认 INFO)")
    args = parser.parse_args()

    logger.setLevel(getattr(logging, args.log_level.upper()))

    watch_dir = os.path.expanduser(args.watch)
    output_dir = os.path.expanduser(args.output)
    temp_dir = os.path.expanduser(args.temp) if args.temp else os.path.join(output_dir, ".tmp")

    if not os.path.isdir(watch_dir):
        logger.error(f"错误: 监听目录不存在: {watch_dir}")
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)

    handler = NcmHandler(output_dir, temp_dir)
    handler.start_retry_worker()

    # 先扫描已有文件
    handler.scan_existing(watch_dir)

    # 启动监听
    observer = Observer()
    observer.schedule(handler, watch_dir, recursive=True)
    observer.start()

    logger.info(f"[监听中] {watch_dir}")
    logger.info(f"[输出到] {output_dir}")
    logger.info("[按 Ctrl+C 退出]\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("\n[退出] 停止监听")
        observer.stop()

    # 带超时 join，避免 observer 线程异常卡死导致主进程永久挂起
    observer.join(timeout=_OBSERVER_JOIN_TIMEOUT)
    if observer.is_alive():
        logger.error(f"[错误] observer 线程在 {_OBSERVER_JOIN_TIMEOUT}s 后仍未退出，强制结束")

    handler.stop_retry_worker()


if __name__ == "__main__":
    main()
