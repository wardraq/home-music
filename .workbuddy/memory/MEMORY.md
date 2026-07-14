# Home Music 项目记忆

## NCM 解密 bug 修复 (2025-07-02)

`ncm_decrypt.py` 有两个 bug，已修复：

1. **artist 字段格式不兼容**：NCM 元数据中 `artist` 可能是 `[["name", "id"], ...]`（列表的列表）而非 `[{"name":..., "id":...}, ...]`（字典列表）。原代码直接调 `a.get("name")` 导致 `'list' object has no attribute 'get'`。修复后兼容 dict、list、str 三种格式。

2. **文件偏移量错误**：原代码把 CRC32(4B)+gap(1B)=5B 拆成 `f.read(4)` + `f.seek(5,1)` = 9B，多读 4 字节。且漏读 `image_space` 字段，导致不跳过封面填充区，音频数据从错误位置开始读取。修正为 `f.seek(5,1)` 跳过 CRC32+gap，然后依次读 image_space、image_size，读完后 seek `image_space - image_size` 跳过填充。

## 测试文件

- 测试曲：`Alex Warren - Save You a Seat.ncm`（7.9MB, 320kbps MP3, 197.6s）
- 解密输出正常，ID3 标签（TIT2/TPE1/TALB）正确写入
- organize.py 按 歌手/专辑/ 曲名 正确分类

## Apple Music 兼容：FLAC → ALAC (2025-07-02)

Apple Music 不支持 FLAC，需转码为 ALAC（.m4a）。

- 用 macOS 自带 `afconvert -f m4af -d alac` 转码，音质无损
- afconvert 不自动复制 FLAC 标签，需用 mutagen 手动复制 title/artist/album
- `organize.py` 原 m4a 标签读取有 bug：MP4 标签键为 `\xa9nam/\xa9ART/\xa9alb`，原代码只匹配字符串 `"title"/"artist"/"album"`，导致 m4a 被分到 `未知歌手/未知专辑`。已修复。

- 测试曲：`卢冠廷,AGA - 一生所爱.ncm`（Hi-Res FLAC, 96kHz/24bit, 122.9MB）
- 转换后：`一生所爱.m4a`（ALAC, 96kHz/24bit, 178MB）
- 整理路径：`卢冠廷、AGA/最经典的演唱会 Live/一生所爱.m4a`

## 代码审查问题修复 (2026-07-10)

CODE_REVIEW.md 报告中的问题继续修复中。报告状态较旧，只显示 4 项已修复，实际此前已修复更多。本次新增 9 项修复：

1. **watcher.py 竞态条件**：用 `_processing` 集合 + Lock 防止 on_created/on_modified 同时处理同一文件。
2. **跨文件系统 move**：`watcher.py` 和 `organize.py` 的 `move=True` 都改为 `copy2 + remove`，兼容跨文件系统。
3. **observer.join 无超时**：改为 `join(timeout=3600)`，避免永久挂起。
4. **文件就绪超时无重试**：增加后台重试队列，最多重试 3 次，超次标记死信。
5. **临时文件清理**：`try/finally` 保证解密临时文件在成功/失败/异常时都被清理。
6. **processed.json 无限增长**：过期清理，保留最近 90 天或最多 10000 条记录。
7. **函数内 import 与脆弱路径**：`hashlib` 移到模块顶部；`sys.path.insert` 使用绝对路径并去重。
8. **organize_file 返回值语义**：新增 `OrganizeResult` 区分 success/failed/unsupported/error。
9. **封面 MIME 检测**：支持 JPEG/PNG/WebP/BMP/GIF。
10. **pyproject.toml**：新增项目元数据、依赖、console scripts。

仍遗留：#23 scan_existing 同步阻塞、#25 main 结构重复、#26 无单元测试等低优先级建议。

## 密钥外部化配置 (2025-07-02)

为减少安全软件误报，NCM 解密密钥不再硬编码在 `ncm_decrypt.py` 中。

- 密钥写入 `config/ncm_keys.yaml`，该文件已加入 `.gitignore`，不会提交到仓库
- 仓库内保留 `config/ncm_keys.yaml.example` 作为模板
- `ncm_decrypt.py` 启动时自动加载 `config/ncm_keys.yaml`
- 缺失配置文件时会提示用户复制 example 文件
