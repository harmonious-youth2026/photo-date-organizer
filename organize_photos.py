#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
organize_photos.py - 按拍摄日期整理照片（零第三方依赖，Python 3.8+）

把一个文件夹（含任意深度子文件夹）里的照片，按拍摄日期移动到根目录下
的年 / 月 / 日分层子文件夹（默认 根/YYYY/MM/DD），或单层 YYYY-MM-DD 目录。
只有在确实有照片要放进去时才创建目录，没有内容的层级一律不建立。
日期不可信的照片不会被"猜"着归档，而是隔离到一个待处理目录，等待人工确认。

设计目标
--------
* 零依赖：只用 Python 标准库；装了 Pillow 会自动增强，但绝非必需。
* 跨平台：Windows / macOS / Linux 同一份代码。
* 数据安全：不删除、不覆盖、不解压；同名自动加后缀；重复运行不重复搬。
* 可审计：逐行启动检查、逐张结果、逐条失败原因、TXT 日志 + Markdown 报告。
* 可预演：--dry-run 只看效果，一个字节都不动。

用法
----
    python organize_photos.py "D:\\照片"
    python organize_photos.py "D:\\照片" --dry-run
    python organize_photos.py "D:\\照片" --layout flat
    python organize_photos.py --help

完整说明见 README.md。
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
import platform
import re
import shutil
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

VERSION = "1.1.0"
PROG = "organize_photos.py"

# ---------------------------------------------------------------------------
# 常量与规则
# ---------------------------------------------------------------------------

PHOTO_EXTS = frozenset("""
.jpg .jpeg .jpe .jfif .png .heic .heif .tif .tiff .bmp .gif .webp .avif
.raw .cr2 .cr3 .nef .nrw .arw .srf .sr2 .dng .orf .rw2 .pef .raf
.3fr .fff .iiq .k25 .kdc .mos .mrw .rwl .x3f .srw .erf .mef .cap
""".split())

# 递归扫描时跳过的目录（版本库、虚拟环境、系统目录，以及本工具自己的输出）
EXCLUDE_DIRS = frozenset({
    ".git", ".svn", ".hg", "__pycache__", ".venv", "venv", "node_modules",
    ".idea", ".vscode", "System Volume Information", "$RECYCLE.BIN",
})

# EXIF 里三个日期标签，按可信度排序
TAG_NAMES = {0x0132: "DateTime", 0x9003: "DateTimeOriginal", 0x9004: "DateTimeDigitized"}
TAG_PRIORITY = ("DateTimeOriginal", "DateTimeDigitized", "DateTime")
EXIF_SUBIFD_POINTER = 0x8769

# 日期字符串：容忍 YYYY:MM:DD HH:MM:SS / YYYY-MM-DD / YYYY/MM/DD / 带 T
EXIF_DATE_RE = re.compile(
    r"^\s*(\d{4})[:\-./](\d{1,2})[:\-./](\d{1,2})"
    r"(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?"
)

# 失败原因分类：(匹配正则, 类别, 原因拆解, 修复建议)
ERROR_RULES = [
    (
        r"拒绝访问|Access is denied|Permission denied|EACCES|WinError 5|权限|not permitted|EPERM",
        "权限不足",
        [
            "当前账号对这个文件/文件夹没有写入权限。",
            "文件可能被设为「只读」，或位于公司加密盘 / 受控目录 / 只读介质。",
        ],
        [
            "右键文件 -> 属性 -> 取消「只读」，或把整个文件夹复制到桌面普通目录再运行。",
            "Windows 可检查「设置 -> 隐私和安全性 -> 受控文件夹访问」是否拦截了该目录。",
        ],
    ),
    (
        r"另一个进程|正在使用|正由另一进程|being used by another|used by another process"
        r"|sharing violation|占用|locked|WinError 32|Text file busy|EBUSY|Device or resource busy",
        "文件被占用",
        [
            "照片正被微信、看图软件、剪辑软件或资源管理器「预览窗格」打开，系统锁住了它。",
            "移动文件需要独占句柄，任何程序开着它都会失败。",
        ],
        [
            "关掉正在打开这张图的程序（尤其是预览窗格），再运行一次；已整理好的不会重复处理。",
            "若反复出现，重启资源管理器或注销一次再试。",
        ],
    ),
    (
        r"路径|太长|too long|ENAMETOOLONG|WinError 206|文件名|目录名|非法字符|invalid|Invalid",
        "路径过长或含非法字符",
        [
            "完整路径（目录 + 文件名）太长，或含有当前系统不允许的字符。",
            "嵌套子文件夹越深，越容易触发 Windows 的 260 字符上限。",
        ],
        [
            "把照片文件夹移到盘符根目录，例如 D:\\Photos，再运行。",
            "Windows 10/11 可启用「Win32 长路径」策略；本工具也会自动尝试长路径写法。",
        ],
    ),
    (
        r"不存在|找不到|not found|No such file|Could not find|does not exist"
        r"|ENOENT|WinError 2|WinError 3",
        "文件在扫描后消失",
        [
            "扫描完成后、移动之前，文件被别的程序删除、改名或同步走了。",
            "或 U 盘 / 网络盘 / 网盘在运行中断开。",
        ],
        ["确认文件夹还在原位置，重新运行本工具即可。"],
    ),
    (
        r"空间|no space|ENOSPC|WinError 112|磁盘|disk full|介质|media|read-only file system",
        "磁盘空间不足或介质只读",
        [
            "目标磁盘已满，或目标位置是只读介质 / 已断开的网络盘。",
            "同盘移动通常不占额外空间，跨盘复制模式需要双倍空间。",
        ],
        ["清理出空间，或改用 --copy 之外的默认移动模式，再重新运行。"],
    ),
    (
        r"WinError 17|跨盘|different disk|cross-device|EXDEV|Invalid cross-device link",
        "跨磁盘 / 跨分区移动",
        [
            "目标目录与源文件不在同一个卷，系统级 rename 无法完成。",
            "本工具已自动回退到「复制 + 删除源文件」的方式。",
        ],
        ["若仍失败，确认目标盘可写且空间充足。"],
    ),
]

GENERIC_REASONS = [
    "属于未归类的系统错误，通常与权限、占用、路径长度或磁盘状态有关。",
    "也可能是杀毒软件 / 云同步盘临时锁住了文件。",
]
GENERIC_FIXES = [
    "把上面「系统原文」按字面去搜索，或先复制一份到桌面普通目录再运行。",
    "带上 --dry-run 先预演一次，可区分是参数问题还是文件问题。",
]

ERROR_CODES = {
    "权限不足": "E_PERMISSION",
    "文件被占用": "E_LOCKED",
    "路径过长或含非法字符": "E_PATH",
    "文件在扫描后消失": "E_MISSING",
    "磁盘空间不足或介质只读": "E_DISK",
    "跨磁盘 / 跨分区移动": "E_CROSS_DEVICE",
    "图片损坏": "E_IMAGE",
    "未归类错误": "E_UNKNOWN",
}


