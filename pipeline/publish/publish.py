"""
publish.py — 预览 + 发布到小红书

流程：
  1. 接收确认后的文案（===图1=== 格式）
  2. 匹配/指定照片
  3. 渲染所有图片
  4. 预览（打开 Finder 查看）
  5. 确认后通过浏览器自动化发布到小红书

用法：
  python pipeline/publish/publish.py --preview draft.txt
  python pipeline/publish/publish.py --publish draft.txt
"""

import json
import os
import re
import sys
import shutil
import subprocess
import logging

from dotenv import load_dotenv

_PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, _PROJECT_ROOT)
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")

PREVIEW_DIR = os.path.join(_PROJECT_ROOT, "output", "preview")
PUBLISH_DIR = os.path.join(_PROJECT_ROOT, "output", "publish")


def parse_slides(post_text):
    """解析 ===图N=== 格式的文案"""
    slides = []
    parts = re.split(r"===图(\d+)===", post_text)
    for i in range(1, len(parts), 2):
        num = int(parts[i])
        text = parts[i + 1].strip() if i + 1 < len(parts) else ""
        slides.append({"num": num, "text": text})
    return slides


def split_slide_text(text):
    """把一张图的文案拆分为大标题和小字注释"""
    lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
    if not lines:
        return "", ""

    main, sub, chars = [], [], 0
    for i, line in enumerate(lines):
        chars += len(line)
        if chars <= 30 and i < 2:
            main.append(line)
        else:
            sub.append(line)

    if not main:
        main, sub = [lines[0]], lines[1:]

    return "\n".join(main), "\n".join(sub)


def preview_post(post_text, photo_paths=None):
    """
    预览帖子：渲染所有图片并打开 Finder

    Args:
        post_text: 帖子文案（===图1=== 格式）
        photo_paths: dict {slide_num: photo_path}，None则用纯文字卡片

    Returns:
        list of rendered image paths
    """
    from pipeline.images.render_post import render_slide, render_text_card, render_cover

    slides = parse_slides(post_text)
    if not slides:
        print("无法解析文案，请确认格式：===图1===")
        return []

    # 清理预览目录
    if os.path.exists(PREVIEW_DIR):
        shutil.rmtree(PREVIEW_DIR)
    os.makedirs(PREVIEW_DIR, exist_ok=True)

    rendered = []
    for s in slides:
        main_text, sub_text = split_slide_text(s["text"])
        out_path = os.path.join(PREVIEW_DIR, f"slide_{s['num']:02d}.jpg")

        photo = photo_paths.get(s["num"]) if photo_paths else None

        is_cover = (s["num"] == 1)
        if photo and os.path.exists(photo):
            path = render_slide(photo, main_text, sub_text, out_path, is_cover=is_cover)
        elif is_cover:
            # 封面没配图也用纯文字封面风格
            path = render_text_card(main_text, sub_text, out_path)
        else:
            path = render_text_card(main_text, sub_text, out_path)

        rendered.append(path)
        print(f"  图{s['num']}: {main_text[:30]}... → {os.path.basename(path)}")

    # 打开 Finder 预览
    subprocess.run(["open", PREVIEW_DIR])
    print(f"\n预览目录已打开: {PREVIEW_DIR}")
    print(f"共 {len(rendered)} 张图片，请检查后确认发布")

    return rendered


