#!/usr/bin/env python3
"""
音乐文件自动整理脚本
将解密后的音频文件按 歌手/专辑/ 歌曲名.格式 分类整理到音乐库。

用法:
  python organize.py <input_file_or_dir> [-o music_library/]
"""

import argparse
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from mutagen import File as MutagenFile
from mutagen.flac import FLAC
from utils import sanitize_filename


@dataclass
class OrganizeResult:
    """整理结果：区分成功、失败、不支持的格式、错误"""
    status: str  # "success" | "failed" | "unsupported" | "error"
    path: str = ""
    message: str = ""

    def __bool__(self) -> bool:
        return self.status == "success"


# 支持的音频格式
SUPPORTED_EXTS = {".mp3", ".flac", ".m4a", ".wav", ".ape", ".ogg", ".opus"}

def read_tags(file_path: str) -> dict:
    """读取音频文件的标签信息"""
    tags_info = {
        "title": "",
        "artist": "",
        "album": "",
        "tracknumber": "",
        "date": "",
        "format": "",
    }

    ext = Path(file_path).suffix.lower()
    tags_info["format"] = ext.lstrip(".")

    try:
        if ext == ".flac":
            audio = FLAC(file_path)
            tags_info["title"] = audio.get("title", [""])[0]
            tags_info["artist"] = audio.get("artist", [""])[0]
            tags_info["album"] = audio.get("album", [""])[0]
            tags_info["tracknumber"] = audio.get("tracknumber", [""])[0]
            tags_info["date"] = audio.get("date", [""])[0]

        elif ext in (".mp3", ".m4a"):
            audio = MutagenFile(file_path)
            if audio and audio.tags:
                # 处理 MP4/M4A 标签 (\xa9nam, \xa9ART, \xa9alb 等)
                mp4_tag_map = {
                    "\xa9nam": "title",
                    "\xa9ART": "artist",
                    "\xa9alb": "album",
                    "\xa9day": "date",
                    "trkn": "tracknumber",
                }
                for key in audio.tags:
                    key_lower = key.lower()
                    target = mp4_tag_map.get(key)
                    if not target:
                        if key_lower == "tit2" or key_lower == "title":
                            target = "title"
                        elif key_lower == "tpe1" or key_lower == "artist":
                            target = "artist"
                        elif key_lower == "talb" or key_lower == "album":
                            target = "album"
                        elif key_lower == "trck" or key_lower == "tracknumber":
                            target = "tracknumber"
                        elif key_lower == "tyer" or key_lower == "date":
                            target = "date"
                    if target:
                        val = audio.tags[key]
                        # MP4 标签值通常是列表，取第一个元素
                        if isinstance(val, (list, tuple)) and len(val) > 0:
                            val = val[0]
                            # trkn 可能是 (track, total) 元组
                            if target == "tracknumber" and isinstance(val, tuple):
                                val = val[0]
                        tags_info[target] = str(val)

        elif ext in (".wav", ".ape", ".ogg", ".opus"):
            audio = MutagenFile(file_path)
            if audio and audio.tags:
                tags_info["title"] = str(audio.tags.get("title", [""])[0]) if "title" in audio.tags else ""
                tags_info["artist"] = str(audio.tags.get("artist", [""])[0]) if "artist" in audio.tags else ""
                tags_info["album"] = str(audio.tags.get("album", [""])[0]) if "album" in audio.tags else ""
                tags_info["tracknumber"] = str(audio.tags.get("tracknumber", [""])[0]) if "tracknumber" in audio.tags else ""
                tags_info["date"] = str(audio.tags.get("date", [""])[0]) if "date" in audio.tags else ""

    except Exception as e:
        print(f"  [警告] 读取标签失败 {file_path}: {e}", file=sys.stderr)

    return tags_info



def build_output_path(tags: dict, output_dir: str, original_name: str) -> str:
    """构建目标文件路径: output_dir/歌手/专辑/歌曲名.格式"""
    artist = sanitize_filename(tags.get("artist", "")) or "未知歌手"
    album = sanitize_filename(tags.get("album", "")) or "未知专辑"
    title = sanitize_filename(tags.get("title", ""))

    # Track number 前缀
    track = tags.get("tracknumber", "")
    if track:
        # 处理 "3/12" 格式，只取前面的数字
        track_num = track.split("/")[0].strip()
        if track_num.isdigit():
            title = f"{int(track_num):02d} - {title}" if title else f"{int(track_num):02d}"

    if not title:
        # 没有标题就用原文件名
        title = Path(original_name).stem

    ext = tags.get("format", "mp3")
    filename = f"{title}.{ext}"
    return os.path.join(output_dir, artist, album, filename)