# ---------------------------------------------------------------------------
# 控制台输出（Windows cmd 默认 GBK，直接 print 中文会炸）
# ---------------------------------------------------------------------------

def setup_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass


def emit(text: str = "") -> None:
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"), flush=True)


def long_path(p: Path) -> str:
    """对超过 240 字符的 Windows 路径加长路径前缀；其他平台原样返回。"""
    s = os.path.abspath(str(p))
    if os.name == "nt" and len(s) > 240 and not s.startswith("\\\\?\\"):
        return "\\\\?\\" + s
    return s


# ---------------------------------------------------------------------------
# 输出：屏幕 + 运行记录（启动即落盘，窗口闪退也能查）
# ---------------------------------------------------------------------------

class Reporter:
    def __init__(self, root: Path, enabled: bool):
        self.lines: list[str] = []
        self.trace_path: Path | None = None
        if enabled:
            for candidate in (root / "整理工具_运行记录.txt",
                              Path(os.environ.get("TEMP", "/tmp")) / "整理工具_运行记录.txt"):
                try:
                    candidate.write_text("", encoding="utf-8")
                    self.trace_path = candidate
                    break
                except OSError:
                    continue

    def w(self, text: str = "") -> None:
        self.lines.append(text)
        emit(text)
        if self.trace_path:
            try:
                self.trace_path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")
            except OSError:
                pass

    def diag(self, title: str, lines: list[str]) -> None:
        self.w("")
        self.w("=" * 60)
        self.w("  " + title)
        self.w("-" * 60)
        for i, line in enumerate(lines, 1):
            self.w("  第 %d 行 | %s" % (i, line))
        self.w("=" * 60)

    def explain(self, title: str, step: str, obj: str, exc: BaseException) -> None:
        code, category, reasons, fixes = classify_error(exc)
        lines = [
            "出错步骤：%s" % step,
            "涉及对象：%s" % obj,
            "错误类型：%s" % type(exc).__name__,
            "系统原文：%s" % exc,
            "原因归类：%s（%s）" % (category, code),
        ]
        for i, r in enumerate(reasons, 1):
            lines.append("原因拆解 %d：%s" % (i, r))
        for i, f in enumerate(fixes, 1):
            lines.append("修复建议 %d：%s" % (i, f))
        self.diag(title, lines)


def classify_error(exc: BaseException) -> tuple[str, str, list[str], list[str]]:
    msg = str(exc)
    for pattern, category, reasons, fixes in ERROR_RULES:
        if re.search(pattern, msg, re.IGNORECASE):
            return ERROR_CODES[category], category, reasons, fixes
    if isinstance(exc, PermissionError):
        for pattern, category, reasons, fixes in ERROR_RULES:
            if category == "权限不足":
                return ERROR_CODES[category], category, reasons, fixes
    if isinstance(exc, FileNotFoundError):
        for pattern, category, reasons, fixes in ERROR_RULES:
            if category == "文件在扫描后消失":
                return ERROR_CODES[category], category, reasons, fixes
    if isinstance(exc, ImageFormatIssue):
        return ERROR_CODES["图片损坏"], "图片损坏", [
            "图片本身已损坏，或格式不被识别（部分 HEIC / RAW 会这样）。",
            "读取拍摄日期失败时，工具本应改用文件时间归档。",
        ], ["用看图软件确认文件能否打开；若日期不准，手动拖进对应日期文件夹即可。"]
    return ERROR_CODES["未归类错误"], "未归类错误", GENERIC_REASONS, GENERIC_FIXES


class ImageFormatIssue(Exception):
    """图片结构损坏。"""


# ---------------------------------------------------------------------------
# EXIF 读取：纯标准库优先，Pillow 可选增强
# ---------------------------------------------------------------------------

def _tiff_dates(tiff: bytes) -> tuple[list[tuple[str, str]], bool]:
    """解析 TIFF/EXIF 块，返回 ([(标签名, 日期字符串)], 头部是否合法)。"""
    if len(tiff) < 8 or tiff[:2] not in (b"II", b"MM"):
        return [], False
    endian = "<" if tiff[:2] == b"II" else ">"
    try:
        if struct.unpack_from(endian + "H", tiff, 2)[0] != 42:
            return [], False
        ifd0 = struct.unpack_from(endian + "I", tiff, 4)[0]
    except struct.error:
        return [], False

    def entries(off: int, depth: int) -> list[tuple[int, int, int, bytes]]:
        if depth > 2 or off + 2 > len(tiff):
            return []
        try:
            count = struct.unpack_from(endian + "H", tiff, off)[0]
        except struct.error:
            return []
        if count > 512:
            return []
        out = []
        unit = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}
        for i in range(count):
            p = off + 2 + i * 12
            if p + 12 > len(tiff):
                break
            try:
                tag, typ, cnt = struct.unpack_from(endian + "HHI", tiff, p)
                size = unit.get(typ, 1) * cnt
                if size <= 4:
                    raw = tiff[p + 8:p + 8 + size]
                else:
                    vo = struct.unpack_from(endian + "I", tiff, p + 8)[0]
                    if vo + size > len(tiff):
                        continue
                    raw = tiff[vo:vo + size]
                out.append((tag, typ, cnt, raw))
            except (struct.error, OverflowError):
                continue
        return out

    found: list[tuple[str, str]] = []

    def collect(items) -> None:
        for tag, typ, _cnt, raw in items:
            if tag in TAG_NAMES and typ in (2, 7):
                s = raw.split(b"\x00")[0].decode("ascii", "replace").strip()
                if s:
                    found.append((TAG_NAMES[tag], s))

    top = entries(ifd0, 0)
    collect(top)
    for tag, typ, cnt, raw in top:
        if tag == EXIF_SUBIFD_POINTER and typ == 4 and cnt >= 1 and len(raw) >= 4:
            try:
                sub_off = struct.unpack_from(endian + "I", raw, 0)[0]
                collect(entries(sub_off, 1))
            except struct.error:
                pass
    return found, True


def _jpeg_tiffs(f) -> list[bytes]:
    """遍历 JPEG 段，取出所有 Exif APP1 的 TIFF 载荷。"""
    out = []
    if f.read(2) != b"\xff\xd8":
        return out
    while True:
        b = f.read(1)
        if not b:
            break
        if b != b"\xff":
            continue
        marker = f.read(1)
        while marker == b"\xff":
            marker = f.read(1)
        if not marker:
            break
        m = marker[0]
        if m in (0xD8, 0xD9, 0x01) or 0xD0 <= m <= 0xD7:
            continue
        lb = f.read(2)
        if len(lb) < 2:
            break
        length = struct.unpack(">H", lb)[0]
        if length < 2:
            break
        payload = f.read(length - 2)
        if m == 0xE1 and payload.startswith(b"Exif\x00\x00"):
            out.append(payload[6:])
        if m == 0xDA:  # SOS：后面是压缩图像数据，不必再扫
            break
    return out


