"""使用 Playwright 打开搜索页并保存截图。"""

from __future__ import annotations

import argparse
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。"""

    parser = argparse.ArgumentParser(
        description="使用 Playwright 打开本地文档搜索助手页面并保存截图。"
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:5000",
        help="本地 Web 服务地址，默认 http://127.0.0.1:5000",
    )
    parser.add_argument(
        "--query",
        default="历史",
        help="需要在首页输入并搜索的关键词，默认 历史",
    )
    parser.add_argument(
        "--output",
        default="artifacts/search-history-playwright.png",
        help="截图输出路径，默认 artifacts/search-history-playwright.png",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=1440,
        help="浏览器视口宽度，默认 1440",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=1800,
        help="浏览器视口高度，默认 1800",
    )
    parser.add_argument(
        "--wait-ms",
        type=int,
        default=1200,
        help="页面稳定后的额外等待时间（毫秒），默认 1200",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="以有界面模式启动浏览器，便于人工观察交互过程",
    )
    return parser


def capture_search_page(
    *,
    base_url: str,
    query: str,
    output_path: Path,
    width: int,
    height: int,
    wait_ms: int,
    headed: bool,
) -> None:
    """打开首页执行一次搜索，并将结果页保存为截图。"""

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "当前环境未安装 Playwright。请先执行 `uv sync --extra dev`，"
            "然后执行 `uv run playwright install chromium`。"
        ) from exc

    normalized_base_url = base_url.rstrip("/")
    search_page_url = f"{normalized_base_url}/search"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not headed)
        page = browser.new_page(viewport={"width": width, "height": height})
        try:
            page.goto(search_page_url, wait_until="domcontentloaded")
            page.locator("#query-input").wait_for(state="visible", timeout=10_000)
            page.locator("#query-input").fill(query)
            page.get_by_role("button", name="搜索").click()

            # 等待搜索结果页稳定，避免截到表单提交中的中间状态。
            page.wait_for_load_state("networkidle")
            page.locator("main").wait_for(state="visible", timeout=10_000)
            page.wait_for_timeout(wait_ms)
            page.screenshot(path=str(output_path), full_page=True)
        except PlaywrightTimeoutError as exc:
            raise RuntimeError(
                "Playwright 在等待页面稳定时超时。"
                "请确认 Web 服务已经启动，并且页面可以正常完成搜索。"
            ) from exc
        finally:
            browser.close()


def main() -> None:
    """脚本入口。"""

    args = build_parser().parse_args()
    output_path = Path(args.output).resolve()
    capture_search_page(
        base_url=args.base_url,
        query=args.query,
        output_path=output_path,
        width=args.width,
        height=args.height,
        wait_ms=args.wait_ms,
        headed=args.headed,
    )
    print(f"截图已保存：{output_path}")


if __name__ == "__main__":
    main()
