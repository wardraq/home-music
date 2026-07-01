#!/usr/bin/env python3
"""
NCM 文件解密引擎
将网易云音乐 .ncm 加密文件解密为原始 MP3/FLAC。

参考实现: https://github.com/QCloudHao/ncmdump

NCM 文件结构:
  1. Magic Header: "CTENFDAM" (8 bytes)
  2. Gap: 2 bytes
  3. Key Data: 4 bytes length + AES-128-ECB 加密的 RC4 密钥 (XOR 0x64)
  4. Meta Data: 4 bytes length + AES-128-ECB 加密的 JSON (XOR 0x63 + base64)
  5. CRC32: 4 bytes
  6. Gap: 5 bytes
  7. Album Image: 4 bytes size + image data
  8. Music Data: RC4 加密的音频数据

用法:
  python ncm_decrypt.py <input.ncm> [-o output_dir]
  python ncm_decrypt.py <input_dir> [-o output_dir]  # 批量
"""

import argparse
import base64
import binascii
import json
import os
import struct
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from Crypto.Cipher import AES
from mutagen.flac import FLAC
from mutagen.id3 import (
    ID3, TIT2, TPE1, TALB, TYER, TRCK, APIC, USLT, TCON, error as ID3Error
)
from mutagen.mp3 import MP3

# AES 密钥（16 bytes，网易云内置）
CORE_KEY = binascii.a2b_hex("687A4852416D736F356B496E62617857")
META_KEY = binascii.a2b_hex("2331346C6A6B5F215C5D2630553C2728")

# NCM 文件魔数
NCM_MAGIC = binascii.a2b_hex("4354454E4644414D")  # "CTENFDAM"


@dataclass
class NcmMeta:
    """NCM 文件解密后提取的元数据"""
    format: str = "mp3"
    music_name: str = "未知歌曲"
    artist: list = field(default_factory=list)
    album: str = "未知专辑"
    album_pic: str = ""
    track_number: str = ""
    year: str = ""
    lyrics: str = ""
    album_image: Optional[bytes] = None


def _unpad(data: bytes) -> bytes:
    """去除 PKCS7 padding"""
    if not data:
        return data
    pad_len = data[-1] if isinstance(data[-1], int) else ord(data[-1])
    if pad_len > 16:
        return data
    return data[:-pad_len]


def parse_ncm(file_path: str) -> tuple[bytes, NcmMeta]:
    """
    解析 NCM 文件，返回 (音频数据, 元数据)。
    """
    with open(file_path, "rb") as f:
        # 1. 验证 Magic Header
        header = f.read(8)
        if header != NCM_MAGIC:
            raise ValueError(f"不是有效的 NCM 文件: {file_path}")

        # 2. 跳过 2 字节 gap
        f.seek(2, 1)

        # 3. 读取并解密 RC4 密钥
        key_length = struct.unpack("<I", f.read(4))[0]
        key_data = f.read(key_length)
        # XOR 0x64
        key_data = bytes(b ^ 0x64 for b in key_data)
        # AES-128-ECB 解密
        cryptor = AES.new(CORE_KEY, AES.MODE_ECB)
        key_data = _unpad(cryptor.decrypt(key_data))
        # 去掉前 17 字节 ("neteasecloudmusic" 前缀)
        key_data = key_data[17:]
        key_length = len(key_data)

        # 4. 构建 RC4 S-box (标准 RC4 KSA)
        key_box = bytearray(range(256))
        c = 0
        last_byte = 0
        key_offset = 0
        for i in range(256):
            swap = key_box[i]
            c = (swap + last_byte + key_data[key_offset]) & 0xFF
            key_offset += 1
            if key_offset >= key_length:
                key_offset = 0
            key_box[i] = key_box[c]
            key_box[c] = swap
            last_byte = c

        # 5. 读取并解密 Meta Data
        meta_length = struct.unpack("<I", f.read(4))[0]
        meta = NcmMeta()

        if meta_length > 0:
            meta_data = f.read(meta_length)
            # XOR 0x63
            meta_data = bytes(b ^ 0x63 for b in meta_data)
            # 去掉 "163 key(Don't modify):" 前缀 (22 bytes)，然后 base64 解码
            meta_data = base64.b64decode(meta_data[22:])
            # AES-128-ECB 解密
            cryptor = AES.new(META_KEY, AES.MODE_ECB)
            meta_data = _unpad(cryptor.decrypt(meta_data)).decode("utf-8")
            # 去掉 "music:" 前缀 (6 bytes)
            meta_data = meta_data[6:]
            # 解析 JSON
            try:
                meta_json = json.loads(meta_data)
                meta = _parse_meta_json(meta_json)
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass

        # 6. 跳过 CRC32 (4 bytes)
        f.read(4)

        # 7. 跳过 5 字节 gap，读取专辑封面
        f.seek(5, 1)
        image_size = struct.unpack("<I", f.read(4))[0]
        if image_size > 0:
            meta.album_image = f.read(image_size)

        # 8. 解密音频数据 (RC4 流式解密)
        audio_chunks = []
        while True:
            chunk = bytearray(f.read(0x8000))
            chunk_length = len(chunk)
            if not chunk:
                break
            for i in range(1, chunk_length + 1):
                j = i & 0xFF
                chunk[i - 1] ^= key_box[(key_box[j] + key_box[(key_box[j] + j) & 0xFF]) & 0xFF]
            audio_chunks.append(bytes(chunk))

        audio_data = b"".join(audio_chunks)

    return audio_data, meta


