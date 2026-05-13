"""
extract_youtube.py — YouTube 字幕/转录下载

流程：
  youtube_sources.txt → 展开成 (频道 × 关键词) 任务列表
    → 每次处理一个任务（一个频道 + 一个关键词）
    → 本地 checkpoint 记录已完成任务，支持随时中断续跑
    → 频道视频列表内存缓存（同频道多关键词只拉一次）
    → 方案A：youtube-transcript-api 拉中文字幕
    → 方案B（无字幕时）：yt-dlp 下载音频 → FunASR (SenseVoice) 本地转录
    → 上传 GCS data/raw_data/youtube_脱口秀/{source_name}/{video_id}_{title}.txt

用法：
  python extract_youtube.py          # 跑所有未完成任务
  python extract_youtube.py --list   # 查看所有任务及完成状态
  python extract_youtube.py --reset  # 清空 checkpoint，从头开始

依赖：
  pip install google-api-python-client youtube-transcript-api yt-dlp funasr modelscope torch torchaudio
  环境变量：YOUTUBE_API_KEY
"""

import os
import re
import sys
import json
import time
import logging
import tempfile
import argparse
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from google.cloud import storage
from googleapiclient.discovery import build as yt_build
from tqdm import tqdm
from dotenv import load_dotenv

load_dotenv(override=True)

# ==================== 配置 ====================
BUCKET_NAME = "xhs-humor-data"
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
OUTPUT_PREFIX = "data/raw_data/youtube_脱口秀/"
SOURCES_FILE = os.path.join(os.path.dirname(__file__), "..", "configs", "youtube_sources.txt")
CHECKPOINT_FILE = os.path.join(os.path.dirname(__file__), "..", "configs", "youtube_checkpoint.json")

logging.basicConfig(level=logging.WARNING)  # 只显示警告，避免干扰进度条

# ==================== 初始化 ====================
storage_client = storage.Client()
bucket = storage_client.bucket(BUCKET_NAME)

# FunASR 模型（懒加载，首次使用时初始化）
_asr_model = None

def _get_asr_model():
    global _asr_model
    if _asr_model is None:
        from funasr import AutoModel
        tqdm.write("  加载 FunASR SenseVoice 模型（首次需要下载）...")
        _asr_model = AutoModel(
            model="iic/SenseVoiceSmall",
            vad_model="fsmn-vad",
            vad_kwargs={"max_single_segment_time": 30000},
        )
        tqdm.write("  FunASR 模型加载完成")
    return _asr_model
FAILED_VIDEOS_FILE = os.path.join(os.path.dirname(__file__), "..", "configs", "youtube_failed_videos.json")

# 频道视频列表内存缓存（同一次运行内复用）
_channel_cache: dict = {}


# ==================== 失败视频记录 ====================

def load_failed_videos() -> dict:
    """加载失败视频记录 {video_id: {"count": int, "last_error": str}}"""
    if os.path.exists(FAILED_VIDEOS_FILE):
        with open(FAILED_VIDEOS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_failed_videos(failed: dict):
    with open(FAILED_VIDEOS_FILE, "w", encoding="utf-8") as f:
        json.dump(failed, f, ensure_ascii=False, indent=2)


def mark_video_failed(failed: dict, video_id: str, error: str):
    entry = failed.get(video_id, {"count": 0, "last_error": ""})
    entry["count"] += 1
    entry["last_error"] = error
    failed[video_id] = entry
    save_failed_videos(failed)


# ==================== Checkpoint ====================

def load_checkpoint() -> set:
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_checkpoint(done: set):
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(done), f, ensure_ascii=False, indent=2)


def task_key(source: str, keyword: str | None) -> str:
    return f"{source}|{keyword or '__all__'}"


# ==================== 加载任务列表 ====================

def load_tasks() -> list[dict]:
    """
    从 youtube_sources.txt 展开成 (source, url, keyword) 任务列表。
    每个频道×每个关键词 = 一个独立任务。
    """
    default_filter = None
    sources = []
    with open(SOURCES_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("DEFAULT_FILTER="):
                default_filter = line.split("=", 1)[1].strip() or None
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) == 3:
                filter_val = parts[2].replace("filter=", "").strip() or default_filter
                sources.append({"source": parts[0], "url": parts[1], "filter": filter_val})
            elif len(parts) == 2:
                sources.append({"source": parts[0], "url": parts[1], "filter": default_filter})
            else:
                sources.append({"source": None, "url": parts[0], "filter": default_filter})

    tasks = []
    for item in sources:
        source = item["source"]
        url = item["url"]
        filter_str = item.get("filter") or ""
        keywords = [k.strip() for k in filter_str.split(",") if k.strip()]
        if keywords:
            for kw in keywords:
                tasks.append({"source": source, "url": url, "keyword": kw})
        else:
            tasks.append({"source": source, "url": url, "keyword": None})
    return tasks


# ==================== YouTube Data API v3 ====================

def _yt():
    return yt_build("youtube", "v3", developerKey=YOUTUBE_API_KEY)


