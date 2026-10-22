"""端到端验证：事件层「关闭 / 继续」是否真的可用，效果文案是否中文化。

用临时数据目录 + 临时端口，绝不碰 data/game.db。
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(r"D:/ai工坊/cultivation-game")
PY = r"C:/Users/周/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
PORT = 5099

tmp = Path(tempfile.mkdtemp(prefix="htj-verify-"))
data_dir = tmp / "data"
(data_dir).mkdir(parents=True)
shutil.copytree(ROOT / "data" / "content", data_dir / "content")
db_path = data_dir / "game.db"

env = dict(os.environ)
env["HUANGTIANJI_DATA"] = str(data_dir)
env["PORT"] = str(PORT)
env["PYTHONIOENCODING"] = "utf-8"

log = open(tmp / "server.log", "w", encoding="utf-8")
proc = subprocess.Popen([PY, "run.py"], cwd=str(ROOT), env=env,
                        stdout=log, stderr=subprocess.STDOUT)


def wait_http(url: str, timeout: float = 40.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as resp:
                if resp.status == 200:
                    return
        except Exception:
            time.sleep(0.4)
    raise RuntimeError(f"服务未就绪：{url}")


def inject_pending(instance_id: str = "verify-1") -> int:
    payload = json.dumps({
        "instance_id": instance_id,
        # 灵脉现世 / leave：必定成功且给「悟性 +1」，用来验证属性加成的中文显示
        "event_id": "ling_mai_xian_shi",
        "created_at_days": 40,
    }, ensure_ascii=False)
    conn = sqlite3.connect(str(db_path), timeout=10)
    try:
        row = conn.execute("SELECT id FROM character ORDER BY id DESC LIMIT 1").fetchone()
        assert row, "没有创建出角色"
        char_id = int(row[0])
        conn.execute("UPDATE character SET pending_event = ? WHERE id = ?", (payload, char_id))
        conn.commit()
        return char_id
    finally:
        conn.close()


results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f" :: {detail}" if detail else ""))


try:
    wait_http(f"http://127.0.0.1:{PORT}/api/v1/meta")
    print("服务已就绪", f"http://127.0.0.1:{PORT}")

    from playwright.sync_api import sync_playwright

    shots = tmp / "shots"
    shots.mkdir()

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.on("console", lambda msg: print(f"  [console.{msg.type}] {msg.text}")
                if msg.type in ("error", "warning") else None)
        page.goto(f"http://127.0.0.1:{PORT}/#/home", wait_until="networkidle")

        # ---- 创角 ----
        page.wait_for_selector("#pane-home:not([hidden])", timeout=15000)
        page.click("#home-start")
        page.wait_for_selector("#pane-creation:not([hidden])", timeout=15000)
        page.wait_for_selector("#reroll-left", timeout=15000)
        page.fill("#f-name", "验证者")
        for key, times in (("str", 3), ("con", 2)):
            plus = f'#points-list .points__row[data-key="{key}"] [data-step="1"]'
            for _ in range(times):
                page.click(plus)
        page.click("#creation-next")
        page.wait_for_selector('#creation-form .creation__step[data-step="2"]:not([hidden])', timeout=10000)
        page.click("#creation-submit")
        page.wait_for_selector("#pane-main:not([hidden])", timeout=20000)
        check("创角并进入洞府", True, page.url)
        page.screenshot(path=str(shots / "01-main.png"))

        # ---- 注入一桩待决事件，刷新后应自动弹出 ----
        char_id = inject_pending()
        print("角色 id =", char_id)
        page.reload(wait_until="networkidle")
        page.wait_for_selector("#layer-event:not([hidden])", timeout=15000)
        title = page.inner_text("#event-title")
        check("刷新后自动弹出事件层", bool(title.strip()), f"标题={title!r}")
        check("标题为中文", any("\u4e00" <= ch <= "\u9fff" for ch in title), title)
        n_choices = page.locator("#event-choices button.choice").count()
        check("选项按钮已渲染", n_choices >= 2, f"{n_choices} 个选项")
        page.screenshot(path=str(shots / "02-event-open.png"))

        # ---- 关闭按钮 ----
        has_close = page.locator("#event-close").count() == 1
        check("事件层有独立关闭按钮", has_close)
        page.click("#event-close")
        page.wait_for_timeout(300)
        hidden = page.locator("#layer-event").is_hidden()
        check("点击关闭后事件层收起", hidden)
        chip_visible = page.locator("#event-resume").is_visible()
        check("收起后出现「续」入口", chip_visible)
        page.screenshot(path=str(shots / "03-dismissed.png"))

        # ---- 再同步不得强行弹回 ----
        page.evaluate("document.dispatchEvent(new CustomEvent('game:sync'))")
        page.wait_for_timeout(400)
        check("同步后不会被强行弹回", page.locator("#layer-event").is_hidden())

        # ---- 「续」入口可返回 ----
        page.click("#event-resume")
        page.wait_for_selector("#layer-event:not([hidden])", timeout=5000)
        check("点击「续」可重新打开", page.locator("#layer-event").is_visible())
        check("事件层打开时「续」入口隐藏", page.locator("#event-resume").is_hidden())

        # ---- 选一个必成且带属性加成的选项（leave：悟性 +1 / 气血 -3%）----
        page.locator("#event-choices button.choice").nth(2).click()
        page.wait_for_function(
            "() => { const c = document.querySelector('#event-continue'); return c && !c.hidden; }",
            timeout=15000)
        deltas = page.inner_text("#event-result-deltas")
        print("效果行文本 =", repr(deltas))
        check("结算后「继续」按钮出现", page.locator("#event-continue").is_visible())

        ascii_tokens = [t for t in deltas.replace("\n", " ").split()
                        if any(ch.isascii() and ch.isalpha() for ch in t)]
        check("效果行无英文键名", not ascii_tokens, f"疑似={ascii_tokens}")
        check("属性加成显示为中文", "悟性" in deltas, deltas)
        check("未泄露成功率字段", "chance" not in deltas.lower())

        kept = page.locator("#event-choices button.choice .choice__label").count()
        check("选项结构未被压平", kept == n_choices, f"保留 {kept}/{n_choices}")
        page.screenshot(path=str(shots / "04-result.png"))

        # ---- 继续 → 关闭 ----
        page.click("#event-continue")
        page.wait_for_timeout(1200)
        check("点击「继续」后事件层关闭", page.locator("#layer-event").is_hidden())
        check("「续」入口同时消失", page.locator("#event-resume").is_hidden())
        page.screenshot(path=str(shots / "05-closed.png"))

        # ---- 刷新确认服务端已清账 ----
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(1200)
        check("刷新后不再弹回（服务端已清事件）", page.locator("#layer-event").is_hidden())

        # ---- 角色页基础属性不得出现内部键名（mnd）----
        page.goto(f"http://127.0.0.1:{PORT}/#/character", wait_until="networkidle")
        page.wait_for_timeout(900)
        char_text = page.inner_text("#pane-character")
        leaked = [tok for tok in ("mnd", "dao_heart", "str", "con", "wis")
                  if tok in char_text]
        check("角色页属性名全中文", not leaked, f"泄露={leaked}")
        page.screenshot(path=str(shots / "06-character.png"))

        browser.close()

    print("\n截图目录：", shots)
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    log.close()

failed = [r for r in results if not r[1]]
print("\n==== 合计 %d 项，失败 %d 项 ====" % (len(results), len(failed)))
for name, ok, detail in results:
    print(("  ✅ " if ok else "  ❌ ") + name + (f"  ({detail})" if detail and not ok else ""))
sys.exit(1 if failed else 0)