def organize_file(file_path: str, output_dir: str, move: bool = False) -> OrganizeResult:
    """
    整理单个文件到音乐库目录结构。

    Args:
        file_path: 输入文件路径
        output_dir: 音乐库根目录
        move: True=移动文件, False=复制文件

    Returns:
        OrganizeResult: 包含状态、目标路径、可选消息
    """
    ext = Path(file_path).suffix.lower()
    if ext not in SUPPORTED_EXTS:
        print(f"  [跳过] 不支持的格式: {file_path}")
        return OrganizeResult(status="unsupported", message=f"不支持的格式: {ext}")

    try:
        tags = read_tags(file_path)
    except Exception as e:
        print(f"  [错误] 读取标签失败: {file_path}: {e}", file=sys.stderr)
        return OrganizeResult(status="error", message=str(e))

    dest_path = build_output_path(tags, output_dir, os.path.basename(file_path))

    # 避免覆盖
    MAX_RETRY = 100
    if os.path.exists(dest_path):
        stem = Path(dest_path).stem
        ext = Path(dest_path).suffix
        parent = os.path.dirname(dest_path)
        counter = 1
        while os.path.exists(dest_path) and counter <= MAX_RETRY:
            dest_path = os.path.join(parent, f"{stem}_{counter}{ext}")
            counter += 1
        if counter > MAX_RETRY:
            return OrganizeResult(status="error", message=f"重名冲突超过 {MAX_RETRY} 次: {dest_path}")

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)

    try:
        if move:
            _safe_move(file_path, dest_path)
        else:
            shutil.copy2(file_path, dest_path)
    except Exception as e:
        return OrganizeResult(status="error", message=f"移动/复制失败: {e}")

    artist = tags.get("artist", "未知歌手")
    album = tags.get("album", "未知专辑")
    title = tags.get("title", Path(file_path).stem)
    print(f"  [整理] {title} → {artist}/{album}/")
    return OrganizeResult(status="success", path=dest_path)


def _safe_move(src: str, dst: str) -> None:
    """
    安全移动文件，兼容跨文件系统。
    shutil.move 跨文件系统时可能失败，这里显式 copy2 + 删除原文件。
    """
    shutil.copy2(src, dst)
    os.remove(src)


def organize_directory(input_dir: str, output_dir: str, move: bool = False) -> dict:
    """
    批量整理目录下所有音频文件。

    Returns:
        {"success": int, "failed": int, "unsupported": int, "error": int, "files": [paths]}
    """
    audio_files = [f for f in Path(input_dir).rglob("*")
                   if f.is_file() and f.suffix.lower() in SUPPORTED_EXTS]

    if not audio_files:
        print(f"  [提示] 目录中没有找到音频文件: {input_dir}")
        return {"success": 0, "failed": 0, "unsupported": 0, "error": 0, "files": []}

    print(f"\n找到 {len(audio_files)} 个音频文件，开始整理...\n")

    success = 0
    failed = 0
    unsupported = 0
    error = 0
    out_files = []

    for i, f in enumerate(sorted(audio_files), 1):
        print(f"[{i}/{len(audio_files)}] {f.name}")
        result = organize_file(str(f), output_dir, move=move)
        if result.status == "success":
            success += 1
            out_files.append(result.path)
        elif result.status == "unsupported":
            unsupported += 1
        elif result.status == "error":
            error += 1
        else:
            failed += 1

    print(f"\n整理完成: 成功 {success} 首, 失败 {failed} 首, 不支持 {unsupported} 首, 错误 {error} 首")
    return {"success": success, "failed": failed, "unsupported": unsupported, "error": error, "files": out_files}


def main():
    parser = argparse.ArgumentParser(
        description="音乐文件整理工具 - 按歌手/专辑分类整理音频文件"
    )
    parser.add_argument("input", help="输入文件或目录路径")
    parser.add_argument("-o", "--output", default="music_library",
                        help="输出音乐库目录 (默认 music_library)")
    parser.add_argument("--move", action="store_true",
                        help="移动文件而非复制 (默认复制)")
    args = parser.parse_args()

    input_path = os.path.expanduser(args.input)
    output_dir = os.path.expanduser(args.output)

    os.makedirs(output_dir, exist_ok=True)

    if os.path.isdir(input_path):
        result = organize_directory(input_path, output_dir, move=args.move)
        sys.exit(0 if result["error"] == 0 else 1)
    elif os.path.isfile(input_path):
        result = organize_file(input_path, output_dir, move=args.move)
        sys.exit(0 if result.status == "success" else 1)
    else:
        print(f"错误: 路径不存在: {input_path}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
