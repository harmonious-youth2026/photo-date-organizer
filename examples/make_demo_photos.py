#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_demo_photos.py - 生成 organize_photos.py 的演示数据（零依赖）

用纯标准库手工构造带 EXIF APP1 段的最小 JPEG，以及无 EXIF 的 PNG。
不依赖 Pillow，也不需要任何真实照片，任何人 clone 下来都能一键复现演示。

用法：
    python examples/make_demo_photos.py                # 生成到 ./_demo/军训照片
    python examples/make_demo_photos.py "D:\\temp\\demo"

生成的文件覆盖以下边界场景（对应 README 的「特殊情况」表）：
    正常 EXIF / 深层子目录 / 同名冲突 / 无 EXIF 按文件时间兜底
    跨年、跨月照片（验证 年/月/日 分层，且没有照片的月份不会被创建）
    EXIF 全零 / Unix 纪元哨兵 / 未来日期 / 年份越界 / 格式非法
    月份非法 / EXIF 段损坏 / 0 字节空文件 / 文件时间过早 / 文件时间在未来
    非照片文件（txt / mp4 / docx / zip）原地不动
"""

from __future__ import annotations

import os
import struct
import sys
from datetime import datetime
from pathlib import Path
import shutil

# EXIF 标签
TAG_EXIF_SUBIFD = 0x8769
TAG_DATETIME_ORIGINAL = 0x9003


def build_tiff(date_string: str) -> bytes:
    """构造一个小端 TIFF/EXIF 块，只含 ExifIFDPointer -> DateTimeOriginal。"""
    data = date_string.encode("ascii", "replace") + b"\x00"
    count = len(data)
    ifd0_off, subifd_off, str_off = 8, 26, 44

    out = bytearray()
    out += b"II" + struct.pack("<H", 42) + struct.pack("<I", ifd0_off)
    # IFD0：1 个条目，指向 Exif 子 IFD
    out += struct.pack("<H", 1)
    out += struct.pack("<HHI", TAG_EXIF_SUBIFD, 4, 1) + struct.pack("<I", subifd_off)
    out += struct.pack("<I", 0)  # 没有下一个 IFD
    # Exif 子 IFD：1 个条目，DateTimeOriginal (ASCII)
    out += struct.pack("<H", 1)
    out += struct.pack("<HHI", TAG_DATETIME_ORIGINAL, 2, count) + struct.pack("<I", str_off)
    out += struct.pack("<I", 0)
    out += data
    assert len(out) == str_off + count, "TIFF 布局计算错误"
    return bytes(out)


def make_jpeg(date_string: str | None = None, *, corrupt: bool = False,
             filler: bytes = b"") -> bytes:
    """构造最小 JPEG：SOI + APP1(Exif) + EOI。filler 用来制造同名但内容不同。"""
    if date_string is None:
        # 完全不含 EXIF 的 JPEG -> 走文件修改时间兜底
        return b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", 4) + b"\x00\x00" + b"\xff\xd9"
    tiff = build_tiff(date_string)
    if corrupt:
        tiff = b"ZZ" + tiff[2:]          # 破坏字节序标记，制造「EXIF 段损坏」
    payload = b"Exif\x00\x00" + tiff
    seg = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    return b"\xff\xd8" + seg + filler + b"\xff\xd9"


def make_png() -> bytes:
    """最小 PNG（无 eXIf 块）-> 没有 EXIF，按文件修改时间归档。"""
    def chunk(ctype: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", 0)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", b"\x00")
            + chunk(b"IEND", b""))


def write(path: Path, content: bytes, mtime: datetime | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    if mtime is not None:
        ts = mtime.timestamp()
        os.utime(str(path), (ts, ts))


def main() -> int:
    default = Path(__file__).resolve().parent.parent / "_demo" / "军训照片"
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else default

    # 安全护栏：只清理本脚本自己创建的演示目录，绝不误删别处
    if root.exists():
        if "_demo" not in str(root) and root.name != "军训照片":
            print("拒绝清理非演示目录：%s" % root)
            return 2
        shutil.rmtree(str(root))
    root.mkdir(parents=True, exist_ok=True)

    d = lambda y, m, day, h=10, mi=0, s=0: "%04d:%02d:%02d %02d:%02d:%02d" % (y, m, day, h, mi, s)

    # ---------- 正常照片：会被归档到日期文件夹 ----------
    normal = [
        ("IMG_0001.jpg", d(2026, 9, 1, 8, 30), b"A"),
        ("IMG_0002.jpg", d(2026, 9, 1, 9, 15), b"B"),
        ("子文件夹A/深层/IMG_0003.jpg", d(2026, 9, 3, 14, 20), b"C"),
        ("IMG_0004.jpg", d(2026, 9, 5, 16, 45), b"D"),
        ("子文件夹B/IMG_0005.jpg", d(2026, 9, 1, 11, 5), b"E"),
        ("IMG_0006.jpg", d(2026, 9, 8, 7, 40), b"F"),
        ("IMG_0007.jpg", d(2026, 9, 3, 18, 0), b"G"),
        ("子文件夹A/IMG_0008.jpg", d(2026, 9, 5, 12, 30), b"H"),
        # 同名冲突：与顶层 IMG_0001.jpg 同名但内容不同 -> 自动加 _2
        ("子文件夹B/IMG_0001.jpg", d(2026, 9, 1, 10, 0), b"DIFFERENT-CONTENT"),
        # 跨年 / 跨月：验证 根/年/月/日 分层，以及「无照片的月份不建立」。
        # 有照片的层级：2025/12、2026/01、2026/03、2026/09
        # 不应出现的层级：2026/02、2026/04~08、2026/10~12
        ("跨年_2025/IMG_0100.jpg", d(2025, 12, 25, 20, 0), b"I"),
        ("跨月_一月/IMG_0101.jpg", d(2026, 1, 17, 9, 0), b"J"),
        ("跨月_三月/IMG_0102.jpg", d(2026, 3, 8, 15, 30), b"K"),
    ]
    for rel, date, filler in normal:
        write(root / rel, make_jpeg(date, filler=filler))

    # ---------- 无 EXIF：按文件修改时间兜底 ----------
    write(root / "无EXIF_截图.png", make_png(), mtime=datetime(2026, 9, 7, 13, 20))
    write(root / "子文件夹A/无EXIF_2.png", make_png(), mtime=datetime(2026, 9, 8, 20, 10))
    write(root / "无EXIF_JPEG.jpg", make_jpeg(), mtime=datetime(2026, 9, 2, 9, 0))

    # ---------- 日期异常：全部进入隔离文件夹，不猜测、不归档 ----------
    suspects = [
        ("异常_相机未设时间.jpg", "0000:00:00 00:00:00"),      # 全零占位
        ("异常_Unix纪元.jpg", d(1970, 1, 1, 0, 0, 0)),        # 哨兵值
        ("异常_远古年份.jpg", d(1955, 3, 8)),                   # 年份小于 min_year
        ("异常_未来年份.jpg", d(2035, 7, 20)),                  # 年份大于 max_year / 未来
        ("异常_格式乱.jpg", "not-a-date-string"),               # 格式非法
        ("异常_月份13.jpg", d(2026, 13, 45)),                   # 日期字段不合法
    ]
    for rel, date in suspects:
        write(root / rel, make_jpeg(date))
    write(root / "异常_EXIF段损坏.jpg", make_jpeg(d(2026, 9, 4), corrupt=True))
    write(root / "异常_空文件.jpg", b"")                        # 0 字节
    write(root / "异常_文件时间过早.png", make_png(), mtime=datetime(1975, 4, 1))
    write(root / "异常_文件时间在未来.png", make_png(), mtime=datetime(2031, 1, 5))

    # ---------- 非照片文件：应当原地不动 ----------
    write(root / "说明.txt", "这是说明文档，工具不应移动它。\n".encode("utf-8"))
    write(root / "宣传视频.mp4", b"FAKE-VIDEO-DATA")
    write(root / "子文件夹A/稿件.docx", b"FAKE-DOCX-DATA")
    write(root / "素材备份.zip", b"FAKE-ZIP-DATA")
    # 一个本工具生成的文件，也应被忽略（非照片）
    write(root / "整理工具_运行记录.txt", b"")

    # 空目录，验证「原始子文件夹不会被删除」
    (root / "子文件夹C" / "更深的空目录").mkdir(parents=True, exist_ok=True)

    files = [p for p in root.rglob("*") if p.is_file()]
    print("演示数据已生成：%s" % root)
    print("文件总数：%d" % len(files))
    print("  其中照片：%d" % sum(1 for p in files if p.suffix.lower() in
                                {".jpg", ".jpeg", ".png", ".heic", ".tif", ".bmp", ".gif"}))
    print("  非照片：%d" % sum(1 for p in files if p.suffix.lower() not in
                              {".jpg", ".jpeg", ".png", ".heic", ".tif", ".bmp", ".gif"}))
    print("目录：")
    for p in sorted(x for x in root.rglob("*") if x.is_dir()):
        print("  " + str(p.relative_to(root)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
