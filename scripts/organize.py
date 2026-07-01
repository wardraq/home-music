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
from pathlib import Path

from mutagen import File as MutagenFile
from mutagen.flac import FLAC
from mutagen.mp3 import MP3


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
                for key in audio.tags:
                    key_lower = key.lower()
                    if key_lower == "tit2" or key_lower == "title":
                        tags_info["title"] = str(audio.tags[key])
                    elif key_lower == "tpe1" or key_lower == "artist":
                        tags_info["artist"] = str(audio.tags[key])
                    elif key_lower == "talb" or key_lower == "album":
                        tags_info["album"] = str(audio.tags[key])
                    elif key_lower == "trck" or key_lower == "tracknumber":
                        tags_info["tracknumber"] = str(audio.tags[key])
                    elif key_lower == "tyer" or key_lower == "date":
                        tags_info["date"] = str(audio.tags[key])

        elif ext in (".wav", ".ape", ".ogg", ".opus"):
            audio = MutagenFile(file_path)
            if audio and audio.tags:
                tags_info["title"] = str(audio.tags.get("title", [""])[0]) if "title" in audio.tags else ""
                tags_info["artist"] = str(audio.tags.get("artist", [""])[0]) if "artist" in audio.tags else ""
                tags_info["album"] = str(audio.tags.get("album", [""])[0]) if "album" in audio.tags else ""

    except Exception as e:
        print(f"  [警告] 读取标签失败 {file_path}: {e}", file=sys.stderr)

    return tags_info


def sanitize_name(name: str) -> str:
    """清理目录/文件名中的非法字符"""
    if not name:
        return ""
    illegal = '<>:"/\\|?*\n\r\t'
    for ch in illegal:
        name = name.replace(ch, "")
    return name.strip().strip(".")


def build_output_path(tags: dict, output_dir: str, original_name: str) -> str:
    """构建目标文件路径: output_dir/歌手/专辑/歌曲名.格式"""
    artist = sanitize_name(tags.get("artist", "")) or "未知歌手"
    album = sanitize_name(tags.get("album", "")) or "未知专辑"
    title = sanitize_name(tags.get("title", ""))

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


def organize_file(file_path: str, output_dir: str, move: bool = False) -> str:
    """
    整理单个文件到音乐库目录结构。

    Args:
        file_path: 输入文件路径
        output_dir: 音乐库根目录
        move: True=移动文件, False=复制文件

    Returns:
        目标文件路径
    """
    ext = Path(file_path).suffix.lower()
    if ext not in (".mp3", ".flac", ".m4a", ".wav", ".ape", ".ogg", ".opus"):
        print(f"  [跳过] 不支持的格式: {file_path}")
        return ""

    tags = read_tags(file_path)
    dest_path = build_output_path(tags, output_dir, os.path.basename(file_path))

    # 避免覆盖
    if os.path.exists(dest_path):
        stem = Path(dest_path).stem
        ext = Path(dest_path).suffix
        parent = os.path.dirname(dest_path)
        counter = 1
        while os.path.exists(dest_path):
            dest_path = os.path.join(parent, f"{stem}_{counter}{ext}")
            counter += 1

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)

    if move:
        shutil.move(file_path, dest_path)
    else:
        shutil.copy2(file_path, dest_path)

    artist = tags.get("artist", "未知歌手")
    album = tags.get("album", "未知专辑")
    title = tags.get("title", Path(file_path).stem)
    print(f"  [整理] {title} → {artist}/{album}/")
    return dest_path


def organize_directory(input_dir: str, output_dir: str, move: bool = False) -> dict:
    """
    批量整理目录下所有音频文件。

    Returns:
        {"success": int, "failed": int, "files": [paths]}
    """
    supported_exts = {".mp3", ".flac", ".m4a", ".wav", ".ape", ".ogg", ".opus"}
    audio_files = [f for f in Path(input_dir).rglob("*")
                   if f.is_file() and f.suffix.lower() in supported_exts]

    if not audio_files:
        print(f"  [提示] 目录中没有找到音频文件: {input_dir}")
        return {"success": 0, "failed": 0, "files": []}

    print(f"\n找到 {len(audio_files)} 个音频文件，开始整理...\n")

    success = 0
    failed = 0
    out_files = []

    for i, f in enumerate(sorted(audio_files), 1):
        print(f"[{i}/{len(audio_files)}] {f.name}")
        try:
            result = organize_file(str(f), output_dir, move=move)
            if result:
                success += 1
                out_files.append(result)
            else:
                failed += 1
        except Exception as e:
            print(f"  [错误] {e}", file=sys.stderr)
            failed += 1

    print(f"\n整理完成: 成功 {success} 首, 失败 {failed} 首")
    return {"success": success, "failed": failed, "files": out_files}


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
        sys.exit(0 if result["failed"] == 0 else 1)
    elif os.path.isfile(input_path):
        result = organize_file(input_path, output_dir, move=args.move)
        sys.exit(0 if result else 1)
    else:
        print(f"错误: 路径不存在: {input_path}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
