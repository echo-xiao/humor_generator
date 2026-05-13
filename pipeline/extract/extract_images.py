"""
extract_images.py — 妈的欧洲账本图文内容提取

流程：
  GCS original_data/xhs_data/妈的欧洲账本*.zip
    → 解压图片/视频（.png/.jpg/.jpeg/.mp4）
    → Gemini OCR 提取文字（账本/清单保持条目格式）
    → 上传 GCS data/raw_data/妈的欧洲账本/extracted_data/{subject}/{file}.txt

特点：
  - 断点续传（每次先查云端已完成列表）
  - 限流自动指数退避重试（最多5次）

注意：使用 vertexai（服务账号鉴权），与其他脚本的 google.genai（API Key）不同
运行：python generate_input_data/extract/extract_images.py
"""

import os
import zipfile
import time
import vertexai
from vertexai.generative_models import GenerativeModel, Part
from google.cloud import storage
from google.api_core.exceptions import ResourceExhausted, ServiceUnavailable, InternalServerError

# 1. 初始化
PROJECT_ID = "gen-lang-client-0371685655"
LOCATION = "us-central1"
vertexai.init(project=PROJECT_ID, location=LOCATION)
model = GenerativeModel("gemini-2.5-pro")

# 2. 配置路径
BUCKET_NAME = 'xhs-humor-data'
SEARCH_KEYWORD = "妈的欧洲账本"
LOCAL_BASE_DIR = './raw_data'
EXTRACT_BASE_DIR = './extracted_results'

os.makedirs(LOCAL_BASE_DIR, exist_ok=True)
os.makedirs(EXTRACT_BASE_DIR, exist_ok=True)

storage_client = storage.Client()
bucket = storage_client.bucket(BUCKET_NAME)
all_blobs = storage_client.list_blobs(BUCKET_NAME)
target_zips = [b for b in all_blobs if SEARCH_KEYWORD in b.name and b.name.endswith('.zip')]

# 预扫描
print("正在预扫描文件总量...")
total_media_files = 0
task_list = []

for zip_blob in target_zips:
    local_temp_zip = os.path.join(LOCAL_BASE_DIR, "scan_temp.zip")
    zip_blob.download_to_filename(local_temp_zip)
    with zipfile.ZipFile(local_temp_zip, 'r') as z:
        media_in_zip = [f for f in z.namelist() if f.lower().endswith(('.png', '.jpg', '.jpeg', '.mp4')) and not f.startswith('__MACOSX')]
        total_media_files += len(media_in_zip)
        task_list.append((zip_blob, media_in_zip))
    os.remove(local_temp_zip)

print(f"找到 {len(target_zips)} 个压缩包，共计 {total_media_files} 个媒体文件。\n")

# 核心处理循环
processed_count = 0

for zip_blob, media_files in task_list:
    subject_name = zip_blob.name.split('/')[-1].replace('.zip', '')
    subject_dir = os.path.join(EXTRACT_BASE_DIR, subject_name)
    os.makedirs(subject_dir, exist_ok=True)

    local_zip = os.path.join(LOCAL_BASE_DIR, "current_process.zip")
    zip_blob.download_to_filename(local_zip)

    with zipfile.ZipFile(local_zip, 'r') as z:
        z.extractall(subject_dir)

    for root, _, files in os.walk(subject_dir):
        for file in files:
            if file.lower().endswith(('.png', '.jpg', '.jpeg', '.mp4')) and not file.startswith('._'):
                processed_count += 1

                txt_file_name = f"{file}.txt"
                blob_path = f"extracted_data/{subject_name}/{txt_file_name}"
                check_blob = bucket.blob(blob_path)

                if check_blob.exists():
                    print(f"进度: [{(processed_count / total_media_files) * 100:.1f}%] | 跳过已处理: {file}")
                    continue

                file_path = os.path.join(root, file)
                global_progress = (processed_count / total_media_files) * 100
                print(f"进度: [{global_progress:.1f}%] | 正在处理: {file}")

                max_retries = 5
                retry_delay = 5

                for attempt in range(max_retries + 1):
                    try:
                        with open(file_path, "rb") as f:
                            data = f.read()
                        mime_type = "video/mp4" if file.lower().endswith('.mp4') else "image/jpeg"
                        media_part = Part.from_data(data=data, mime_type=mime_type)

                        prompt = "请提取并列出该媒体文件中的所有文字。如果是账本或清单，请保持其条目格式。直接输出内容。"
                        response = model.generate_content([prompt, media_part])
                        text_result = response.text.strip()

                        local_txt_path = os.path.join(subject_dir, txt_file_name)
                        with open(local_txt_path, "w", encoding="utf-8") as f_txt:
                            f_txt.write(text_result)

                        check_blob.upload_from_filename(local_txt_path)
                        break

                    except (ResourceExhausted, ServiceUnavailable, InternalServerError) as e:
                        if attempt < max_retries:
                            sleep_time = retry_delay * (2 ** attempt)
                            print(f"  [Warning] 触发限流/服务繁忙 (429/5xx)。等待 {sleep_time} 秒后重试... (尝试 {attempt+1}/{max_retries})")
                            time.sleep(sleep_time)
                        else:
                            print(f"  [Error] {file} 重试多次失败，跳过。错误信息: {e}")

                    except Exception as e:
                        print(f"  [Error] 处理 {file} 出现未知错误: {e}")
                        break

    os.remove(local_zip)

print(f"\n任务处理完成！")
