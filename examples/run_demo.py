#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_demo.py - 一键演示 + 自动验收（用于生成 README 的示例截图）

分阶段执行，每个阶段在交互式终端下会暂停，方便逐屏截图：

    STAGE 0  环境与版本
    STAGE 1  生成演示数据（含跨年 / 跨月 / 异常日期）
    STAGE 2  整理前的目录树
    STAGE 3  --dry-run 预演（确认一个字节都不动）
    STAGE 4  正式整理（默认 根/年/月/日 分层）
    STAGE 5  整理后的目录结构（只列出工具生成的层级）
    STAGE 6  自动验收：逐条 PASS / FAIL
    STAGE 7  截图与上传建议

特点：全程零第三方依赖、不联网、只在项目自己的 _demo/ 目录里造数据，
绝不触碰桌面上的真实照片。非交互环境（或加 --no-pause）会连续跑完不暂停。

用法：
    python examples/run_demo.py
    python examples/run_demo.py --no-pause
Windows 下也可以直接双击项目根目录的「演示测试.bat」。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "organize_photos.py"
MAKER = ROOT / "examples" / "make_demo_photos.py"
DEMO = ROOT / "_demo" / "军训照片"
FLAT = ROOT / "_demo" / "flat兼容测试"
QUAR = "_待处理_日期异常"
NO_PAUSE = "--no-pause" in sys.argv


