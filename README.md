# Home Music

网易云音乐本地解密 + 自动整理分类 + 手机同步，打造个人音乐库。

## 功能

- **解密**：自动将网易云下载的 `.ncm` 文件解密为 MP3/FLAC
- **整理**：自动补全 ID3 标签、嵌入封面、按歌手/专辑分类
- **同步**：通过 WiFi 将音乐同步到手机，离线播放

## 快速开始

```bash
# 1. 安装依赖
pip install -r scripts/requirements.txt

# 2. 解密单个文件
python scripts/ncm_decrypt.py /path/to/song.ncm

# 3. 批量解密 + 整理
python scripts/ncm_decrypt.py /path/to/ncm_files/ --output music_library/

# 4. 自动监听网易云下载目录（新文件自动解密）
python scripts/watcher.py --watch ~/Music/CloudMusic/ --output music_library/
```

## 项目结构

```
home-music/
├── scripts/          # 核心脚本
│   ├── ncm_decrypt.py    # .ncm 解密
│   ├── organize.py       # 元数据整理 + 分类
│   ├── watcher.py        # 目录监听自动解密
│   └── requirements.txt  # Python 依赖
├── docs/             # 文档
├── music_library/    # 解密整理后的音乐库（gitignore）
├── config/           # 配置文件
└── README.md
```

## 技术栈

- Python 3.12+
- [ncmdump](https://github.com/nanomsg/nanomsg) - NCM 解密
- [mutagen](https://github.com/quodlibet/mutagen) - ID3 标签处理
- [beets](https://beets.io/) - 音乐库管理（可选，进阶整理）

## License

MIT