def _png_tiffs(f) -> list[bytes]:
    """遍历 PNG chunk，取出 eXIf 块（内容即 TIFF）。"""
    out = []
    f.read(8)
    while True:
        lb = f.read(4)
        if len(lb) < 4:
            break
        length = struct.unpack(">I", lb)[0]
        ctype = f.read(4)
        if len(ctype) < 4:
            break
        if length > 64 * 1024 * 1024:
            break
        data = f.read(length)
        f.read(4)  # CRC
        if ctype == b"eXIf" and data:
            out.append(data)
        if ctype == b"IEND":
            break
    return out


def _builtin_exif(path: Path) -> tuple[str | None, str | None, str]:
    """返回 (日期字符串, 标签名, 状态)。状态：ok / no_exif / corrupt_exif / unreadable。"""
    try:
        size = path.stat().st_size
    except OSError:
        return None, None, "unreadable"
    if size == 0:
        return None, None, "unreadable"
    try:
        with open(long_path(path), "rb") as f:
            head = f.read(12)
            f.seek(0)
            if head.startswith(b"\xff\xd8"):
                tiffs = _jpeg_tiffs(f)
            elif head.startswith(b"\x89PNG\r\n\x1a\n"):
                tiffs = _png_tiffs(f)
            elif head[:2] in (b"II", b"MM"):
                tiffs = [f.read(min(size, 8 * 1024 * 1024))]
            else:
                return None, None, "no_exif"
    except (OSError, ValueError, struct.error):
        return None, None, "unreadable"

    header_ok = False
    all_pairs: list[tuple[str, str]] = []
    for tiff in tiffs:
        pairs, ok = _tiff_dates(tiff)
        header_ok = header_ok or ok
        all_pairs.extend(pairs)
    for tagname in TAG_PRIORITY:
        for tag, value in all_pairs:
            if tag == tagname and value.strip():
                return value, tagname, "ok"
    if tiffs and not header_ok:
        return None, None, "corrupt_exif"
    return None, None, "no_exif"


def _pillow_exif(path: Path) -> tuple[str | None, str | None, str]:
    """可选增强：装了 Pillow 时能多读几种容器；失败一律当作「无 EXIF」。"""
    if path.suffix.lower() not in {
        ".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".tif", ".tiff",
        ".webp", ".gif", ".bmp", ".avif", ".heic", ".heif",
    }:
        return None, None, "no_exif"
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        return None, None, "no_exif"
    try:
        with Image.open(long_path(path)) as im:
            ex = im.getexif()
            pairs: list[tuple[str, str]] = []
            groups = [dict(ex.items())]
            try:
                groups.append(dict(ex.get_ifd(EXIF_SUBIFD_POINTER)))
            except Exception:
                pass
            for group in groups:
                for tag_id, value in group.items():
                    name = TAG_NAMES.get(tag_id)
                    if not name:
                        continue
                    if isinstance(value, bytes):
                        s = value.split(b"\x00")[0].decode("ascii", "replace").strip()
                    else:
                        s = str(value).strip()
                    if s:
                        pairs.append((name, s))
    except Exception:
        # 打不开就当作没有 EXIF，绝不因此把 RAW / 特殊格式误判为损坏
        return None, None, "no_exif"
    for tagname in TAG_PRIORITY:
        for tag, value in pairs:
            if tag == tagname:
                return value, tagname, "ok"
    return None, None, "no_exif"


def read_exif_datetime(path: Path, use_pillow: bool = True) -> tuple[str | None, str | None, str]:
    raw, tag, note = _builtin_exif(path)
    if note == "ok":
        return raw, tag, "ok"
    if note in ("unreadable", "corrupt_exif"):
        return None, None, note
    if use_pillow:
        return _pillow_exif(path)
    return None, None, "no_exif"


# ---------------------------------------------------------------------------
# 日期判定：可信 -> 归档；可疑 -> 隔离（绝不猜）
# ---------------------------------------------------------------------------

@dataclass
class Config:
    root: Path
    dry_run: bool = False
    copy: bool = False
    min_year: int = 1970
    max_year: int = 0
    future_days: int = 1
    quarantine_name: str = "_待处理_日期异常"
    use_pillow: bool = True
    layout: str = "ymd"             # ymd = 根/年/月/日；flat = 根/YYYY-MM-DD

    @property
    def apply(self) -> bool:
        return not self.dry_run

    @property
    def layout_desc(self) -> str:
        return ("年/月/日 三级分层（根/YYYY/MM/DD）" if self.layout == "ymd"
                else "单层日期文件夹（根/YYYY-MM-DD）")


@dataclass
class DateVerdict:
    kind: str                       # ok / suspect
    date: dt.datetime | None
    source: str
    reason: str = ""
    raw: str = ""
    tag: str = ""
    is_fallback: bool = False


@dataclass
class FileResult:
    src: Path
    rel: str
    status: str                     # moved / already / quarantined / failed / skipped
    dest: Path | None = None
    date_str: str = ""
    source: str = ""
    reason: str = ""
    raw: str = ""
    renamed: bool = False
    error: BaseException | None = None


def parse_exif_date(s: str) -> tuple[dt.datetime | None, str]:
    if re.fullmatch(r"[\s0:./\-]+", s):
        return None, "相机时间未设置（全零 / 占位符）"
    m = EXIF_DATE_RE.match(s)
    if not m:
        return None, "日期字符串格式非法，无法解析"
    try:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hh = int(m.group(4) or 0)
        mi = int(m.group(5) or 0)
        ss = int(m.group(6) or 0)
        return dt.datetime(y, mo, d, hh, mi, ss), ""
    except ValueError as e:
        return None, "日期字段不合法（%s）" % e


def why_suspect(d: dt.datetime, cfg: Config, today: dt.datetime, is_exif: bool) -> str:
    if is_exif:
        if (d.year, d.month, d.day) == (1, 1, 1):
            return "占位日期 0001-01-01（相机时间未设置）"
        if (d.year, d.month, d.day, d.hour, d.minute, d.second) == (1970, 1, 1, 0, 0, 0):
            return "哨兵日期 1970-01-01 00:00:00（Unix 纪元，多为未设置）"
        low = cfg.min_year
    else:
        # 文件时间常来自复制 / 解压，1980 是 FAT 最小年份，比 EXIF 放宽一点
        low = 1980
    if d.year < low or d.year > cfg.max_year:
        return "年份 %d 超出合理范围 [%d, %d]" % (d.year, low, cfg.max_year)
    if d > today + dt.timedelta(days=cfg.future_days):
        return "日期在未来（%s），超过今天 +%d 天" % (d.date().isoformat(), cfg.future_days)
    return ""