def setup() -> None:
    """让 Windows cmd 也能正确显示中文与树形符号。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass


def say(text: str = "") -> None:
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"), flush=True)


def banner(no: int, title: str) -> None:
    say("")
    say("=" * 66)
    say("  STAGE %d  |  %s" % (no, title))
    say("=" * 66)


def pause() -> None:
    if NO_PAUSE or not sys.stdin.isatty():
        say("")
        return
    try:
        input("  [ 按回车进入下一阶段；现在可以截图本屏 ]")
    except (EOFError, KeyboardInterrupt):
        say("")


def run(args: list) -> tuple[int, str]:
    cp = subprocess.run(
        [sys.executable] + [str(a) for a in args],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    return cp.returncode, (cp.stdout or "") + (cp.stderr or "")


def pick(out: str, needles: list[str]) -> list[str]:
    return [l for l in out.splitlines() if any(n in l for n in needles)]


def summary_lines(out: str) -> list[str]:
    """抽取主脚本「整理完成」之后的汇总段，作为 STAGE 4 的截图内容。"""
    lines = out.splitlines()
    for i, l in enumerate(lines):
        if "整理完成 —— 结果汇总" in l:
            return lines[i:]
    return lines[-32:]


def before_tree(root: Path) -> list[str]:
    lines = [root.name + "/"]
    items = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    for i, p in enumerate(items):
        branch = "`-- " if i == len(items) - 1 else "+-- "
        lines.append(branch + p.name + ("/" if p.is_dir() else ""))
    return lines


def layout_tree(root: Path) -> list[str]:
    """只展示工具生成的 年/月/日 层级与隔离目录，并标注每个日期目录的张数。"""
    lines = [root.name + "/"]
    years = sorted((d for d in root.iterdir()
                    if d.is_dir() and re.fullmatch(r"\d{4}", d.name)),
                   key=lambda p: p.name)
    q = root / QUAR
    tops = list(years) + ([q] if q.is_dir() else [])
    for ti, top in enumerate(tops):
        tlast = ti == len(tops) - 1
        tb = "`-- " if tlast else "+-- "
        if top == q:
            n = sum(1 for x in q.iterdir() if x.is_file())
            if (q / "_隔离清单.csv").exists():
                n -= 1
            lines.append(tb + q.name + "/   (%d 张异常，未归档)" % n)
            continue
        lines.append(tb + top.name + "/")
        pref = "    " if tlast else "|   "
        months = sorted((d for d in top.iterdir() if d.is_dir()),
                        key=lambda p: p.name)
        for mi, m in enumerate(months):
            mlast = mi == len(months) - 1
            lines.append(pref + ("`-- " if mlast else "+-- ") + m.name + "/")
            pref2 = pref + ("    " if mlast else "|   ")
            days = sorted((d for d in m.iterdir() if d.is_dir()),
                          key=lambda p: p.name)
            for di, day in enumerate(days):
                dlast = di == len(days) - 1
                n = sum(1 for x in day.iterdir() if x.is_file())
                lines.append(pref2 + ("`-- " if dlast else "+-- ") +
                             day.name + "/   (%d 张)" % n)
    return lines


def year_dirs(root: Path) -> list[Path]:
    return sorted((d for d in root.iterdir()
                   if d.is_dir() and re.fullmatch(r"\d{4}", d.name)),
                  key=lambda p: p.name)


def count_archived(root: Path) -> int:
    n = 0
    for y in year_dirs(root):
        for m in y.iterdir():
            if m.is_dir():
                for dd in m.iterdir():
                    if dd.is_dir():
                        n += sum(1 for x in dd.iterdir() if x.is_file())
    return n


def main() -> int:
    setup()
    checks: list[tuple[str, bool]] = []

    def ck(label: str, ok: bool, detail: str = "") -> None:
        ok = bool(ok)
        say("  [%s] %s%s" % ("PASS" if ok else "FAIL", label,
                            ("  -> " + detail) if detail else ""))
        checks.append((label, ok))

    # ---------------------------------------------------------------- STAGE 0
    banner(0, "环境与版本")
    rc, ver = run([MAIN, "--version"])
    say("  Python       : %s" % sys.version.split()[0])
    say("  工具版本     : %s" % ver.strip())
    say("  项目根目录   : %s" % ROOT)
    say("  演示数据目录 : %s" % DEMO)
    say("  安全边界     : 零第三方依赖、不联网、只在 _demo/ 内造数据；")
    say("                 桌面上的真实照片一张都不会被触碰。")
    pause()

    # ---------------------------------------------------------------- STAGE 1
    banner(1, "生成演示数据（含跨年 / 跨月 / 异常日期）")
    rc, out = run([MAKER])
    for l in pick(out, ["演示数据已生成", "文件总数", "其中照片", "非照片："]):
        say("  " + l.strip())
    say("  覆盖场景：正常 EXIF / 深层子目录 / 同名冲突 / 无 EXIF 兜底")
    say("            跨年 2025-12、跨月 2026-01 与 2026-03")
    say("            10 种异常日期 / 5 个非照片文件 / 空目录")
    results_ok = (rc == 0)
    say("  生成结果     : %s" % ("OK" if results_ok else "FAILED"))
    checks.append(("演示数据生成成功", results_ok))
    pause()

    # ---------------------------------------------------------------- STAGE 2
    banner(2, "整理前的目录树（照片散落在多层子文件夹里）")
    for l in before_tree(DEMO):
        say("  " + l)
    pause()

    # ---------------------------------------------------------------- STAGE 3
    banner(3, "预演 --dry-run（只计算，不改动任何文件）")
    rc, out = run([MAIN, DEMO, "--dry-run"])
    for l in pick(out, ["目录结构   :", "递归扫描完成", ">>> 一共整理了",
                        "另有 ", "预演结束"]):
        say(l if l.startswith("  ") else "  " + l)
    pre_ok = not (DEMO / "2026").exists() and not (DEMO / "2025").exists()
    say("  磁盘核查     : %s" % ("此时不存在任何年份目录，预演真的没落盘"
                                if pre_ok else "出现了意外目录，请检查"))
    checks.append(("预演不创建任何目录", pre_ok))
    pause()

    # ---------------------------------------------------------------- STAGE 4
    banner(4, "正式整理（默认 根/年/月/日 三级分层）")
    rc, out = run([MAIN, DEMO])
    for l in summary_lines(out):
        say(l)
    checks.append(("主脚本正常退出（exit 0）", rc == 0))
    pause()

    # ---------------------------------------------------------------- STAGE 5
    banner(5, "整理后的目录结构（只列出工具生成的层级）")
    for l in layout_tree(DEMO):
        say("  " + l)
    say("")
    say("  关键点：2026/02、2026/04~08、2026/10~12 等没有照片的月份均未创建；")
    say("          原有的子文件夹、视频/文档等非照片文件保持原位，不在此树中展开。")
    pause()

    # ---------------------------------------------------------------- STAGE 6
    banner(6, "自动验收（逐条 PASS / FAIL）")

    ck("根/年/月/日 三级目录已创建", (DEMO / "2026" / "09" / "01").is_dir(),
       "2026/09/01")
    ck("跨年目录 2025/12/25 已创建", (DEMO / "2025" / "12" / "25").is_dir())
    ck("跨月目录 2026/01/17 与 2026/03/08 已创建",
       (DEMO / "2026" / "01" / "17").is_dir()
       and (DEMO / "2026" / "03" / "08").is_dir())

    should_not = ["2026/02", "2026/04", "2026/05", "2026/06", "2026/07",
                  "2026/08", "2026/10", "2026/11", "2026/12",
                  "2025/01", "2025/11"]
    absent = [s for s in should_not if not (DEMO / s).exists()]
    ck("没有照片的月份一律不创建", len(absent) == len(should_not),
       "抽查 %d 个应为空的月份，%d 个确实不存在" % (len(should_not), len(absent)))

    empties: list[str] = []
    for y in year_dirs(DEMO):
        if not any(y.iterdir()):
            empties.append(str(y.relative_to(DEMO)))
        for m in y.iterdir():
            if not m.is_dir():
                continue
            if not any(m.iterdir()):
                empties.append(str(m.relative_to(DEMO)))
            for dd in m.iterdir():
                if dd.is_dir() and not any(dd.iterdir()):
                    empties.append(str(dd.relative_to(DEMO)))
    ck("工具生成的日期目录链中没有空文件夹", not empties,
       ("空目录: " + ", ".join(empties)) if empties else "0 个空目录")

    n_arch = count_archived(DEMO)
    ck("归档照片共 15 张", n_arch == 15, "实际 %d 张" % n_arch)

    q = DEMO / QUAR
    n_q = sum(1 for x in q.iterdir()
              if x.is_file() and x.name != "_隔离清单.csv")
    ck("10 张日期异常照片被隔离且未归档", n_q == 10, "实际 %d 张" % n_q)
    ck("隔离清单 CSV 已生成", (q / "_隔离清单.csv").is_file())

    d0901 = DEMO / "2026" / "09" / "01"
    ck("同名文件自动加后缀、不覆盖",
       (d0901 / "IMG_0001.jpg").is_file()
       and (d0901 / "IMG_0001_2.jpg").is_file())

    ck("非照片文件保持原位",
       all((DEMO / f).is_file() for f in
           ["说明.txt", "宣传视频.mp4", "素材备份.zip", "子文件夹A/稿件.docx"]))

    reports = sorted(DEMO.glob("照片整理报告_*.md"))
    ck("Markdown 报告含「实际生成的目录结构」章节",
       bool(reports)
       and "## 实际生成的目录结构" in reports[-1].read_text(encoding="utf-8"))

    rc2, out2 = run([MAIN, DEMO])
    idemp = (re.search(r"本次移动成功\s*:\s*0\s*张", out2) is not None
             and re.search(r"已在正确日期文件夹（跳过）\s*:\s*15\s*张", out2) is not None)
    ck("重复运行不重复搬运（0 移动 / 15 已在位）", idemp)

    if FLAT.exists():
        shutil.rmtree(str(FLAT))
    FLAT.mkdir(parents=True)
    shutil.copy2(str(d0901 / "IMG_0001.jpg"), str(FLAT / "兼容测试.jpg"))
    run([MAIN, FLAT, "--layout", "flat"])
    flat_ok = ((FLAT / "2026-09-01" / "兼容测试.jpg").is_file()
               and not (FLAT / "2026").exists())
    ck("--layout flat 仍能生成单层 YYYY-MM-DD 目录", flat_ok)
    shutil.rmtree(str(FLAT), ignore_errors=True)

    passed = sum(1 for _, ok in checks if ok)
    say("")
    say("  " + "-" * 60)
    say("  验收结果：%d / %d 项通过" % (passed, len(checks)))
    say("  " + "-" * 60)
    pause()

    # ---------------------------------------------------------------- STAGE 7
    banner(7, "演示完成 —— 截图与上传建议")
    say("  建议保存这 3 张截图（放进 GitHub 仓库的 docs/ 或 README）：")
    say("    1) STAGE 4：运行结果汇总，含 >>> 一共整理了 15 张照片 <<<")
    say("    2) STAGE 5：目录树，展示跨年分层与「无照片的月份不创建」")
    say("    3) STAGE 6：验收结果，%d / %d 项全部 PASS" % (passed, len(checks)))
    say("")
    say("  本次生成的报告与日志：")
    for l in pick(out, ["完整日志已保存", "结构化报告已保存", "隔离清单已保存"]):
        say("    " + l.strip())
    say("")
    say("  README 里的示例报告：examples/运行报告示例.md")
    say("  全部 PASS 之后即可 git add / commit / push 上传 GitHub。")
    say("")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
