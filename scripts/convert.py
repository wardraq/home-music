#!/usr/bin/env python3
"""
音频格式转换脚本
支持 FLAC/MP3/WAV/ALAC 等格式互转，配合 convert.yaml 配置使用。

依赖: afconvert (macOS 自带), mutagen

用法:
  python scripts/convert.py <input_file_or_dir> [--format alac|mp3_320|flac] [--config config/convert.yaml]
  python scripts/convert.py music_library/ --all    # 批量转换整个目录
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml
from mutagen import File as MutagenFile
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4


SUPPORTED_INPUT_EXTS = {".flac", ".mp3", ".m4a", ".wav", ".aiff", ".aif"}
SUPPORTED_OUTPUT_FORMATS = {"alac", "mp3_320", "mp3_192", "mp3_v0", "flac", "wav"}


def load_config(config_path: str = "config/convert.yaml") -> dict:
    """加载转换配置"""
    config_path = os.path.expanduser(config_path)
    if not os.path.exists(config_path):
        print(f"[警告] 配置文件不存在: {config_path}，使用默认配置", file=sys.stderr)
        return _default_config()
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _default_config() -> dict:
    return {
        "default_format": "alac",
        "target_platform": "apple_music",
        "formats": {
            "alac": {"output_ext": ".m4a", "codec": "alac", "container": "m4af"},
            "mp3_320": {"output_ext": ".mp3", "bitrate": 320},
        },
        "file_handling": {
            "delete_original": False,
            "overwrite": False,
            "output_dir": "music_library",
            "auto_organize": True,
        },
        "tags": {
            "copy_tags": True,
            "ignore_tag_errors": True,
        },
    }


def get_output_path(input_path: str, output_format: str, config: dict) -> str:
    """根据配置和目标格式决定输出路径

    默认输出到源文件同目录，文件名相同仅扩展名不同。
    可通过 config 的 output_dir 指定统一输出目录。
    """
    ext_map = {
        "alac": ".m4a",
        "mp3_320": ".mp3",
        "mp3_192": ".mp3",
        "mp3_v0": ".mp3",
        "flac": ".flac",
        "wav": ".wav",
    }
    output_ext = ext_map.get(output_format, ".m4a")
    output_dir = config.get("file_handling", {}).get("output_dir", "")
    if output_dir:
        # 统一输出到指定目录，保持原文件名
        output_dir = os.path.expanduser(output_dir)
        return os.path.join(output_dir, f"{Path(input_path).stem}{output_ext}")
    else:
        # 输出到源文件同目录
        return str(Path(input_path).with_suffix(output_ext))


def copy_tags(src_path: str, dst_path: str, config: dict) -> bool:
    """将源文件标签复制到目标文件"""
    if not config.get("tags", {}).get("copy_tags", True):
        return True

    try:
        from mutagen.flac import FLAC as FLACCls
        from mutagen.mp3 import MP3 as MP3Cls
        from mutagen.mp4 import MP4

        # 读取源文件标签
        src = MutagenFile(src_path)
        if src is None:
            return True  # 无标签，跳过

        src_tags = {}
        if isinstance(src, FLACCls):
            for k, v in (src.tags or {}).items():
                src_tags[k] = v[0] if isinstance(v, list) and len(v) > 0 else str(v)
        elif isinstance(src, MP3Cls) and src.tags:
            for frame in src.tags.values():
                if frame.FrameID == "TIT2":
                    src_tags["title"] = str(frame)
                elif frame.FrameID == "TPE1":
                    src_tags["artist"] = str(frame)
                elif frame.FrameID == "TALB":
                    src_tags["album"] = str(frame)
                elif frame.FrameID == "TDRC":
                    src_tags["date"] = str(frame)
                elif frame.FrameID == "TRCK":
                    src_tags["tracknumber"] = str(frame)
        else:
            # 其他格式，尝试直接读 common 标签
            for k in ("title", "artist", "album", "date", "tracknumber"):
                v = src.get(k)
                if v:
                    src_tags[k] = str(v[0]) if isinstance(v, list) else str(v)

        if not src_tags:
            return True

        dst_ext = Path(dst_path).suffix.lower()

        if dst_ext == ".m4a":
            dst = MP4(dst_path)
            if dst.tags is None:
                dst.add_tags()
            mp4_map = {
                "title": "\xa9nam",
                "artist": "\xa9ART",
                "album": "\xa9alb",
                "date": "\xa9day",
                "tracknumber": "trkn",
                "genre": "\xa9gen",
            }
            for src_key, dst_key in mp4_map.items():
                if src_key in src_tags:
                    val = src_tags[src_key]
                    if dst_key == "trkn":
                        parts = str(val).split("/")
                        track_num = int(parts[0]) if parts[0].isdigit() else 0
                        total = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
                        dst.tags[dst_key] = [(track_num, total)]
                    else:
                        dst.tags[dst_key] = str(val)
            dst.save()

        elif dst_ext == ".mp3":
            from mutagen.id3 import ID3, TIT2, TPE1, TALB, TDRC, TRCK
            dst = MP3(dst_path)
            if dst.tags is None:
                dst.add_tags()
            id3_map = {
                "title": ("TIT2", TIT2),
                "artist": ("TPE1", TPE1),
                "album": ("TALB", TALB),
                "date": ("TDRC", TDRC),
                "tracknumber": ("TRCK", TRCK),
            }
            for src_key, (frame_id, frame_cls) in id3_map.items():
                if src_key in src_tags:
                    dst.tags.add(frame_cls(encoding=3, text=src_tags[src_key]))
            dst.save()

        elif dst_ext == ".flac":
            from mutagen.flac import FLAC as FLACOut
            dst = FLACOut(dst_path)
            flac_map = {"title": "title", "artist": "artist", "album": "album", "date": "date", "tracknumber": "tracknumber"}
            for src_key, dst_key in flac_map.items():
                if src_key in src_tags:
                    dst.tags[dst_key] = src_tags[src_key]
            dst.save()

        return True
    except Exception as e:
        ignore = config.get("tags", {}).get("ignore_tag_errors", True)
        print(f"  [警告] 复制标签失败: {e}" + (" (已忽略)" if ignore else ""), file=sys.stderr)
        return ignore


def convert_to_alac(input_path: str, output_path: str) -> bool:
    """使用 afconvert 转换为 ALAC"""
    try:
        cmd = ["afconvert", "-f", "m4af", "-d", "alac", input_path, output_path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            print(f"  [错误] afconvert 失败: {result.stderr}", file=sys.stderr)
            return False
        return True
    except subprocess.TimeoutExpired:
        print(f"  [错误] afconvert 超时", file=sys.stderr)
        return False
    except Exception as e:
        print(f"  [错误] 转换失败: {e}", file=sys.stderr)
        return False


def convert_to_mp3(input_path: str, output_path: str, bitrate: int = 320) -> bool:
    """使用 ffmpeg 或 lame 转换为 MP3（需要安装 ffmpeg）"""
    # 优先用 ffmpeg，没有就用 afconvert（但 afconvert 不直接支持 MP3 编码）
    try:
        # 检查 ffmpeg
        result = subprocess.run(["which", "ffmpeg"], capture_output=True)
        if result.returncode == 0:
            cmd = [
                "ffmpeg", "-i", input_path,
                "-ab", f"{bitrate}k",
                "-map_metadata", "0",
                "-y" if True else "-n",
                output_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                print(f"  [错误] ffmpeg 失败: {result.stderr[-200:]}", file=sys.stderr)
                return False
            return True
        else:
            print(f"  [错误] 需要 ffmpeg 来转换 MP3，请先安装: brew install ffmpeg", file=sys.stderr)
            return False
    except Exception as e:
        print(f"  [错误] 转换失败: {e}", file=sys.stderr)
        return False


def convert_to_flac(input_path: str, output_path: str) -> bool:
    """使用 afconvert 转换为 FLAC"""
    try:
        cmd = ["afconvert", "-f", "flac", "-d", "flac", input_path, output_path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            print(f"  [错误] afconvert 失败: {result.stderr}", file=sys.stderr)
            return False
        return True
    except Exception as e:
        print(f"  [错误] 转换失败: {e}", file=sys.stderr)
        return False


def convert_file(input_path: str, output_format: str, config: dict) -> str:
    """
    转换单个音频文件。

    Args:
        input_path: 输入文件路径
        output_format: 目标格式 (alac, mp3_320, flac, ...)
        config: 配置字典

    Returns:
        输出文件路径，失败返回空字符串
    """
    input_ext = Path(input_path).suffix.lower()
    if input_ext not in SUPPORTED_INPUT_EXTS:
        print(f"  [跳过] 不支持的输入格式: {input_ext}", file=sys.stderr)
        return ""

    output_path = get_output_path(input_path, output_format, config)

    # 检查输出文件是否已存在
    if os.path.exists(output_path) and not config.get("file_handling", {}).get("overwrite", False):
        print(f"  [跳过] 输出文件已存在: {output_path}", file=sys.stderr)
        return output_path

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    print(f"  [转换] {os.path.basename(input_path)} → {output_format} ...", end=" ", flush=True)

    success = False
    if output_format == "alac":
        success = convert_to_alac(input_path, output_path)
    elif output_format.startswith("mp3_"):
        bitrate = int(output_format.split("_")[1])
        success = convert_to_mp3(input_path, output_path, bitrate)
    elif output_format == "flac":
        success = convert_to_flac(input_path, output_path)
    else:
        print(f"  [错误] 不支持的输出格式: {output_format}", file=sys.stderr)
        return ""

    if not success:
        return ""

    print("完成", flush=True)

    # 复制标签
    copy_tags(input_path, output_path, config)

    return output_path


def convert_directory(input_dir: str, output_format: str, config: dict) -> dict:
    """批量转换目录下的所有音频文件"""
    audio_files = [
        str(f) for f in Path(input_dir).rglob("*")
        if f.is_file() and f.suffix.lower() in SUPPORTED_INPUT_EXTS
    ]

    if not audio_files:
        print(f"  [提示] 目录中没有找到音频文件: {input_dir}")
        return {"success": 0, "failed": 0, "files": []}

    print(f"\n找到 {len(audio_files)} 个音频文件，开始转换...\n")

    success = 0
    failed = 0
    out_files = []

    for i, f in enumerate(sorted(audio_files), 1):
        print(f"[{i}/{len(audio_files)}] {Path(f).name}")
        try:
            result = convert_file(f, output_format, config)
            if result:
                success += 1
                out_files.append(result)
            else:
                failed += 1
        except Exception as e:
            print(f"  [错误] {e}", file=sys.stderr)
            failed += 1

    print(f"\n转换完成: 成功 {success} 首, 失败 {failed} 首")
    return {"success": success, "failed": failed, "files": out_files}


def main():
    parser = argparse.ArgumentParser(
        description="音频格式转换工具 - 支持 FLAC/MP3/ALAC 等格式互转"
    )
    parser.add_argument("input", help="输入文件或目录路径")
    parser.add_argument("--format", choices=list(SUPPORTED_OUTPUT_FORMATS),
                        help="输出格式 (默认从配置文件读取)")
    parser.add_argument("--config", default="config/convert.yaml",
                        help="转换配置文件路径 (默认 config/convert.yaml)")
    parser.add_argument("--all", action="store_true",
                        help="转换目录下所有支持的音频文件")
    args = parser.parse_args()

    config = load_config(args.config)

    # 确定输出格式
    output_format = args.format or config.get("default_format", "alac")
    if output_format not in SUPPORTED_OUTPUT_FORMATS:
        print(f"[错误] 不支持的格式: {output_format}", file=sys.stderr)
        sys.exit(1)

    input_path = os.path.expanduser(args.input)

    if not os.path.exists(input_path):
        print(f"[错误] 路径不存在: {input_path}", file=sys.stderr)
        sys.exit(1)

    if os.path.isdir(input_path):
        result = convert_directory(input_path, output_format, config)
        sys.exit(0 if result["failed"] == 0 else 1)
    elif os.path.isfile(input_path):
        result = convert_file(input_path, output_format, config)
        sys.exit(0 if result else 1)
    else:
        print(f"[错误] 无效路径: {input_path}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