def _parse_meta_json(meta_json: dict) -> NcmMeta:
    """从 NCM 内嵌 JSON 中解析元数据"""
    meta = NcmMeta()

    fmt = meta_json.get("format", "mp3")
    meta.format = fmt.lower() if fmt else "mp3"

    meta.music_name = meta_json.get("musicName", "未知歌曲")

    artists = meta_json.get("artist", [])
    if isinstance(artists, list):
        meta.artist = [[a.get("name", ""), a.get("id", 0)] for a in artists]
    elif isinstance(artists, str):
        meta.artist = [[artists, 0]]

    album_info = meta_json.get("album", {})
    if isinstance(album_info, dict):
        meta.album = album_info.get("name", "未知专辑")
    else:
        meta.album = str(album_info) if album_info else "未知专辑"

    meta.album_pic = meta_json.get("albumPic", "")
    meta.track_number = str(meta_json.get("trackNumber", ""))

    pub_time = meta_json.get("publishTime", 0)
    if pub_time:
        meta.year = str(pub_time)[:4]

    meta.lyrics = meta_json.get("lyrics", "") or meta_json.get("lyric", "")

    return meta


def _sanitize_filename(name: str) -> str:
    """清理文件名非法字符"""
    if not name:
        return ""
    illegal = '<>:"/\\|?*\n\r\t'
    for ch in illegal:
        name = name.replace(ch, "")
    return name.strip().strip(".")


def write_audio_file(audio_data: bytes, meta: NcmMeta, output_dir: str,
                     original_filename: str = "") -> str:
    """写入音频文件 + ID3 标签"""
    ext = "flac" if meta.format == "flac" else "mp3"
    safe_name = _sanitize_filename(meta.music_name)
    if not safe_name:
        safe_name = _sanitize_filename(Path(original_filename).stem) or "unknown"
    out_filename = f"{safe_name}.{ext}"
    out_path = os.path.join(output_dir, out_filename)

    counter = 1
    while os.path.exists(out_path):
        out_path = os.path.join(output_dir, f"{safe_name}_{counter}.{ext}")
        counter += 1

    os.makedirs(output_dir, exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(audio_data)

    _write_tags(out_path, meta, ext)
    return out_path


def _write_tags(file_path: str, meta: NcmMeta, ext: str):
    """写入标签"""
    artist_str = "、".join(a[0] for a in meta.artist) if meta.artist else "未知歌手"
    if ext == "flac":
        _write_flac_tags(file_path, meta, artist_str)
    else:
        _write_mp3_tags(file_path, meta, artist_str)


def _write_mp3_tags(file_path: str, meta: NcmMeta, artist_str: str):
    try:
        audio = MP3(file_path)
        if audio.tags is None:
            audio.add_tags()
        tags = audio.tags
        tags.add(TIT2(encoding=3, text=meta.music_name))
        tags.add(TPE1(encoding=3, text=artist_str))
        tags.add(TALB(encoding=3, text=meta.album))
        if meta.year:
            tags.add(TYER(encoding=3, text=meta.year))
        if meta.track_number:
            tags.add(TRCK(encoding=3, text=meta.track_number))
        if meta.album_image:
            mime = "image/jpeg" if meta.album_image[:3] == b"\xff\xd8\xff" else "image/png"
            tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=meta.album_image))
        if meta.lyrics:
            tags.add(USLT(encoding=3, lang="chi", desc="", text=meta.lyrics))
        audio.save()
    except Exception as e:
        print(f"  [警告] 写入 MP3 标签失败: {e}", file=sys.stderr)


