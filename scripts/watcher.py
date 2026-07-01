#!/usr/bin/env python3
"""
目录监听脚本
监听网易云下载目录，检测到新的 .ncm 文件后自动解密 + 整理到音乐库。

用法:
  python watcher.py --watch ~/Music/CloudMusic/ --output music_library/
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# 导入同目录下的解密和整理模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ncm_decrypt import decrypt_file
from organize import organize_file


# 已处理文件记录
PROCESSED_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "config", "processed.json")


def load_processed() -> dict:
    """加载已处理文件记录"""
    if os.path.exists(PROCESSED_FILE):
        try:
            with open(PROCESSED_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_processed(processed: dict):
    """保存已处理文件记录"""
    os.makedirs(os.path.dirname(PROCESSED_FILE), exist_ok=True)
    with open(PROCESSED_FILE, "w", encoding="utf-8") as f:
        json.dump(processed, f, ensure_ascii=False, indent=2)


def get_file_md5(file_path: str) -> str:
    """计算文件 MD5"""
    import hashlib
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

    def _process_file(self, file_path: str):
        """处理单个 .ncm 文件：解密 → 整理"""
        # 等文件写入完成（网易云下载可能还在写）
        if not self._wait_for_file_ready(file_path):
            return

        # 检查是否已处理
        try:
            file_md5 = get_file_md5(file_path)
        except IOError:
            return

        if file_md5 in self.processed:
            print(f"  [跳过] 已处理过: {os.path.basename(file_path)}")
            return

        filename = os.path.basename(file_path)
        print(f"\n[发现新文件] {filename}")

        # 解密到临时目录
        print(f"  [解密中]...")
        decrypted = decrypt_file(file_path, self.temp_dir)
        if not decrypted:
            print(f"  [失败] 解密失败: {filename}")
            return

        # 整理到音乐库
        print(f"  [整理中]...")
        organized = organize_file(decrypted, self.output_dir, move=True)
        if organized:
            # 清理临时文件
            if os.path.exists(decrypted):
                os.remove(decrypted)
            # 记录已处理
            self.processed[file_md5] = {
                "filename": filename,
                "output": organized,
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            save_processed(self.processed)
            print(f"  [完成] → {organized}")
        else:
            print(f"  [警告] 整理失败，文件留在: {decrypted}")

    def _wait_for_file_ready(self, file_path: str, timeout: float = 30.0) -> bool:
        """等待文件写入完成"""
        start = time.time()
        last_size = -1
        while time.time() - start < timeout:
            if not os.path.exists(file_path):
                time.sleep(0.5)
                continue
            current_size = os.path.getsize(file_path)
            if current_size > 0 and current_size == last_size:
                return True  # 大小稳定，认为写入完成
            last_size = current_size
            time.sleep(1.0)
        print(f"  [超时] 等待文件写入超时: {file_path}")
        return False

    def scan_existing(self, watch_dir: str):
        """启动时扫描已有但未处理的 .ncm 文件"""
        ncm_files = list(Path(watch_dir).rglob("*.ncm"))
        if not ncm_files:
            return
        print(f"\n[扫描] 发现 {len(ncm_files)} 个 .ncm 文件，检查是否有未处理的...")
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
    args = parser.parse_args()

    watch_dir = os.path.expanduser(args.watch)
    output_dir = os.path.expanduser(args.output)
    temp_dir = os.path.expanduser(args.temp) if args.temp else os.path.join(output_dir, ".tmp")

    if not os.path.isdir(watch_dir):
        print(f"错误: 监听目录不存在: {watch_dir}", file=sys.stderr)
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)

    handler = NcmHandler(output_dir, temp_dir)

    # 先扫描已有文件
    handler.scan_existing(watch_dir)

    # 启动监听
    observer = Observer()
    observer.schedule(handler, watch_dir, recursive=True)
    observer.start()

    print(f"\n[监听中] {watch_dir}")
    print(f"[输出到] {output_dir}")
    print(f"[按 Ctrl+C 退出]\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[退出] 停止监听")
        observer.stop()

    observer.join()


if __name__ == "__main__":
    main()
