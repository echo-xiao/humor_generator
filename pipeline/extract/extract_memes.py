"""
extract_memes.py — 十万个梗库梗结构提取

流程：
  GCS original_data/xhs_data/十万个梗库*.zip
    → 解压视频（.mp4/.mov/.avi）
    → ffmpeg 提取音频（mp3）
    → Gemini 提取结构化梗信息（梗名称/来源/含义/用法建议）
    → 上传 GCS extracted_knowledge/{subject}/{video}.txt

输出格式（供 to_rag_meme.py 解析）：
  【梗名称】：...
  【梗来源】：...
  【梗含义】：...
  【用法建议】：...

特点：
  - 断点续传（启动时预加载云端已完成列表）
  - 限流自动指数退避重试（最多5次）
  - 处理完立即删除本地视频/音频，节省磁盘

注意：使用 vertexai（服务账号鉴权），与其他脚本的 google.genai（API Key）不同
运行：python generate_input_data/extract/extract_memes.py
"""

import os
import zipfile
import subprocess
import time
import vertexai
from vertexai.generative_models import GenerativeModel, Part
from google.cloud import storage
from google.api_core import exceptions
import shutil

# 1. 环境初始化
PROJECT_ID = "gen-lang-client-0371685655"
LOCATION = "us-central1"
vertexai.init(project=PROJECT_ID, location=LOCATION)

MODEL_NAME = "gemini-2.5-pro"
model = GenerativeModel(MODEL_NAME)

# 2. 路径配置
BUCKET_NAME = 'xhs-humor-data'
SEARCH_KEYWORD = "十万个梗库"
LOCAL_BASE_DIR = './raw_videos_temp'
RESULT_BASE_DIR = './meme_results'
CLOUD_OUTPUT_DIR = "extracted_knowledge"

os.makedirs(LOCAL_BASE_DIR, exist_ok=True)
os.makedirs(RESULT_BASE_DIR, exist_ok=True)

storage_client = storage.Client()
bucket = storage_client.bucket(BUCKET_NAME)

# 断点续传：预加载已完成列表
print("正在同步云端已完成的任务列表，以便实现断点续传...")
existing_blobs = set([b.name for b in bucket.list_blobs(prefix=CLOUD_OUTPUT_DIR)])
print(f"云端已存在 {len(existing_blobs)} 个解析结果，这些将被跳过。\n")

# 3. 获取目标文件并预扫描总量
all_blobs = storage_client.list_blobs(BUCKET_NAME)
target_zips = [b for b in all_blobs if SEARCH_KEYWORD in b.name and b.name.endswith('.zip')]

print(f"正在预扫描视频总量以计算全局进度...")
total_videos = 0
task_list = []

for zip_blob in target_zips:
    temp_zip = os.path.join(LOCAL_BASE_DIR, "scan.zip")
    zip_blob.download_to_filename(temp_zip)
    with zipfile.ZipFile(temp_zip, 'r') as z:
        v_list = [f for f in z.namelist() if f.lower().endswith(('.mp4', '.mov', '.avi')) and not f.startswith('__MACOSX')]
        total_videos += len(v_list)
        task_list.append((zip_blob, v_list))
    os.remove(temp_zip)

print(f"共计待处理视频总数: {total_videos}\n")


def generate_with_retry(prompt_text, audio_bytes, max_retries=5):
    retry_count = 0
    wait_time = 10

    while retry_count < max_retries:
        try:
            audio_part = Part.from_data(data=audio_bytes, mime_type="audio/mpeg")
            response = model.generate_content([prompt_text, audio_part])
            return response.text.strip()

        except exceptions.ResourceExhausted:
            print(f"  [!] 触发限流 (429)，等待 {wait_time} 秒后重试 (第 {retry_count+1}/{max_retries} 次)...")
            time.sleep(wait_time)
            retry_count += 1
            wait_time *= 2

        except exceptions.InternalServerError:
            print(f"  [!] 服务器内部错误 (500)，等待 5 秒重试...")
            time.sleep(5)
            retry_count += 1

        except Exception as e:
            raise e

    raise Exception("重试次数耗尽，API 调用失败")


# 4. 核心处理循环
processed_count = 0
skipped_count = 0

for zip_blob, video_files in task_list:
    subject_name = zip_blob.name.split('/')[-1].replace('.zip', '')
    subject_dir = os.path.join(RESULT_BASE_DIR, subject_name)
    os.makedirs(subject_dir, exist_ok=True)

    local_zip = os.path.join(LOCAL_BASE_DIR, "current.zip")
    print(f"\n>>> 正在下载并解压主题: {subject_name}")
    zip_blob.download_to_filename(local_zip)

    try:
        with zipfile.ZipFile(local_zip, 'r') as z:
            z.extractall(subject_dir)
    except zipfile.BadZipFile:
        print(f"错误: 压缩包损坏 {subject_name}，跳过")
        continue

    for root, _, files in os.walk(subject_dir):
        for file in files:
            if file.lower().endswith(('.mp4', '.mov', '.avi')) and not file.startswith('._'):
                processed_count += 1
                video_path = os.path.join(root, file)
                audio_path = os.path.join(root, os.path.splitext(file)[0] + ".mp3")
                txt_name = f"{os.path.splitext(file)[0]}.txt"
                cloud_txt_path = f"{CLOUD_OUTPUT_DIR}/{subject_name}/{txt_name}"

                if cloud_txt_path in existing_blobs:
                    print(f"[{processed_count}/{total_videos}] 跳过已存在: {file}")
                    skipped_count += 1
                    if os.path.exists(video_path): os.remove(video_path)
                    continue

                percent = (processed_count / total_videos) * 100
                print(f"进度: [{percent:.2f}%] | 正在处理: {file}")

                try:
                    subprocess.run([
                        'ffmpeg', '-i', video_path,
                        '-vn', '-acodec', 'libmp3lame', '-q:a', '2',
                        audio_path
                    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

                    prompt = """
                    作为互联网梗百科专家，请听这段音频并提取：
                    1. 【梗名称】：
                    2. 【梗来源】：（原始出处/背景）
                    3. 【梗含义】：（为什么好笑/逻辑）
                    4. 【用法建议】：（适用文案场景）
                    直接输出以上结构化内容。
                    """

                    with open(audio_path, "rb") as f_audio:
                        audio_data = f_audio.read()

                    result_text = generate_with_retry(prompt, audio_data)

                    txt_local_path = os.path.join(subject_dir, txt_name)
                    with open(txt_local_path, "w", encoding="utf-8") as f_txt:
                        f_txt.write(result_text)

                    bucket.blob(cloud_txt_path).upload_from_filename(txt_local_path)
                    existing_blobs.add(cloud_txt_path)

                    if os.path.exists(video_path): os.remove(video_path)
                    if os.path.exists(audio_path): os.remove(audio_path)

                except Exception as e:
                    print(f"处理文件 {file} 失败: {e}")
                    if os.path.exists(audio_path): os.remove(audio_path)

    if os.path.exists(local_zip):
        os.remove(local_zip)
    shutil.rmtree(subject_dir)

print(f"\n--- 任务全部完成！共扫描 {processed_count}，实际处理 {processed_count - skipped_count}，跳过 {skipped_count} ---")