def judge_date(path: Path, cfg: Config, today: dt.datetime) -> DateVerdict:
    raw, tag, note = read_exif_datetime(path, cfg.use_pillow)

    if note == "unreadable":
        return DateVerdict("suspect", None, "无法读取",
                           "文件无法读取或大小为 0 字节（疑似损坏 / 占位文件）", raw or "", tag or "")
    if note == "corrupt_exif":
        return DateVerdict("suspect", None, "EXIF 段",
                           "文件含 EXIF 段但结构损坏，日期无法解析", raw or "", tag or "")
    if note == "ok" and raw:
        parsed, err = parse_exif_date(raw)
        if parsed is None:
            return DateVerdict("suspect", None, "EXIF · " + (tag or "?"),
                               "EXIF 日期异常：" + err, raw, tag or "")
        reason = why_suspect(parsed, cfg, today, is_exif=True)
        if reason:
            return DateVerdict("suspect", None, "EXIF · " + (tag or "?"), reason, raw, tag or "")
        return DateVerdict("ok", parsed, "EXIF · " + (tag or "?"), "", raw, tag or "")

    # 没有 EXIF：用文件最后修改时间兜底
    try:
        mtime = dt.datetime.fromtimestamp(path.stat().st_mtime)
    except (OSError, OverflowError, ValueError) as e:
        return DateVerdict("suspect", None, "文件修改时间",
                           "文件时间无法读取（%s）" % e, "", "")
    reason = why_suspect(mtime, cfg, today, is_exif=False)
    if reason:
        return DateVerdict("suspect", None, "文件修改时间", reason,
                           mtime.isoformat(sep=" "), "")
    return DateVerdict("ok", mtime, "文件修改时间（无 EXIF）", "", "", "", is_fallback=True)


# ---------------------------------------------------------------------------
# 文件搬运
# ---------------------------------------------------------------------------

def same_content(a: Path, b: Path) -> bool:
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
        ha, hb = hashlib.sha256(), hashlib.sha256()
        with open(long_path(a), "rb") as fa, open(long_path(b), "rb") as fb:
            for chunk in iter(lambda: fa.read(1024 * 1024), b""):
                ha.update(chunk)
            for chunk in iter(lambda: fb.read(1024 * 1024), b""):
                hb.update(chunk)
        return ha.hexdigest() == hb.hexdigest()
    except OSError:
        return False


def resolve_dest(dest_dir: Path, name: str, src: Path,
                 extra_taken: set[str] | None = None) -> tuple[Path | None, bool, bool]:
    """返回 (目标路径, 是否改名, 是否内容重复)。None 表示内容重复、无需搬运。

    extra_taken 供 --dry-run 使用：预演时不真正落盘，用虚拟占用集合模拟
    「前面的照片已经搬进去了」，从而也能算出同名冲突。
    """
    stem, ext = os.path.splitext(name)
    candidate = dest_dir / name
    k = 1
    renamed = False
    while candidate.exists() or (extra_taken and str(candidate).lower() in extra_taken):
        if candidate.exists() and same_content(src, candidate):
            return None, renamed, True
        k += 1
        if k > 9999:
            raise RuntimeError("同名文件超过 9999 个，已停止以避免死循环")
        candidate = dest_dir / ("%s_%d%s" % (stem, k, ext))
        renamed = True
    return candidate, renamed, False


def safe_move(src: Path, dst: Path, copy: bool = False) -> None:
    ls, ld = long_path(src), long_path(dst)
    if copy:
        shutil.copy2(ls, ld)
        return
    try:
        os.rename(ls, ld)          # 同卷原子移动；Windows 上目标存在时不会覆盖
    except OSError:
        shutil.move(ls, ld)        # 跨卷 / 特殊文件系统：回退到复制 + 删除


# ---------------------------------------------------------------------------
# 目录布局：年 / 月 / 日
# ---------------------------------------------------------------------------

def date_dir(root: Path, date_str: str, layout: str) -> tuple[Path, str]:
    """按布局返回 (目标目录, 显示标签)。

    ymd  -> 根/YYYY/MM/DD ，标签 YYYY/MM/DD
    flat -> 根/YYYY-MM-DD ，标签 YYYY-MM-DD
    隔离目录不受 layout 影响，始终在根目录下单层存放。
    """
    if layout == "flat":
        return root / date_str, date_str
    y, m, d = date_str.split("-")
    return root / y / m / d, "%s/%s/%s" % (y, m, d)


def prune_empty_dirs(dest_dir: Path, root: Path) -> None:
    """回收「本次刚建好却没放进去文件」的空目录链，保证不留空文件夹。

    从最深一级向上逐级检查：只有确实为空且位于 root 之内的目录才会被
    删除；一旦遇到非空目录、或走到 root（含）就立即停止。用户原有的
    目录不会被触碰。
    """
    try:
        cur = dest_dir.resolve()
        stop = root.resolve()
    except OSError:
        return
    guard = 0
    while cur != stop and guard < 12:
        try:
            cur.relative_to(stop)      # 不在 root 之下就退出，避免误删
        except ValueError:
            break
        try:
            next(cur.iterdir())        # 还有内容 -> 停止回收
            break
        except StopIteration:
            pass                       # 空目录，继续
        except OSError:
            break
        try:
            cur.rmdir()
        except OSError:
            break
        cur = cur.parent
        guard += 1


# ---------------------------------------------------------------------------
# 扫描
# ---------------------------------------------------------------------------

def excluded_dir(name: str, quarantine: str) -> bool:
    return (name in EXCLUDE_DIRS or name == quarantine
            or name.startswith("_待处理") or name.startswith("."))


def scan(root: Path, cfg: Config) -> tuple[list[Path], int, list[BaseException]]:
    photos: list[Path] = []
    total = 0
    errors: list[BaseException] = []

    def onerror(e: OSError) -> None:
        errors.append(e)

    for dirpath, dirnames, filenames in os.walk(str(root), followlinks=False, onerror=onerror):
        dirnames[:] = [d for d in dirnames if not excluded_dir(d, cfg.quarantine_name)]
        for fn in filenames:
            total += 1
            p = Path(dirpath) / fn
            if p.suffix.lower() in PHOTO_EXTS:
                photos.append(p)
    return sorted(photos, key=lambda x: str(x).lower()), total, errors


# ---------------------------------------------------------------------------
# 单张处理
# ---------------------------------------------------------------------------