def _get_channel_id(url):
    m = re.search(r'/channel/(UC[\w-]+)', url)
    if m:
        return m.group(1), None
    m = re.search(r'@([\w-]+)', url)
    if m:
        handle = m.group(1)
        resp = _yt().channels().list(part="snippet,contentDetails", forHandle=handle).execute()
        items = resp.get("items", [])
        if items:
            return items[0]["id"], items[0]["snippet"]["title"]
    return None, None


def _list_all_videos(channel_id: str) -> list[tuple]:
    resp = _yt().channels().list(part="contentDetails", id=channel_id).execute()
    uploads_id = resp["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    videos = []
    page_token = None
    with tqdm(desc="  拉取视频列表", unit="条", leave=False) as pbar:
        while True:
            resp = _yt().playlistItems().list(
                part="snippet", playlistId=uploads_id,
                maxResults=50, pageToken=page_token,
            ).execute()
            for item in resp["items"]:
                videos.append((
                    item["snippet"]["resourceId"]["videoId"],
                    item["snippet"]["title"],
                ))
                pbar.update(1)
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
    return videos


def get_channel_videos(url: str) -> tuple[list, str]:
    """获取频道全部视频，结果内存缓存（同频道多关键词只拉一次）"""
    channel_id, channel_title = _get_channel_id(url)
    if not channel_id:
        return [], url
    if channel_id not in _channel_cache:
        tqdm.write(f"  首次拉取频道视频列表...")
        _channel_cache[channel_id] = (_list_all_videos(channel_id), channel_title or channel_id)
    return _channel_cache[channel_id]


# ==================== 字幕 ====================

def get_subtitle(video_id: str) -> str | None:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        tl = YouTubeTranscriptApi.list_transcripts(video_id)
        for lang in ["zh-Hans", "zh", "zh-TW", "zh-CN"]:
            try:
                return "\n".join(t["text"] for t in tl.find_manually_created_transcript([lang]).fetch())
            except Exception:
                continue
        for lang in ["zh-Hans", "zh", "zh-TW", "zh-CN"]:
            try:
                return "\n".join(t["text"] for t in tl.find_generated_transcript([lang]).fetch())
            except Exception:
                continue
    except Exception:
        pass
    return None


# ==================== 音频转录 ====================

def _spinner(stop_event, label):
    chars = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    start = time.time()
    i = 0
    while not stop_event.is_set():
        sys.stderr.write(f"\r  {chars[i % len(chars)]} {label} ({_fmt_duration(time.time()-start)})   ")
        sys.stderr.flush()
        time.sleep(0.3)
        i += 1
    sys.stderr.write("\r" + " " * 60 + "\r")
    sys.stderr.flush()


def transcribe_with_retry(audio_path: str, max_retries: int = 3) -> str | None:
    for attempt in range(max_retries):
        try:
            stop_event = threading.Event()
            t = threading.Thread(target=_spinner, args=(stop_event, "FunASR 转录中..."), daemon=True)
            t.start()
            try:
                model = _get_asr_model()
                result = model.generate(input=audio_path, batch_size_s=300)
            finally:
                stop_event.set()
                t.join()

            if not result:
                tqdm.write("  转录结果为空")
                return None

            # 拼接所有片段的文本
            texts = []
            for segment in result:
                text = segment.get("text", "")
                if text:
                    texts.append(text)
            full_text = "\n".join(texts)
            tqdm.write(f"  转录完成: {len(full_text)} 字")
            return full_text.strip() if full_text.strip() else None

        except Exception as e:
            tqdm.write(f"  转录失败 (attempt {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2)
    return None


def download_and_transcribe(video_id: str) -> str | None:
    import yt_dlp
    with tempfile.TemporaryDirectory() as tmpdir:
        audio_path = os.path.join(tmpdir, f"{video_id}.mp3")
        pbar = None

        def progress_hook(d):
            nonlocal pbar
            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                downloaded = d.get("downloaded_bytes", 0)
                speed = d.get("speed") or 0
                if total and pbar is None:
                    pbar = tqdm(total=total, unit="B", unit_scale=True, desc="  下载音频", leave=False)
                if pbar:
                    pbar.n = downloaded
                    pbar.set_postfix(speed=f"{speed/1024/1024:.1f}MB/s" if speed else "")
                    pbar.refresh()
            elif d["status"] == "finished" and pbar:
                pbar.n = pbar.total
                pbar.refresh()
                pbar.close()

        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(tmpdir, f"{video_id}.%(ext)s"),
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "128"}],
            "quiet": True,
            "progress_hooks": [progress_hook],
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
        except Exception as e:
            if pbar:
                pbar.close()
            tqdm.write(f"  下载失败: {e}")
            return None

        if not os.path.exists(audio_path):
            return None
        return transcribe_with_retry(audio_path)


# ==================== 处理单个任务 ====================

