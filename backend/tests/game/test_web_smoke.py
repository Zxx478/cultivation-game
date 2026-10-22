"""工程包 C · Web 冒烟测试。

不依赖后端是否已挂载模板：直接读 ``backend/app/web`` 下的文件做静态断言，
并用 Jinja2 渲染一次 ``index.html``（假 context），确保模板语法与 include 路径无误。

覆盖：
- index.html 含 ≥12 个 ``data-pane``，且 12 个主界面 + 3 个浮层齐备；
- 静态资源（CSS/JS/每个 pane 模块）存在且非空；
- 首屏无任何 http(s) 外链、无 CDN、无外链字体/图片；
- 创角末页含「宗门 / 方向 / 散修」三关键词（R05）；
- 前端不用 ``innerHTML`` 拼接（XSS 基线，R109）；
- 双主题变量与响应式断点存在。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WEB_DIR = Path(__file__).resolve().parents[3] / "backend" / "app" / "web"
TEMPLATE_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"
INDEX_PATH = TEMPLATE_DIR / "index.html"

MAIN_PANES = [
    "home",
    "creation",
    "main",
    "character",
    "cultivation",
    "techniques",
    "inventory",
    "quests",
    "map",
    "sect",
    "forbidden",
    "saves",
    "logs",
]
OVERLAY_PANES = ["event", "breakthrough", "combat"]

PANE_MODULES = MAIN_PANES + OVERLAY_PANES
CORE_MODULES = ["api", "state", "router", "ui", "app", "actions"]


@pytest.fixture(scope="module")
def index_html() -> str:
    assert INDEX_PATH.is_file(), f"缺少首屏模板：{INDEX_PATH}"
    return INDEX_PATH.read_text(encoding="utf-8")


def expand_includes(source: str) -> str:
    """不依赖 Jinja2 的 include 展开：把 {% include "panes/x.html" %} 换成文件内容。

    后端 main.py 尚未挂载模板时，用它得到「首屏最终 HTML」以便断言。
    """
    pattern = re.compile(r'{%\s*include\s+"(panes/[a-z_]+\.html)"\s*%}')

    def replace(match: re.Match) -> str:
        path = TEMPLATE_DIR / match.group(1)
        assert path.is_file(), f"include 指向的文件不存在：{path}"
        return path.read_text(encoding="utf-8")

    return pattern.sub(replace, source)


def test_web_dirs_exist() -> None:
    assert WEB_DIR.is_dir(), "缺少 backend/app/web 目录"
    assert TEMPLATE_DIR.is_dir(), "缺少 templates 目录"
    assert STATIC_DIR.is_dir(), "缺少 static 目录"


def test_index_declares_at_least_12_panes(index_html: str) -> None:
    full = expand_includes(index_html)
    found = set(re.findall(r'data-pane="([a-z_]+)"', full))
    assert len(found) >= 12, f"data-pane 仅 {len(found)} 个：{sorted(found)}"
    for pane in MAIN_PANES:
        assert pane in found, f"首屏缺少 {pane} pane 容器"


def test_index_includes_all_main_and_overlay_panes(index_html: str) -> None:
    includes = set(re.findall(r'{%\s*include\s+"panes/([a-z_]+)\.html"\s*%}', index_html))
    for pane in MAIN_PANES + OVERLAY_PANES:
        assert pane in includes, f"index.html 未 include panes/{pane}.html"

    rendered = "\n".join(
        (TEMPLATE_DIR / "panes" / f"{pane}.html").read_text(encoding="utf-8")
        for pane in MAIN_PANES + OVERLAY_PANES
    )
    for pane in MAIN_PANES + OVERLAY_PANES:
        assert f'data-pane="{pane}"' in rendered, f"缺少 data-pane=\"{pane}\" 容器"


def test_main_content_container_present(index_html: str) -> None:
    assert 'id="main"' in index_html
    assert "<main" in index_html
    # 12 个主界面必须落在 #main 之内（在 index 的 include 区块中）
    first = index_html.index('<main')
    last = index_html.index("</main>")
    inner = index_html[first:last]
    for pane in MAIN_PANES:
        assert f"panes/{pane}.html" in inner, f"{pane} 未挂在 #main 内"


def test_static_assets_exist() -> None:
    expected = [
        STATIC_DIR / "css" / "theme.css",
        STATIC_DIR / "css" / "main.css",
    ]
    expected += [STATIC_DIR / "js" / "core" / f"{name}.js" for name in CORE_MODULES]
    expected += [STATIC_DIR / "js" / "panes" / f"{name}.js" for name in PANE_MODULES]

    for path in expected:
        assert path.is_file(), f"缺少静态资源：{path}"
        assert path.stat().st_size > 200, f"静态资源内容过少：{path}"


def test_index_references_local_assets_only(index_html: str) -> None:
    assert "/static/css/theme.css" in index_html
    assert "/static/css/main.css" in index_html
    assert "/static/js/core/app.js" in index_html
    assert 'type="module"' in index_html


def test_no_external_network_references() -> None:
    """首屏零外域请求：模板、CSS、JS 都不许出现 http(s) 外链。"""
    targets = [INDEX_PATH]
    targets += sorted(TEMPLATE_DIR.rglob("*.html"))
    targets += sorted(STATIC_DIR.rglob("*.css"))
    targets += sorted(STATIC_DIR.rglob("*.js"))

    pattern = re.compile(r"https?://", re.IGNORECASE)
    for path in targets:
        content = path.read_text(encoding="utf-8")
        hits = pattern.findall(content)
        assert not hits, f"{path.name} 含外链：{hits[:3]}"

    for path in sorted(STATIC_DIR.rglob("*.css")):
        css = path.read_text(encoding="utf-8")
        assert "@import" not in css, f"{path.name} 使用 @import，禁止外链字体"
        assert not re.search(r"url\(\s*['\"]?https?", css, re.IGNORECASE)


def test_creation_final_page_mentions_three_paths() -> None:
    """R05：创角末页须同时出现「宗门」「方向」「散修」。"""
    creation = (TEMPLATE_DIR / "panes" / "creation.html").read_text(encoding="utf-8")
    for keyword in ("宗门", "方向", "散修"):
        assert keyword in creation, f"创角末页缺少关键词：{keyword}"


def test_no_innerhtml_assignments() -> None:
    """R109：前端一律用 textContent / createElement，禁止 innerHTML 拼接。"""
    violations = []
    for path in sorted((STATIC_DIR / "js").rglob("*.js")):
        content = path.read_text(encoding="utf-8")
        if re.search(r"\.innerHTML\s*=", content) or "insertAdjacentHTML" in content:
            violations.append(path.name)
    assert not violations, f"发现 innerHTML 拼接：{violations}"


def test_theme_tokens_for_both_themes() -> None:
    theme = (STATIC_DIR / "css" / "theme.css").read_text(encoding="utf-8")
    for token in ("--ink-0", "--paper-0", "--cinnabar", "--jade", "--gold", "--line"):
        assert token in theme, f"缺少设计变量 {token}"
    assert '[data-theme="paper"]' in theme, "缺少宣纸主题"
    assert "prefers-reduced-motion" in theme, "缺少动效降级"


def test_responsive_breakpoints_present() -> None:
    main_css = (STATIC_DIR / "css" / "main.css").read_text(encoding="utf-8")
    assert "@media (max-width: 768px)" in main_css, "缺少移动断点"
    assert "@media (max-width: 1280px)" in main_css, "缺少日志抽屉断点"
    assert ".tabbar" in main_css, "缺少底部 Tab"
    assert "float--crit" in main_css and "float--dodge" in main_css, "缺少伤害飘字样式"


def test_key_ui_hooks_present() -> None:
    """交互骨架：状态条、Toast、事件选项、战斗按钮、突破明细。"""
    index_html = INDEX_PATH.read_text(encoding="utf-8")
    for hook in ('id="toasts"', 'id="log-list"', 'id="nav-list"', 'id="tabbar"', 'id="theme-toggle"'):
        assert hook in index_html, f"index.html 缺少 {hook}"

    required = {
        "panes/home.html": ['id="home-title"', 'id="home-start"', 'id="home-continue"',
                            'id="home-save-list"', 'id="home-time-body"'],
        "panes/main.html": ['data-bar="life"', 'data-action="seclusion"', 'data-action="meditate"'],
        "panes/event.html": ['id="event-choices"', 'id="event-continue"'],
        "panes/combat.html": ['data-caction="attack"', 'data-caction="flee"', 'id="float-layer"'],
        "panes/breakthrough.html": ['id="bt-items"', 'id="bt-final"', 'id="bt-start"'],
        "panes/saves.html": ['id="save-slots"', 'id="import-text"', 'id="import-file"'],
    }
    for name, hooks in required.items():
        content = (TEMPLATE_DIR / name).read_text(encoding="utf-8")
        for hook in hooks:
            assert hook in content, f"{name} 缺少 {hook}"


def test_index_renders_with_jinja2() -> None:
    """用假 context 渲染一次，确保 include 路径与语法无误。"""
    jinja2 = pytest.importorskip("jinja2", reason="未安装 jinja2，跳过模板渲染断言")
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)
    template = env.get_template("index.html")
    html = template.render()

    assert 'data-pane="main"' in html
    assert 'data-pane="combat"' in html
    assert "/static/js/core/app.js" in html
    assert len(re.findall(r'data-pane="([a-z_]+)"', html)) >= 12
    assert "http://" not in html and "https://" not in html