def process_one(path: Path, cfg: Config, today: dt.datetime, rep: Reporter,
                reserved: set[str] | None = None) -> FileResult:
    root = cfg.root
    try:
        rel = str(path.relative_to(root))
    except ValueError:
        rel = str(path)

    if path.is_symlink():
        rep.w("  [跳过] %s   （符号链接，避免移动指向外部的文件）" % rel)
        return FileResult(path, rel, "skipped", reason="符号链接")

    verdict = judge_date(path, cfg, today)
    is_quarantine = verdict.kind == "suspect"
    date_str = (verdict.date.strftime("%Y-%m-%d")
                if verdict.kind == "ok" and verdict.date else "")
    if is_quarantine:
        dest_dir = root / cfg.quarantine_name
        label = cfg.quarantine_name
    else:
        dest_dir, label = date_dir(root, date_str, cfg.layout)

    result = FileResult(path, rel, "quarantined" if is_quarantine else "moved",
                        date_str=date_str, source=verdict.source,
                        reason=verdict.reason, raw=verdict.raw)

    # 已在正确位置：幂等，不重复搬（分层后 dest_dir 是完整的 年/月/日 路径）
    if not is_quarantine and date_str:
        try:
            if path.parent.resolve() == dest_dir.resolve():
                rep.w("  [已在位] %s  <-  %s" % (label, rel))
                result.status = "already"
                result.dest = path       # 供目录树与报告还原该层级
                return result
        except OSError:
            pass

    try:
        dest, renamed, duplicate = resolve_dest(
            dest_dir, path.name, path, reserved if not cfg.apply else None)
    except Exception as e:
        result.status = "failed"
        result.error = e
        return result

    if duplicate:
        rep.w("  [跳过] %s   （目标位置已存在内容完全相同的文件）" % rel)
        result.status = "skipped"
        result.reason = "目标位置已有内容相同的文件"
        return result

    result.dest = dest
    result.renamed = renamed

    if not cfg.apply:
        if dest and reserved is not None:
            reserved.add(str(dest).lower())
        note = verdict.reason if is_quarantine else verdict.source
        rep.w("  [预演] %s  ->  %s   (%s)" % (rel, label, note))
        return result

    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        safe_move(path, dest, copy=cfg.copy)
        if is_quarantine:
            rep.w("  [隔离] %s  ->  %s   (%s)" % (rel, label, verdict.reason))
        else:
            note = "，同名已加后缀" if renamed else ""
            rep.w("  [归档] %s  ->  %s   (%s%s)" % (rel, label, verdict.source, note))
    except Exception as e:
        result.status = "failed"
        result.error = e
        # 移动没成功 -> 刚建好的空目录链立即回收，绝不留下空文件夹
        prune_empty_dirs(dest_dir, root)
        rep.w("  [失败] %s   （原因见文末逐行说明）" % rel)
    return result


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------

@dataclass
class Stats:
    total_files: int = 0
    photos: int = 0
    moved: int = 0
    already: int = 0
    quarantined: int = 0
    failed: int = 0
    skipped: int = 0
    fallback: int = 0
    renamed: int = 0
    by_date: dict = field(default_factory=dict)

    def account(self, r: FileResult) -> None:
        if r.status == "moved":
            self.moved += 1
        elif r.status == "already":
            self.already += 1
        elif r.status == "quarantined":
            self.quarantined += 1
        elif r.status == "failed":
            self.failed += 1
        else:
            self.skipped += 1
        if r.renamed and r.status in ("moved", "quarantined"):
            self.renamed += 1
        if r.status in ("moved", "already"):
            if r.source.startswith("文件修改时间"):
                self.fallback += 1
            if r.date_str:
                slot = self.by_date.setdefault(r.date_str, {"n": 0, "exif": 0, "file": 0})
                slot["n"] += 1
                if r.source.startswith("EXIF"):
                    slot["exif"] += 1
                else:
                    slot["file"] += 1


def build_layout_tree(cfg: Config, results: list[FileResult]) -> tuple[list[str], int]:
    """构造本次生成的目录树（虚拟，--dry-run 同样适用）。

    返回 (树文本行, 目录节点数)。只展示工具管理的日期目录与隔离目录：
    原有子文件夹、视频/文档等非照片文件保持原位，不在树中展开。
    树由「确实归档成功的结果」反推，因此每个目录节点都至少有一个子项，
    天然不会出现空层级。
    """
    tree: dict = {}
    for r in results:
        if r.status not in ("moved", "already", "quarantined") or not r.dest:
            continue
        try:
            parts = r.dest.relative_to(cfg.root).parts
        except ValueError:
            continue
        node = tree
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node.setdefault(parts[-1], None)      # 文件节点

    def count_dirs(node: dict) -> int:
        n = 0
        for v in node.values():
            if isinstance(v, dict):
                n += 1 + count_dirs(v)
        return n

    def render(node: dict, prefix: str = "") -> list[str]:
        out: list[str] = []
        # 目录排前面，文件排后面，同级按名称排序
        items = sorted(node.items(), key=lambda kv: (kv[1] is None, kv[0].lower()))
        for i, (name, child) in enumerate(items):
            last = (i == len(items) - 1)
            branch = "└── " if last else "├── "
            if isinstance(child, dict):
                out.append(prefix + branch + name + "/")
                out.extend(render(child, prefix + ("    " if last else "│   ")))
            else:
                out.append(prefix + branch + name)
        return out

    n_dirs = count_dirs(tree)
    if not tree:
        return ["（本次没有生成任何目录）"], 0
    return [cfg.root.name + "/"] + render(tree), n_dirs


