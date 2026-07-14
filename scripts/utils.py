"""
公共工具函数
供 ncm_decrypt.py / organize.py / convert.py / watcher.py 共用。
"""

import hashlib


def sanitize_filename(name: str) -> str:
    """
    清理文件名/目录名中的非法字符。
    macOS/Linux/Windows 通用。
    """
    if not name:
        return ""
    illegal = '<>:"/\\|?*\n\r\t'
    for ch in illegal:
        name = name.replace(ch, "")
    return name.strip().strip(".")


def get_file_md5(filepath: str, chunk_size: int = 8192) -> str:
    """计算文件的 MD5 值"""
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()
