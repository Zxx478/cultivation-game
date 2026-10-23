"""端到端验证：「今日行事」面板的耗时是否随境界自动变化、是否自动刷新。

用临时数据目录 + 临时端口，绝不碰 data/game.db。

验收点：
  1. 锻血档（1 个月 / 30 日）下五类行事的耗时与配置一致
  2. 面板把换算口径写出来（基础日数 × 本境档位 ÷ 30）
  3. 「连闭关」合计日数随次数联动
  4. 境界升到辟府（3 个月 / 90 日）后，不刷新页面也能自动更新
  5. 刷新页面后仍是新档位（服务端权威）
  6. 全文不得残留写死的「三十日」之类旧文案
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(r"D:/ai工坊/cultivation-game")
PY = r"C:/Users/周/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
PORT = 5098

tmp = Path(tempfile.mkdtemp(prefix="htj-action-"))
data_dir = tmp / "data"
data_dir.mkdir(parents=True)
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


def raise_realm_to_open_palace() -> int:
    """把角色推到第二境（辟府）：本境档位 3 个月 / 90 日。"""
    conn = sqlite3.connect(str(db_path), timeout=10)
    try:
        row = conn.execute("SELECT id FROM character ORDER BY id DESC LIMIT 1").fetchone()
        assert row, "没有创建出角色"
        char_id = int(row[0])
        conn.execute(
            "UPDATE character SET realm_index = 2, realm_stage = 1, level = 10, "
            "cultivation = 0, lifespan_max_days = 54000 WHERE id = ?",
            (char_id,),
        )
        conn.commit()
        return char_id
    finally:
        conn.close()


def clear_pending_event() -> None:
    """行动有概率掷出事件；测时间口径时不希望被浮层挡住，直接清账。"""
    conn = sqlite3.connect(str(db_path), timeout=10)
    try:
        conn.execute("UPDATE character SET pending_event = ''")
        conn.commit()
    finally:
        conn.close()


def close_event_if_open(page) -> None:
    """事件层若已弹出，用真实关闭按钮收起（不绕过 UI）。"""
    if page.locator("#layer-event").is_visible():
        page.click("#event-close")
        page.wait_for_timeout(300)


results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f" :: {detail}" if detail else ""))


def cost_of(page, kind: str) -> str:
    return page.inner_text(f'[data-cost="{kind}"]').strip()


try:
    wait_http(f"http://127.0.0.1:{PORT}/api/v1/meta")
    print("服务已就绪", f"http://127.0.0.1:{PORT}")

    from playwright.sync_api import sync_playwright

    shots = tmp / "shots"
    shots.mkdir()

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 1000})
        page.on("console", lambda msg: print(f"  [console.{msg.type}] {msg.text}")
                if msg.type in ("error",) else None)
        page.goto(f"http://127.0.0.1:{PORT}/#/home", wait_until="networkidle")

        # ---- 创角并进洞府 ----
        page.wait_for_selector("#pane-home:not([hidden])", timeout=15000)
        page.click("#home-start")
        page.wait_for_selector("#pane-creation:not([hidden])", timeout=15000)
        page.wait_for_selector("#reroll-left", timeout=15000)
        page.fill("#f-name", "验时者")
        for key, times in (("str", 3), ("con", 2)):
            plus = f'#points-list .points__row[data-key="{key}"] [data-step="1"]'
            for _ in range(times):
                page.click(plus)
        page.click("#creation-next")
        page.wait_for_selector('#creation-form .creation__step[data-step="2"]:not([hidden])', timeout=10000)
        page.click("#creation-submit")
        page.wait_for_selector("#pane-main:not([hidden])", timeout=20000)
        page.wait_for_timeout(300)
        check("创角并进入洞府", True, page.url)
        page.screenshot(path=str(shots / "01-main-forging-blood.png"))

        # ---- 锻血档：1 个月 / 30 日 ----
        tier = page.inner_text("#action-tier").strip()
        tier_days = page.inner_text("#action-tier-days").strip()
        check("本境档位显示为 1 个月", tier == "1 个月", tier)
        check("一档日数显示为 30 日", "30 日" in tier_days, tier_days)

        expected_r1 = {"seclusion": "30 日", "adventure": "15 日", "gather": "10 日",
                       "spar": "3 日", "meditate": "10 日"}
        actual_r1 = {kind: cost_of(page, kind) for kind in expected_r1}
        check("五类行事耗时与锻血档一致", actual_r1 == expected_r1, str(actual_r1))

        repeat_cost = cost_of(page, "repeat")
        check("连闭关默认 3 次合计 90 日", "90 日" in repeat_cost, repeat_cost)

        law = page.inner_text("#action-law").strip()
        print("规则文案 =", repr(law))
        check("口径写成「基础日数 × 档位 ÷ 30」", "÷ 30" in law and "本境倍率" in law, law)
        check("口径声明会随境界重算", "自动重算" in law, law)

        hint = page.inner_text("#action-hint").strip()
        check("提示里的闭关日数取自本境", "30 日" in hint, hint)
        check("提示不再写死「三十日」", "三十日" not in hint, hint)

        rows = page.locator("#action-list .actionlist__item").count()
        check("耗时细目覆盖五类行事 + 冲境", rows == 6, f"{rows} 行")
        breakthrough_days = page.inner_text("#action-list .actionlist__item:last-child .actionlist__days").strip()
        check("冲境耗时同样按档位缩放", "3 日" in breakthrough_days, breakthrough_days)

        # ---- 次数联动 ----
        page.select_option("#repeat-count", "5")
        page.wait_for_timeout(150)
        repeat_cost = cost_of(page, "repeat")
        check("改次数后合计日数联动（30×5=150）", "150 日" in repeat_cost, repeat_cost)
        page.select_option("#repeat-count", "3")
        page.wait_for_timeout(150)

        # ---- 境界提升：不刷新页面，面板必须自己更新 ----
        page.evaluate("window.__noReload = 'kept'")
        char_id = raise_realm_to_open_palace()
        print("角色 id =", char_id, "→ 已升入辟府档")

        # 用一次真实行动触发状态刷新（与突破结算走同一条 store.setState → emit('state') 路径）
        page.click('#actionbar button.action[data-action="meditate"]')
        page.wait_for_function(
            "() => document.querySelector('#action-tier')?.textContent?.trim() === '3 个月'",
            timeout=15000)
        page.wait_for_timeout(300)

        check("升境后无需刷新即显示 3 个月", page.inner_text("#action-tier").strip() == "3 个月")
        check("期间页面未重载", page.evaluate("window.__noReload || 'lost'") == "kept")

        expected_r2 = {"seclusion": "90 日", "adventure": "45 日", "gather": "30 日",
                       "spar": "9 日", "meditate": "30 日"}
        actual_r2 = {kind: cost_of(page, kind) for kind in expected_r2}
        check("升境后五类耗时整体变为 3 倍", actual_r2 == expected_r2, str(actual_r2))
        check("升境后一档日数为 90 日", "90 日" in page.inner_text("#action-tier-days"), "")

        repeat_cost = cost_of(page, "repeat")
        check("升境后连闭关合计 270 日", "270 日" in repeat_cost, repeat_cost)

        law_r2 = page.inner_text("#action-law").strip()
        hit = "×3" in law_r2 and "90 日" in law_r2
        check("升境后口径倍率改为 ×3", hit, law_r2)
        page.screenshot(path=str(shots / "02-main-open-palace.png"))

        # ---- 刷新后仍是新档位（服务端权威，不靠前端记忆）----
        # 行动有概率掷出事件，会遮住面板并禁用按钮；先按真实按钮收起，再清账后重载
        close_event_if_open(page)
        clear_pending_event()
        page.reload(wait_until="networkidle")
        page.wait_for_selector("#pane-main:not([hidden])", timeout=15000)
        page.wait_for_timeout(600)
        check("刷新后档位仍为 3 个月", page.inner_text("#action-tier").strip() == "3 个月")
        check("刷新后闭关仍为 90 日", cost_of(page, "seclusion") == "90 日", cost_of(page, "seclusion"))
        hint_r2 = page.inner_text("#action-hint").strip()
        check("刷新后提示改为 90 日", "90 日" in hint_r2, hint_r2)

        # ---- 全屏文案不得残留旧刻度 ----
        body = page.inner_text("#action-card")
        stale = [token for token in ("闭关三十日", "闭关一日", "十五日", "闭关一日三十天") if token in body]
        check("面板无残留的写死日数", not stale, f"残留={stale}")
        page.screenshot(path=str(shots / "03-after-reload.png"))

        # ---- 切走再回来：订阅须解绑重挂，面板照常可用 ----
        page.click('[data-nav="character"]')
        page.wait_for_selector("#pane-character:not([hidden])", timeout=10000)
        page.wait_for_timeout(200)
        page.click('[data-nav="main"]')
        page.wait_for_selector("#pane-main:not([hidden])", timeout=10000)
        page.wait_for_timeout(300)
        check("切回洞府后档位仍正确", page.inner_text("#action-tier").strip() == "3 个月")
        check("切回洞府后耗时仍正确", cost_of(page, "seclusion") == "90 日", cost_of(page, "seclusion"))

        # 来回切换后再打坐一次，耗时只能扣一次（防重复绑定导致双提交）
        page.click('#actionbar button.action[data-action="meditate"]')
        page.wait_for_timeout(1500)
        toast_text = page.inner_text("#toasts .toast__msg") if page.locator("#toasts .toast__msg").count() else ""
        print("结算提示 =", repr(toast_text))
        check("来回切换后仍可正常结算", "30 日" in toast_text, toast_text)

        # ---- 窄屏：细目说明换行后不得溢出 ----
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(400)
        overflow = page.evaluate(
            "() => { const el = document.querySelector('#action-card');"
            " return el ? el.scrollWidth - el.clientWidth : 0; }")
        check("窄屏下今日行事不横向溢出", overflow <= 1, f"超出 {overflow}px")
        page.screenshot(path=str(shots / "04-mobile.png"))

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