def _write_flac_tags(file_path: str, meta: NcmMeta, artist_str: str):
    try:
        audio = FLAC(file_path)
        audio["title"] = meta.music_name
        audio["artist"] = artist_str
        audio["album"] = meta.album
        if meta.year:
            audio["date"] = meta.year
        if meta.track_number:
            audio["tracknumber"] = meta.track_number
        if meta.album_image:
            from mutagen.flac import Picture
            pic = Picture()
            pic.type = 3
            pic.mime = "image/jpeg" if meta.album_image[:3] == b"\xff\xd8\xff" else "image/png"
            pic.desc = "Cover"
            pic.data = meta.album_image
            audio.add_picture(pic)
        if meta.lyrics:
            audio["lyrics"] = meta.lyrics
        audio.save()
    except Exception as e:
        print(f"  [警告] 写入 FLAC 标签失败: {e}", file=sys.stderr)


def decrypt_file(input_path: str, output_dir: str = ".") -> Optional[str]:
    """解密单个 NCM 文件"""
    if not os.path.isfile(input_path):
        print(f"  [错误] 文件不存在: {input_path}", file=sys.stderr)
        return None
    if not input_path.lower().endswith(".ncm"):
        print(f"  [跳过] 不是 .ncm 文件: {input_path}", file=sys.stderr)
        return None

    try:
        audio_data, meta = parse_ncm(input_path)
        out_path = write_audio_file(audio_data, meta, output_dir,
                                    original_filename=os.path.basename(input_path))
        artist_str = "、".join(a[0] for a in meta.artist) if meta.artist else "未知歌手"
        print(f"  [成功] {os.path.basename(input_path)} → {os.path.basename(out_path)}")
        print(f"         歌手: {artist_str}  专辑: {meta.album}  格式: {meta.format}")
        return out_path
    except ValueError as e:
        print(f"  [错误] {e}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  [错误] 解密失败 {input_path}: {e}", file=sys.stderr)
        return None


def decrypt_directory(input_dir: str, output_dir: str = ".") -> dict:
    """批量解密目录下所有 .ncm 文件"""
    ncm_files = sorted(set(Path(input_dir).rglob("*.ncm")))
    if not ncm_files:
        print(f"  [提示] 目录中没有 .ncm 文件: {input_dir}")
        return {"success": 0, "failed": 0, "files": []}

    print(f"\n找到 {len(ncm_files)} 个 .ncm 文件，开始解密...\n")

    success = 0
    failed = 0
    out_files = []

    for i, ncm_file in enumerate(ncm_files, 1):
        print(f"[{i}/{len(ncm_files)}] {ncm_file.name}")
        result = decrypt_file(str(ncm_file), output_dir)
        if result:
            success += 1
            out_files.append(result)
        else:
            failed += 1

    print(f"\n解密完成: 成功 {success} 首, 失败 {failed} 首")
    return {"success": success, "failed": failed, "files": out_files}


def main():
    parser = argparse.ArgumentParser(
        description="NCM 文件解密工具 - 将网易云音乐 .ncm 解密为 MP3/FLAC"
    )
    parser.add_argument("input", help="输入文件或目录路径")
    parser.add_argument("-o", "--output", default=".",
                        help="输出目录 (默认当前目录)")
    args = parser.parse_args()

    input_path = os.path.expanduser(args.input)
    output_dir = os.path.expanduser(args.output)
    os.makedirs(output_dir, exist_ok=True)

    if os.path.isdir(input_path):
        result = decrypt_directory(input_path, output_dir)
        sys.exit(0 if result["failed"] == 0 else 1)
    elif os.path.isfile(input_path):
        result = decrypt_file(input_path, output_dir)
        sys.exit(0 if result else 1)
    else:
        print(f"错误: 路径不存在: {input_path}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