def build_markdown_report(cfg: Config, stats: Stats, results: list[FileResult],
                          scan_errors: list[BaseException], started: dt.datetime,
                          rep: Reporter) -> str:
    q = [r for r in results if r.status == "quarantined"]
    f = [r for r in results if r.status == "failed"]
    mode = "预演（--dry-run，未改动任何文件）" if cfg.dry_run else (
        "复制（--copy，保留源文件）" if cfg.copy else "移动（正式运行）")
    L: list[str] = []
    L.append("# 照片整理运行报告")
    L.append("")
    L.append("> 由 `organize_photos.py` v%s 自动生成 · %s" %
             (VERSION, started.strftime("%Y-%m-%d %H:%M:%S")))
    L.append("")
    L.append("## 概要")
    L.append("")
    L.append("| 项目 | 值 |")
    L.append("| --- | --- |")
    L.append("| 目标目录 | `%s` |" % cfg.root)
    L.append("| 运行模式 | %s |" % mode)
    L.append("| 运行环境 | Python %s / %s |" % (platform.python_version(), platform.system()))
    L.append("| 日期合理范围 | EXIF %d–%d 年；文件时间 1980–%d 年；未来容差 %d 天 |" %
             (cfg.min_year, cfg.max_year, cfg.max_year, cfg.future_days))
    L.append("| 目录结构 | %s |" % cfg.layout_desc)
    L.append("| 异常隔离目录 | `%s` |" % cfg.quarantine_name)
    L.append("| 运行记录 | `%s` |" % (rep.trace_path or "未写入"))
    L.append("")
    L.append("## 结果统计")
    L.append("")
    L.append("| 结果 | 张数 | 说明 |")
    L.append("| --- | ---: | --- |")
    L.append("| 扫描到的文件 | %d | 含非照片文件 |" % stats.total_files)
    L.append("| 识别为照片 | %d | 按扩展名白名单 |" % stats.photos)
    L.append("| 本次移动成功 | %d | 已归入日期文件夹 |" % stats.moved)
    L.append("| 已在正确位置 | %d | 重复运行时跳过 |" % stats.already)
    L.append("| **日期异常隔离** | **%d** | 不做归档，等待人工确认 |" % stats.quarantined)
    L.append("| 处理失败 | %d | 原因见下方逐条说明 |" % stats.failed)
    L.append("| 其他跳过 | %d | 符号链接 / 内容重复 |" % stats.skipped)
    L.append("| 按文件时间兜底 | %d | 没有可用 EXIF |" % stats.fallback)
    L.append("| 同名自动加后缀 | %d | 未覆盖任何文件 |" % stats.renamed)
    L.append("")
    L.append("**>>> 一共整理了 %d 张照片 <<<**" % (stats.moved + stats.already))
    if stats.quarantined:
        L.append("")
        L.append("另有 **%d 张**因日期不可信被隔离，未计入上面的整理数。" % stats.quarantined)
    L.append("")
    L.append("## 按日期明细")
    L.append("")
    if stats.by_date:
        L.append("| 日期 | 目标目录（相对根目录） | 张数 | 来自 EXIF | 来自文件时间 |")
        L.append("| --- | --- | ---: | ---: | ---: |")
        for k in sorted(stats.by_date):
            v = stats.by_date[k]
            _, rel_label = date_dir(cfg.root, k, cfg.layout)
            L.append("| `%s` | `%s/` | %d | %d | %d |" %
                     (k, rel_label, v["n"], v["exif"], v["file"]))
        L.append("")
        L.append("共 %d 个日期目录。" % len(stats.by_date))
    else:
        L.append("本次没有产生日期目录。")
    L.append("")

    tree_lines, n_dirs = build_layout_tree(cfg, results)
    L.append("## 实际生成的目录结构")
    L.append("")
    L.append("采用 **%s**；只有确实有照片要放进去的层级才会被创建。" % cfg.layout_desc)
    L.append("")
    L.append("```text")
    L.extend(tree_lines)
    L.append("```")
    L.append("")
    if cfg.dry_run:
        L.append("> 预演模式：以上是**将要生成**的结构，此时磁盘上尚未创建任何目录。")
    else:
        L.append("> 共 %d 个目录节点，每一个都至少包含照片或下级目录，"
                 "**没有产生任何空文件夹**。" % n_dirs)
    L.append("> 原有的子文件夹、视频/文档等非照片文件保持原位，未在树中展开。")
    L.append("")

    if q:
        L.append("## 日期异常隔离清单")
        L.append("")
        L.append("这些照片**没有按日期归档**，原样移入 `%s`，等待人工确认。" % cfg.quarantine_name)
        L.append("")
        L.append("| 文件 | 原路径 | 判定原因 | 原始日期值 |")
        L.append("| --- | --- | --- | --- |")
        for r in q:
            L.append("| `%s` | `%s` | %s | `%s` |" %
                     (r.src.name, r.rel.replace("\\", "/"), r.reason, r.raw or "—"))
        L.append("")
        L.append("> 处理建议：用看图软件确认拍摄日期后，手动拖进对应日期文件夹；")
        L.append("> 若确认日期无误，可用 `--min-year` / `--max-year` / `--future-days` 放宽规则后重跑。")
        L.append("")

    if f:
        L.append("## 失败清单（逐条说明）")
        L.append("")
        for i, r in enumerate(f, 1):
            code, category, reasons, fixes = classify_error(r.error or Exception("未知错误"))
            L.append("### %d. `%s`" % (i, r.rel.replace("\\", "/")))
            L.append("")
            L.append("- **出错步骤**：移动照片到目标文件夹")
            L.append("- **涉及对象**：`%s`" % r.src)
            L.append("- **错误类型**：`%s`" % type(r.error).__name__)
            L.append("- **系统原文**：`%s`" % (r.error or ""))
            L.append("- **原因归类**：%s" % category)
            for j, x in enumerate(reasons, 1):
                L.append("- **原因拆解 %d**：%s" % (j, x))
            for j, x in enumerate(fixes, 1):
                L.append("- **修复建议 %d**：%s" % (j, x))
            L.append("")

    if scan_errors:
        L.append("## 无法读取的子目录")
        L.append("")
        for i, e in enumerate(scan_errors, 1):
            code, category, reasons, fixes = classify_error(e)
            L.append("%d. `%s` — %s：%s" % (i, getattr(e, "filename", "?"), category, e))
        L.append("")

    L.append("## 安全说明")
    L.append("")
    L.append("- 视频、文档、压缩包等非照片文件保持原位，不会被移动或删除。")
    L.append("- 原始子文件夹不会被删除；只移动文件。")
    L.append("- 同名文件不覆盖：内容相同则跳过，不同则自动加 `_2`、`_3` 后缀。")
    L.append("- 日期不可信的照片不猜测、不丢弃，统一隔离并留下清单。")
    L.append("- 重复运行不会产生重复搬运（幂等）。")
    L.append("- 只有确有照片要放入时才创建目录，不会留下空的年份 / 月份 / 日期文件夹。")
    L.append("")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=PROG,
        description="按拍摄日期把散落在多层子文件夹里的照片归档到 根/年/月/日 分层目录"
                    "（默认），无照片的层级一律不创建；也可用 --layout flat 改回单层"
                    " YYYY-MM-DD。日期不可信的照片会隔离到待处理文件夹，不猜测、不丢弃。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例：
  python organize_photos.py "D:\\照片"
  python organize_photos.py "D:\\照片" --dry-run
  python organize_photos.py "D:\\照片" --copy
  python organize_photos.py "D:\\照片" --layout flat
  python organize_photos.py "D:\\照片" --min-year 2000 --max-year 2030
