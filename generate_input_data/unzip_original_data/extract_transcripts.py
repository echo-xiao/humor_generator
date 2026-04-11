import os
import zipfile
import subprocess
import time
from google.cloud import storage
from google.api_core import exceptions
from google import genai
from google.genai import types
from tqdm import tqdm
from dotenv import load_dotenv

load_dotenv()

# ==================== 配置 ====================
BUCKET_NAME = "xhs-humor-data"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

SOURCES = [
    "脱口秀大咖",
    "脱口秀集锦",
]

ZIP_PREFIX = "data/original_data/xhs_data/"
OUTPUT_PREFIX = "data/raw_data/"
LOCAL_TMP_DIR = "/tmp/transcript_tmp"

os.makedirs(LOCAL_TMP_DIR, exist_ok=True)

# ==================== 初始化 ====================
client = genai.Client(api_key=GEMINI_API_KEY)
storage_client = storage.Client()
bucket = storage_client.bucket(BUCKET_NAME)

PROMPT = "逐字转录脱口秀原文，在每个段子或笑点结束后换行分段。直接输出原文，不要其他内容。"

# ==================== 转录函数 ====================
def transcribe_with_retry(audio_path, max_retries=3):
    for attempt in range(max_retries):
        try:
            with open(audio_path, "rb") as f:
                audio_data = f.read()

            response = client.models.generate_content(
                model="gemini-2.5-pro",
                contents=[
                    PROMPT,
                    types.Part.from_bytes(data=audio_data, mime_type="audio/mpeg")
                ]
            )
            return response.text.strip()

        except Exception as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                wait = 10 * (2 ** attempt)
                print(f"限流，等待 {wait} 秒...")
                time.sleep(wait)
            elif "503" in str(e) or "UNAVAILABLE" in str(e):
                wait = 10 * (2 ** attempt)
                print(f"服务过载，等待 {wait} 秒...")
                time.sleep(wait)
            else:
                print(f"错误: {e}")
                return None
    return None

# ==================== 主流程 ====================
def process_source(source_name):
    print(f"\n{'='*50}")
    print(f"开始处理: {source_name}")

    # 找到对应的 zip 文件
    all_blobs = list(bucket.list_blobs(prefix=ZIP_PREFIX))
    zip_blobs = [b for b in all_blobs if source_name in b.name and b.name.endswith(".zip")]
    print(f"找到 {len(zip_blobs)} 个 zip 文件")

    # 断点续传：已完成的 txt
    output_prefix = f"{OUTPUT_PREFIX}{source_name}/"
    done_files = set(
        b.name.split("/")[-1].replace(".txt", "")
        for b in bucket.list_blobs(prefix=output_prefix)
        if b.name.endswith(".txt")
    )
    print(f"已完成 {len(done_files)} 个")

    for zip_blob in zip_blobs:
        zip_name = zip_blob.name.split("/")[-1].replace(".zip", "")
        local_zip = os.path.join(LOCAL_TMP_DIR, "current.zip")
        extract_dir = os.path.join(LOCAL_TMP_DIR, zip_name)

        print(f"\n下载: {zip_name}")
        subprocess.run([
            "gcloud", "storage", "cp",
            f"gs://{BUCKET_NAME}/{zip_blob.name}",
            local_zip
        ], check=True)

        try:
            with zipfile.ZipFile(local_zip, "r") as z:
                z.extractall(extract_dir)
        except zipfile.BadZipFile:
            print(f"压缩包损坏，跳过")
            os.remove(local_zip)
            continue

        # 找视频文件
        video_files = []
        for root, _, files in os.walk(extract_dir):
            for file in files:
                if file.lower().endswith((".mp4", ".mov", ".avi")) and not file.startswith("._"):
                    video_files.append((root, file))

        print(f"找到 {len(video_files)} 个视频")

        for root, file in tqdm(video_files, desc=zip_name[:30], unit="视频"):
            video_path = os.path.join(root, file)
            base_name = os.path.splitext(file)[0]
            audio_path = os.path.join(root, base_name + ".mp3")
            txt_name = base_name + ".txt"
            cloud_txt_path = f"{output_prefix}{txt_name}"

            # 断点续传
            if base_name in done_files:
                print(f"跳过: {file}")
                os.remove(video_path)
                continue

            print(f"处理: {file}")

            try:
                # 提取音频
                subprocess.run([
                    "ffmpeg", "-i", video_path,
                    "-vn", "-acodec", "libmp3lame", "-q:a", "2",
                    audio_path
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

                # Gemini 转录
                text = transcribe_with_retry(audio_path)
                if not text:
                    print(f"转录失败: {file}")
                    continue

                # 保存并上传
                txt_local = os.path.join(root, txt_name)
                with open(txt_local, "w", encoding="utf-8") as f:
                    f.write(text)
                bucket.blob(cloud_txt_path).upload_from_filename(txt_local)
                done_files.add(base_name)

                print(f"完成: {txt_name}")
                time.sleep(0.5)

            except Exception as e:
                print(f"失败 {file}: {e}")
            finally:
                if os.path.exists(video_path):
                    os.remove(video_path)
                if os.path.exists(audio_path):
                    os.remove(audio_path)

        os.remove(local_zip)

    print(f"\n{source_name} 处理完毕")

def main():
    for source in SOURCES:
        process_source(source)
    print("\n全部完成！")

if __name__ == "__main__":
    main()