def prepare_publish(post_text, photo_paths=None, title="", description=""):
    """
    准备发布：渲染图片 + 生成发布信息

    Args:
        post_text: 帖子文案
        photo_paths: 配图路径
        title: 帖子标题（小红书标题）
        description: 帖子正文描述

    Returns:
        dict with publish info
    """
    from pipeline.images.render_post import render_slide, render_text_card

    slides = parse_slides(post_text)

    if os.path.exists(PUBLISH_DIR):
        shutil.rmtree(PUBLISH_DIR)
    os.makedirs(PUBLISH_DIR, exist_ok=True)

    rendered = []
    for s in slides:
        main_text, sub_text = split_slide_text(s["text"])
        out_path = os.path.join(PUBLISH_DIR, f"slide_{s['num']:02d}.jpg")

        photo = photo_paths.get(s["num"]) if photo_paths else None

        is_cover = (s["num"] == 1)
        if photo and os.path.exists(photo):
            path = render_slide(photo, main_text, sub_text, out_path, is_cover=is_cover)
        elif is_cover:
            # 封面没配图也用纯文字封面风格
            path = render_text_card(main_text, sub_text, out_path)
        else:
            path = render_text_card(main_text, sub_text, out_path)
        rendered.append(path)

    publish_info = {
        "title": title,
        "description": description,
        "images": rendered,
        "slide_count": len(rendered),
    }

    # 保存发布信息
    info_path = os.path.join(PUBLISH_DIR, "publish_info.json")
    with open(info_path, "w", encoding="utf-8") as f:
        json.dump(publish_info, f, ensure_ascii=False, indent=2)

    return publish_info


async def publish_to_xiaohongshu(publish_info):
    """
    通过 Playwright 浏览器自动化发布到小红书

    Args:
        publish_info: prepare_publish 返回的发布信息
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("请先安装 playwright: pip install playwright && playwright install chromium")
        return False

    images = publish_info["images"]
    title = publish_info["title"]
    desc = publish_info.get("description", "")

    async with async_playwright() as p:
        # 使用持久化的浏览器上下文（保持登录状态）
        user_data = os.path.join(_PROJECT_ROOT, ".browser_data")
        browser = await p.chromium.launch_persistent_context(
            user_data,
            headless=False,
            viewport={"width": 1280, "height": 900},
        )

        page = browser.pages[0] if browser.pages else await browser.new_page()

        # 打开小红书创作者中心
        await page.goto("https://creator.xiaohongshu.com/publish/publish")
        print("已打开小红书发布页面")
        print("如果需要登录，请手动扫码登录，然后按回车继续...")
        input()

        # 上传图片
        upload_input = page.locator('input[type="file"]')
        await upload_input.set_input_files(images)
        print(f"已上传 {len(images)} 张图片")

        # 等待上传完成
        await page.wait_for_timeout(3000)

        # 填写标题
        if title:
            title_input = page.locator('[placeholder*="标题"]')
            await title_input.fill(title)

        # 填写正文
        if desc:
            desc_input = page.locator('[placeholder*="正文"]').or_(page.locator('[contenteditable="true"]'))
            await desc_input.fill(desc)

        print("\n图片和文案已填入，请检查后手动点击发布按钮")
        print("按回车关闭浏览器...")
        input()

        await browser.close()
        return True


def publish_sync(publish_info):
    """同步版本的发布"""
    import asyncio
    return asyncio.run(publish_to_xiaohongshu(publish_info))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="预览/发布帖子")
    parser.add_argument("--preview", type=str, help="预览：传入文案文件路径")
    parser.add_argument("--publish", type=str, help="发布：传入文案文件路径")
    parser.add_argument("--title", type=str, default="", help="帖子标题")
    args = parser.parse_args()

    if args.preview:
        with open(args.preview, "r", encoding="utf-8") as f:
            text = f.read()
        preview_post(text)

    elif args.publish:
        with open(args.publish, "r", encoding="utf-8") as f:
            text = f.read()
        info = prepare_publish(text, title=args.title)
        print(f"\n准备发布: {info['slide_count']} 张图片")
        confirm = input("确认发布到小红书？(y/n): ")
        if confirm.lower() == "y":
            publish_sync(info)
        else:
            print("已取消")
    else:
        print("用法:")
        print("  预览: python pipeline/publish/publish.py --preview draft.txt")
        print("  发布: python pipeline/publish/publish.py --publish draft.txt --title '标题'")