""",
    )
    p.add_argument("root", nargs="?", default=None,
                   help="要整理的文件夹；不填则用当前工作目录")
    p.add_argument("--layout", choices=("ymd", "flat"), default="ymd",
                   help="目录结构：ymd = 根/YYYY/MM/DD 三级分层（默认，空层级不建）；"
                        "flat = 根/YYYY-MM-DD 单层")
    p.add_argument("--dry-run", action="store_true",
                   help="只预演并打印结果，不创建文件夹、不移动任何文件")
    p.add_argument("--copy", action="store_true",
                   help="复制而非移动，源文件保持原位")
    p.add_argument("--min-year", type=int, default=1970,
                   help="EXIF 日期允许的最早年份（默认 1970）")
    p.add_argument("--max-year", type=int, default=None,
                   help="日期允许的最晚年份（默认：今年 + 1）")
    p.add_argument("--future-days", type=int, default=1,
                   help="允许日期比今天最多晚几天，超出即判异常（默认 1）")
    p.add_argument("--quarantine", default="_待处理_日期异常",
                   help="异常日期照片的隔离文件夹名（默认 _待处理_日期异常）")
    p.add_argument("--no-pillow", action="store_true",
                   help="禁用可选的 Pillow 增强，只用内置 EXIF 解析器")
    p.add_argument("--version", action="version", version="%(prog)s " + VERSION)
    return p


def main(argv: list[str] | None = None) -> int:
    setup_console()
    args = build_parser().parse_args(argv)
    today = dt.datetime.now()
    started = today

    raw_root = args.root or os.getcwd()
    root = Path(os.path.abspath(os.path.expanduser(raw_root)))
    cfg = Config(
        root=root,
        dry_run=args.dry_run,
        copy=args.copy,
        min_year=args.min_year,
        max_year=args.max_year or (today.year + 1),
        future_days=args.future_days,
        quarantine_name=args.quarantine,
        use_pillow=not args.no_pillow,
        layout=args.layout,
    )

    rep = Reporter(root, enabled=cfg.apply)

    rep.w("=" * 60)
    rep.w("  照片整理 · 按拍摄日期归档   v" + VERSION)
    rep.w("=" * 60)
    rep.w("  运行时间   : " + started.strftime("%Y-%m-%d %H:%M:%S"))
    rep.w("  Python     : %s / %s" % (platform.python_version(), platform.system()))
    rep.w("  目标目录   : " + str(root))
    rep.w("  运行模式   : " + ("预演（--dry-run，不改动任何文件）" if cfg.dry_run
                              else ("复制（--copy，保留源文件）" if cfg.copy else "移动（正式运行）")))
    rep.w("  运行记录   : " + (str(rep.trace_path) if rep.trace_path else "预演模式，未写入"))
    rep.w("  目录结构   : " + cfg.layout_desc)
    rep.w("  异常隔离夹 : " + cfg.quarantine_name)
    rep.w("  日期规则   : EXIF %d-%d，文件时间 1980-%d，未来容差 %d 天" %
          (cfg.min_year, cfg.max_year, cfg.max_year, cfg.future_days))
    rep.w("")
    rep.w("【启动检查】逐行结果：")
    n = 0

    n += 1
    if sys.version_info < (3, 8):
        rep.diag("启动失败：逐行报错说明", [
            "检查项：Python 版本，当前 %s" % platform.python_version(),
            "原因 A：系统自带的是 Python 2 或 3.7 以下的老版本。",
            "修复建议：到 python.org 安装 Python 3.8 或更新版本，重新运行。",
            "本次没有移动任何文件，数据安全。",
        ])
        return 2
    rep.w("  第 %d 行 | [通过] Python %s，满足 3.8+ 要求。" % (n, platform.python_version()))

    n += 1
    if not root.exists():
        rep.diag("启动失败：逐行报错说明", [
            "检查项：目标文件夹是否存在。路径 = %s" % root,
            "原因 A：文件夹被改名、移动或删除了。",
            "原因 B：路径里的中文、空格或盘符写错了；U 盘换了盘符。",
            "原因 C：文件夹在网盘 / 移动硬盘上，此刻没连接电脑。",
            "修复建议 1：把完整路径用引号包起来作为第一个参数传入。",
            "修复建议 2：Windows 下可把文件夹直接拖到 run.bat 图标上。",
            "修复建议 3：在资源管理器地址栏复制完整路径再粘贴。",
            "本次没有移动任何文件，数据安全。",
        ])
        return 2
    if not root.is_dir():
        rep.diag("启动失败：逐行报错说明", [
            "检查项：目标路径是不是文件夹。路径 = %s" % root,
            "原因：传入的是一个文件，不是目录。",
            "修复建议：改传这个文件所在的文件夹路径。",
            "本次没有移动任何文件，数据安全。",
        ])
        return 2
    rep.w("  第 %d 行 | [通过] 目标文件夹存在。" % n)

    n += 1
    if cfg.min_year > cfg.max_year or cfg.future_days < 0:
        rep.diag("启动失败：逐行报错说明", [
            "检查项：日期参数是否自洽。",
            "原因：--min-year (%d) 大于 --max-year (%d)，或 --future-days 为负数。" %
            (cfg.min_year, cfg.max_year),
            "修复建议：调整参数后重新运行，例如 --min-year 1970 --max-year 2027。",
            "本次没有移动任何文件，数据安全。",
        ])
        return 2
    rep.w("  第 %d 行 | [通过] 日期参数自洽。" % n)

    n += 1
    try:
        next(os.scandir(str(root)), None)
        rep.w("  第 %d 行 | [通过] 目标文件夹可以读取。" % n)
    except OSError as e:
        rep.explain("读取失败：逐行报错说明", "读取目标文件夹", str(root), e)
        return 2

    n += 1
    if cfg.dry_run:
        rep.w("  第 %d 行 | [跳过] 预演模式不检测写入权限。" % n)
    elif rep.trace_path and str(rep.trace_path).lower().startswith(str(root).lower()):
        rep.w("  第 %d 行 | [通过] 目标文件夹可写（运行记录已建立）。" % n)
    elif rep.trace_path:
        rep.w("  第 %d 行 | [警告] 目标文件夹不可写，运行记录改存 TEMP；仍会尝试整理，失败逐行说明。" % n)
    else:
        rep.w("  第 %d 行 | [警告] 任何位置都写不了运行记录；结果只显示在屏幕上。" % n)

    n += 1
    pillow_note = "未安装（不影响使用）"
    if cfg.use_pillow:
        try:
            import PIL  # noqa: F401
            pillow_note = "已安装，自动启用格式增强"
        except ImportError:
            pillow_note = "未安装，使用内置解析器（零依赖）"
    else:
        pillow_note = "已通过 --no-pillow 禁用"
    rep.w("  第 %d 行 | [通过] 内置 EXIF 解析器就绪；Pillow：%s。" % (n, pillow_note))

    n += 1
    photos, total_files, scan_errors = scan(root, cfg)
    rep.w("  第 %d 行 | [通过] 递归扫描完成：共 %d 个文件，其中照片 %d 张。" %
          (n, total_files, len(photos)))
    if scan_errors:
        rep.w("          | 有 %d 个子目录读不到（已跳过），原因见文末逐行说明。" % len(scan_errors))

    if not photos:
        rep.diag("没有找到照片：逐行说明", [
            "检查项：在 %s 及其所有子文件夹里筛选照片扩展名。" % root,
            "原因 A：照片扩展名被改过，或还藏在压缩包里（本工具不解压 zip）。",
            "原因 B：文件夹里确实只有视频、文档、压缩包。",
            "原因 C：照片已被整理过，或文件夹被移走了。",
            "修复建议：确认照片确实在该目录下；压缩包请先解压再运行。",
            "本次没有移动任何文件，数据安全。",
        ])
        return 0

    rep.w("")
    rep.w("【开始整理】逐张处理：")
    results: list[FileResult] = []
    stats = Stats(total_files=total_files, photos=len(photos))
    reserved: set[str] = set()
    for i, p in enumerate(photos, 1):
        # 单张失败不能中断整批
        try:
            r = process_one(p, cfg, today, rep, reserved)
        except Exception as e:
            rep.w("  [%d/%d] [失败] %s   （原因见文末逐行说明）" % (i, len(photos), p.name))
            r = FileResult(p, str(p), "failed", error=e)
        results.append(r)
        stats.account(r)

    rep.w("")
    rep.w("=" * 60)
    rep.w("  整理完成 —— 结果汇总")
    rep.w("=" * 60)
    move_line = ("预计移动成功             : %d 张" if cfg.dry_run
                 else "本次移动成功             : %d 张") % stats.moved
    for i, line in enumerate([
        "扫描文件总数             : %d 个" % stats.total_files,
        "识别为照片               : %d 张" % stats.photos,
        move_line,
        "已在正确日期文件夹（跳过）: %d 张" % stats.already,
        "日期异常隔离（待人工确认）: %d 张" % stats.quarantined,
        "处理失败                 : %d 张" % stats.failed,
        "其他跳过                 : %d 张" % stats.skipped,
        "无 EXIF，按文件时间归档   : %d 张" % stats.fallback,
        "重名自动加后缀           : %d 张" % stats.renamed,
    ], 1):
        rep.w("  第 %d 行 | %s" % (i, line))
    rep.w("  " + "-" * 56)
    rep.w("  >>> 一共整理了 %d 张照片 <<<" % (stats.moved + stats.already))
    if stats.quarantined:
        rep.w("  另有 %d 张日期异常，已隔离到 %s，未做归档。" %
              (stats.quarantined, cfg.quarantine_name))
    rep.w("=" * 60)
    rep.w("")
    rep.w("  按日期明细（目标目录 -> 张数）：")
    for k in sorted(stats.by_date):
        _, lbl = date_dir(root, k, cfg.layout)
        rep.w("    %s/   ->   %d 张" % (lbl, stats.by_date[k]["n"]))
    rep.w("  共 %d 个日期目录，位置：%s" % (len(stats.by_date), root))
    if cfg.layout == "ymd":
        rep.w("  分层规则：根/年/月/日；无照片的年份与月份不建立，不产生空文件夹。")
    rep.w("")
    rep.w("  安全说明：非照片文件保持原位；子文件夹不删除；不覆盖同名文件；")
    rep.w("            日期不可信的照片只隔离、不猜测；重复运行不会重复搬运。")

    failures = [r for r in results if r.status == "failed"]
    if failures:
        rep.w("")
        rep.w("#" * 60)
        rep.w("  以下 %d 张未整理成功，逐行说明报错原因：" % len(failures))
        rep.w("#" * 60)
        for i, r in enumerate(failures, 1):
            rep.explain("错误 %d/%d" % (i, len(failures)),
                        "移动照片到目标文件夹", str(r.src), r.error or Exception("未知错误"))
    for i, e in enumerate(scan_errors, 1):
        rep.explain("无法读取的子目录 %d/%d" % (i, len(scan_errors)),
                    "递归扫描子目录", getattr(e, "filename", str(root)), e)

    if cfg.apply:
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            log = root / ("照片整理日志_%s.txt" % stamp)
            log.write_text("\n".join(rep.lines) + "\n", encoding="utf-8")
            rep.w("")
            rep.w("  完整日志已保存到：" + str(log))
        except OSError as e:
            rep.w("  [提示] 日志写入失败（不影响整理结果）：%s" % e)

        try:
            md = build_markdown_report(cfg, stats, results, scan_errors, started, rep)
            report = root / ("照片整理报告_%s.md" % stamp)
            report.write_text(md, encoding="utf-8")
            rep.w("  结构化报告已保存到：" + str(report))
        except OSError as e:
            rep.w("  [提示] 报告写入失败（不影响整理结果）：%s" % e)

        q = [r for r in results if r.status == "quarantined" and r.dest]
        if q:
            try:
                qdir = root / cfg.quarantine_name
                qdir.mkdir(parents=True, exist_ok=True)
                import csv
                with open(long_path(qdir / "_隔离清单.csv"), "w",
                          newline="", encoding="utf-8-sig") as fh:
                    wr = csv.writer(fh)
                    wr.writerow(["文件名", "原相对路径", "保存为", "判定原因", "原始日期值", "日期来源"])
                    for r in q:
                        wr.writerow([r.src.name, r.rel, (r.dest.name if r.dest else ""),
                                     r.reason, r.raw or "—", r.source])
                rep.w("  隔离清单已保存到：" + str(qdir / "_隔离清单.csv"))
            except OSError as e:
                rep.w("  [提示] 隔离清单写入失败：%s" % e)
    else:
        rep.w("")
        rep.w("  [预演结束] 以上只是模拟结果，没有创建或移动任何文件。")
        rep.w("              确认无误后去掉 --dry-run 再运行一次即可。")

    return 0 if stats.failed == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        emit("")
        emit("  已由用户中断（Ctrl+C）。本次已经完成的移动会保留，重新运行不会重复处理。")
        sys.exit(130)
    except Exception as exc:  # 顶层兜底：任何未预料异常也逐行解释
        emit("")
        emit("=" * 60)
        emit("  未预料到的致命错误：逐行报错说明")
        emit("-" * 60)
        for i, line in enumerate([
            "错误类型：" + type(exc).__name__,
            "系统原文：" + str(exc),
            "原因拆解 1：通常是传入的路径有问题，或某个文件在扫描与移动之间被外部程序改动。",
            "原因拆解 2：也可能是文件系统权限、编码或磁盘状态异常。",
            "修复建议 1：带 --dry-run 先预演一次，确认参数与路径正确。",
            "修复建议 2：把上面的「系统原文」按字面搜索，或复制一份到普通目录再试。",
            "本次没有删除任何文件；已经成功的移动会保留，重新运行不会重复处理。",
        ], 1):
            emit("  第 %d 行 | %s" % (i, line))
        emit("=" * 60)
        sys.exit(1)