def _fmt_duration(seconds):
    seconds = int(seconds)
    h, m, s = seconds // 3600, (seconds % 3600) // 60, seconds % 60
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def process_task(source: str, url: str, keyword: str | None, max_videos: int = 0):
    """处理一个 (频道, 关键词) 任务"""
    all_videos, channel_title = get_channel_videos(url)

    if not source:
        source = "".join(c for c in channel_title if c.isalnum() or c in " -_，。！？").strip()[:30]

    if keyword:
        videos = [(vid, title) for vid, title in all_videos if keyword in title]
        tqdm.write(f"  关键词「{keyword}」匹配：{len(all_videos)} → {len(videos)} 个视频")
    else:
        videos = all_videos
        tqdm.write(f"  全量：{len(videos)} 个视频")

    output_prefix = f"{OUTPUT_PREFIX}{source}/"

    # 断点续传（视频级别）
    done_videos = set(
        b.name.split("/")[-1].split("_")[0]
        for b in bucket.list_blobs(prefix=output_prefix)
        if b.name.endswith(".txt")
    )
    pending = [v for v in videos if v[0] not in done_videos]
    if max_videos > 0:
        pending = pending[:max_videos]
    tqdm.write(f"  已完成 {len(done_videos)} 个，待处理 {len(pending)} 个")

    if not pending:
        return

    total_start = time.time()
    completed = 0
    lock = threading.Lock()

    def _process_one_video(video_id, title):
        """处理单个视频，返回 (video_id, title, text, method) or None"""
        text = get_subtitle(video_id)
        method = "字幕"
        if not text:
            tqdm.write(f"  [{title[:25]}] 无字幕，音频转录...")
            text = download_and_transcribe(video_id)
            method = "FunASR转录"
        if not text:
            return None
        return (video_id, title, text, method)

    tqdm.write(f"  并发数：3")
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(_process_one_video, vid, title): (vid, title)
            for vid, title in pending
        }
        pbar = tqdm(total=len(pending), desc=f"{source}/{keyword or 'all'}", unit="视频", dynamic_ncols=True)

        for future in as_completed(futures):
            vid, title = futures[future]
            pbar.update(1)
            try:
                result = future.result()
            except Exception as e:
                tqdm.write(f"  ⚠ 异常跳过 {title[:25]}: {e}")
                continue

            if result is None:
                tqdm.write(f"  ⚠ 跳过: {title[:25]}")
                continue

            video_id, title, text, method = result
            safe_title = "".join(c for c in title if c.isalnum() or c in " -_，。！？").strip()[:50]
            cloud_path = output_prefix + f"{video_id}_{safe_title}.txt"
            bucket.blob(cloud_path).upload_from_string(text, content_type="text/plain; charset=utf-8")

            with lock:
                done_videos.add(video_id)
                completed += 1
                avg = (time.time() - total_start) / completed
                remaining = avg * (len(pending) - completed)
            tqdm.write(
                f"  ✓ [{method}] {title[:25]} | {len(text)}字 | 剩余≈{_fmt_duration(remaining)}"
            )

        pbar.close()

    tqdm.write(f"  ✅ 任务完成，处理 {completed} 个视频，耗时 {_fmt_duration(time.time()-total_start)}")


# ==================== 主程序 ====================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="YouTube 脱口秀字幕/转录下载")
    parser.add_argument("--list", action="store_true", help="列出所有任务及完成状态")
    parser.add_argument("--reset", action="store_true", help="清空 checkpoint，从头开始")
    parser.add_argument("--limit", type=int, default=0, help="本次最多处理 N 个任务（0=不限制）")
    parser.add_argument("--max-videos", type=int, default=20, help="每个任务最多处理 N 个视频（0=不限制，默认20）")
    args = parser.parse_args()

    tasks = load_tasks()

    if args.reset:
        if os.path.exists(CHECKPOINT_FILE):
            os.remove(CHECKPOINT_FILE)
        tqdm.write("✅ Checkpoint 已清空")

    if args.list:
        done = load_checkpoint()
        tqdm.write(f"\n共 {len(tasks)} 个任务：\n")
        for i, t in enumerate(tasks, 1):
            key = task_key(t["source"], t["keyword"])
            status = "✅" if key in done else "⬜"
            tqdm.write(f"  {status} [{i:2d}] {t['source']} — 关键词: {t['keyword'] or '全部'}")
        tqdm.write("")
    else:
        done = load_checkpoint()
        pending = [t for t in tasks if task_key(t["source"], t["keyword"]) not in done]
        if args.limit > 0:
            pending = pending[:args.limit]
        tqdm.write(f"\n共 {len(tasks)} 个任务，已完成 {len(done)} 个，本次处理 {len(pending)} 个\n")

        for i, task in enumerate(pending, 1):
            key = task_key(task["source"], task["keyword"])
            tqdm.write(f"\n{'='*50}")
            tqdm.write(f"[{len(done)+i}/{len(tasks)}] {task['source']} — 关键词: {task['keyword'] or '全部'}")

            try:
                process_task(task["source"], task["url"], task["keyword"], max_videos=args.max_videos)
                done.add(key)
                save_checkpoint(done)
            except KeyboardInterrupt:
                tqdm.write("\n⚠ 中断，进度已保存，下次从这里继续")
                break
            except Exception as e:
                tqdm.write(f"  ❌ 任务失败: {e}，跳过继续（不标记为已完成，下次会重试）")
