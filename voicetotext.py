import sys
import os
import json
import time
import threading
import tempfile
import shutil
import subprocess
import re
import requests
import webbrowser
import asyncio
import traceback
import importlib.util
import opencc
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import gspread

import yt_dlp
import imageio_ffmpeg
# Google Gemini SDK compatibility: prefer new google-genai, fall back to deprecated google-generativeai if present.
try:
    from google import genai as new_genai
except Exception:
    new_genai = None
try:
    import google.generativeai as old_genai
except Exception:
    old_genai = None

from PyQt5.QtWidgets import (QApplication, QWidget, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                             QFileDialog, QMessageBox, QVBoxLayout, QHBoxLayout, QSpacerItem,
                             QSizePolicy, QFormLayout, QDialog)
from PyQt5.QtGui import QFont
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QStandardPaths

# 尝试导入 chrome_lens_py
try:
    from chrome_lens_py import LensAPI
except Exception as e:
    print(f"警告: chrome_lens_py 导入失败。图片翻译功能将不可用。错误: {e}")
    LensAPI = None

# ----------------------------
# 全局配置和常量
# ----------------------------
converter = opencc.OpenCC('t2s')
GOOGLE_API_KEYS = []
current_key_index = 0
ORIGINAL_GEMINI_MODELS = ["gemini-2.5-flash"]
GEMINI_MODELS = ORIGINAL_GEMINI_MODELS.copy()
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
COOKIE_FILENAME = 'facebook_cookies.txt'
# 兼容用户常见命名：打包后可把 cookies.txt 或 facebook_cookies.txt 放在 exe 同目录
COOKIE_CANDIDATE_FILENAMES = ['cookies.txt', 'facebook_cookies.txt']


def get_app_dir():
    """返回程序所在目录。源码运行时是 .py 所在目录，打包后是 .exe 所在目录。"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def get_default_cookie_path(cache_dir=None):
    """
    优先使用 exe/脚本同目录下的 cookies 文件；找不到时再使用系统缓存目录。
    这样打包后只要把 cookies.txt 放在 exe 旁边，程序就能识别。
    """
    app_dir = get_app_dir()
    for name in COOKIE_CANDIDATE_FILENAMES:
        candidate = os.path.join(app_dir, name)
        if os.path.exists(candidate):
            return candidate

    if cache_dir:
        return os.path.join(cache_dir, COOKIE_FILENAME)
    return os.path.join(app_dir, COOKIE_FILENAME)


def ensure_cookie_path(cookie_path):
    """
    运行时再次确认 Cookie 文件位置，兼容 exe 同目录和缓存目录。
    返回实际存在的 Cookie 文件路径；都不存在时返回原路径。
    """
    if cookie_path and os.path.exists(cookie_path):
        return cookie_path

    app_dir = get_app_dir()
    for name in COOKIE_CANDIDATE_FILENAMES:
        candidate = os.path.join(app_dir, name)
        if os.path.exists(candidate):
            return candidate

    return cookie_path

# Google Key 限制管理
GOOGLE_KEY_BACKOFF = {}
GOOGLE_KEY_BACKOFF_LOCK = threading.Lock()
GOOGLE_KEY_BACKOFF_SECONDS = 1000

# 【新增】全局标志位：记录本次软件运行期间 Groq 是否已失败
# 如果为 True，后续任务将直接使用 Deepgram
GROQ_HAS_FAILED_THIS_SESSION = False


# ----------------------------
# 运行环境检查：源码运行时提醒缺少依赖；打包后的 exe 不自动 pip 安装，避免破坏用户环境
# ----------------------------
def check_runtime_dependencies():
    required = {
        "requests": "requests",
        "gspread": "gspread",
        "yt_dlp": "yt-dlp",
        "imageio_ffmpeg": "imageio-ffmpeg",
        "opencc": "opencc-python-reimplemented",
        "PyQt5": "PyQt5",
        "google.auth": "google-auth",
        "google_auth_oauthlib": "google-auth-oauthlib",
        "chrome_lens_py": "chrome-lens-py",
        "google.protobuf": "protobuf",
    }
    missing = []
    for module_name, package_name in required.items():
        if importlib.util.find_spec(module_name) is None:
            missing.append(package_name)
    return sorted(set(missing))


def format_missing_dependency_message(missing):
    if not missing:
        return ""
    return (
        "检测到缺少依赖库：\n"
        + "\n".join(f"- {name}" for name in missing)
        + "\n\n请在项目目录执行：\npython -m pip install -r requirements.txt"
    )


# ----------------------------
# 修改：解析合并的 API Keys 输入 (支持 Deepgram)
# ----------------------------
def parse_combined_keys(multiline_text):
    """
    解析 Key。
    规则：
      - groq: / gsk: -> Groq
      - deepgram: / dg: -> Deepgram
      - google: / AIza -> Google
    返回 (google_keys, groq_keys, deepgram_keys, unknown_keys)
    """
    google_keys = []
    groq_keys = []
    deepgram_keys = []
    unknown_keys = []

    if not multiline_text:
        return google_keys, groq_keys, deepgram_keys, unknown_keys

    for line in multiline_text.splitlines():
        line = line.strip()
        if not line:
            continue
        # 去掉引号
        if (line.startswith('"') and line.endswith('"')) or (line.startswith("'") and line.endswith("'")):
            line = line[1:-1].strip()

        low = line.lower()

        # 1. Deepgram 识别
        if low.startswith("deepgram:") or low.startswith("dg:"):
            parts = line.split(":", 1)
            key = parts[1].strip() if len(parts) > 1 else ""
            if key: deepgram_keys.append(key)

        # 2. Groq 识别
        elif low.startswith("groq:") or low.startswith("gsk:") or low.startswith("g:"):
            parts = line.split(":", 1)
            key = parts[1].strip() if len(parts) > 1 else ""
            if key: groq_keys.append(key)

        # 3. Google 识别
        elif low.startswith("google:") or low.startswith("gg:") or low.startswith("genai:") or low.startswith(
                "googlekey:"):
            parts = line.split(":", 1)
            key = parts[1].strip() if len(parts) > 1 else ""
            if key: google_keys.append(key)

        # 4. 无前缀自动识别
        else:
            if line.startswith("AIza"):
                google_keys.append(line)
            elif low.startswith("gsk"):
                groq_keys.append(line)
            # Deepgram key 通常没有固定前缀特征，如果不加前缀很难自动识别，
            # 这里为了保险，如果不带 deepgram: 前缀，暂时归为 unknown，
            # 或者你可以假设剩下的长字符串可能是 deepgram，但建议用户加前缀。
            else:
                unknown_keys.append(line)

    return google_keys, groq_keys, deepgram_keys, unknown_keys


# ----------------------------
# 翻译与修正 (Google / Groq)
# ----------------------------
def groq_translate_via_chat(prompt, groq_key, log_callback=None, max_tokens=1200):
    api_base = "https://api.groq.com/openai/v1"
    url = f"{api_base}/chat/completions"
    headers = {"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"}
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    try:
        if log_callback: log_callback("正在使用 Groq (llama-3.3-70b-versatile) 进行翻译回落...")
        r = requests.post(url, headers=headers, json=payload, timeout=60)
        if r.status_code in (401, 403):
            if log_callback: log_callback(f"Groq 翻译请求被拒绝: {r.status_code} {r.text}")
            return None
        r.raise_for_status()
        data = r.json()
        choices = data.get("choices") or []
        if choices:
            content = choices[0].get("message", {}).get("content") if choices[0].get("message") else choices[0].get(
                "text")
            if content:
                return content.strip()
        return None
    except Exception as e:
        if log_callback: log_callback(f"Groq 翻译请求失败: {e}")
        return None



def generate_gemini_text(api_key, model_name, prompt):
    """兼容新版 google-genai 与旧版 google-generativeai 的 Gemini 调用。"""
    if new_genai is not None:
        client = new_genai.Client(api_key=api_key)
        response = client.models.generate_content(model=model_name, contents=prompt)
        text = getattr(response, "text", None)
        if text:
            return text.strip()
        # 兼容某些返回结构
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            content = getattr(candidates[0], "content", None)
            parts = getattr(content, "parts", None) if content else None
            if parts:
                maybe_text = getattr(parts[0], "text", None)
                if maybe_text:
                    return maybe_text.strip()
        return ""

    if old_genai is not None:
        old_genai.configure(api_key=api_key)
        model = old_genai.GenerativeModel(model_name)
        response = model.generate_content(prompt)
        if response.candidates and response.candidates[0].content.parts:
            return response.candidates[0].content.parts[0].text.strip()
        return ""

    raise RuntimeError("未安装 Gemini SDK。请安装 google-genai。")

def translate_and_correct(text, translate_require, google_keys, groq_keys, unknown_keys, log_callback=None):
    global GEMINI_MODELS, ORIGINAL_GEMINI_MODELS, GOOGLE_KEY_BACKOFF, GOOGLE_KEY_BACKOFF_LOCK, GOOGLE_KEY_BACKOFF_SECONDS
    GEMINI_MODELS = ORIGINAL_GEMINI_MODELS.copy()

    if translate_require:
        prompt_template = f"{translate_require}\n下面是原始文本：\n{{text}}"
    else:
        prompt_template = (
            "请先修正以下贴文中的所有语法和拼接错误，再将修正后的文本翻译成流畅易懂的中文（如果有重复内容，只翻译一次）。"
            " 完成翻译后，请备注贴文是什么语言，最终结果格式：（原文）翻译结果\n下面是原始文本：\n{text}"
        )
    prompt = prompt_template.format(text=text)

    # Google 尝试
    google_candidates = list(google_keys) + [k for k in unknown_keys if k.startswith("AIza")]
    now = time.time()
    with GOOGLE_KEY_BACKOFF_LOCK:
        google_candidates = [k for k in google_candidates if
                             not (k in GOOGLE_KEY_BACKOFF and GOOGLE_KEY_BACKOFF[k] > now)]

    for gkey in google_candidates:
        try:
            if log_callback: log_callback("尝试使用 Google Key 进行翻译...")
            for model_name in list(GEMINI_MODELS):
                try:
                    result = generate_gemini_text(gkey, model_name, prompt)
                    if result:
                        if log_callback: log_callback("使用 Google Key 翻译成功。")
                        return result
                except Exception as e:
                    err = str(e).lower()
                    if log_callback: log_callback(f"[Google:{model_name}] 错误: {e}")
                    mark_backoff = False
                    if "429" in err or "quota" in err or "rate limit" in err or "exceeded" in err: mark_backoff = True
                    if "resource has been exhausted" in err: mark_backoff = True
                    if "invalid" in err or "401" in err or "403" in err: mark_backoff = True

                    if mark_backoff:
                        with GOOGLE_KEY_BACKOFF_LOCK:
                            GOOGLE_KEY_BACKOFF[gkey] = time.time() + GOOGLE_KEY_BACKOFF_SECONDS
                    continue
        except Exception as e:
            with GOOGLE_KEY_BACKOFF_LOCK:
                GOOGLE_KEY_BACKOFF[gkey] = time.time() + GOOGLE_KEY_BACKOFF_SECONDS
            continue

    # Groq 回落
    groq_candidates = list(groq_keys) + [k for k in unknown_keys if k.lower().startswith("gsk")]
    for groq_key in groq_candidates:
        res = groq_translate_via_chat(prompt, groq_key, log_callback=log_callback)
        if res:
            if log_callback: log_callback("使用 Groq 翻译（回落）成功。")
            return res
        else:
            continue

    if log_callback:
        log_callback("翻译失败：未能使用任一 Google 或 Groq Key 完成翻译。")
    return "翻译失败"


# ----------------------------
# 辅助功能：GSpread, Download Audio
# ----------------------------
def get_gspread_client():
    import json
    cache_dir = QStandardPaths.writableLocation(QStandardPaths.CacheLocation)
    json_file = os.path.join(cache_dir, "credentials.json")
    if not os.path.exists(json_file):
        raise Exception("授权文件 (credentials.json) 不存在。")

    creds = None
    token_file = os.path.join(cache_dir, "token.json")

    if os.path.exists(token_file):
        try:
            creds = Credentials.from_authorized_user_file(token_file, SCOPES)
        except Exception:
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            with open(json_file, 'r', encoding='utf-8') as f:
                client_config = json.load(f)
            flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(token_file, "w", encoding="utf-8") as token:
            token.write(creds.to_json())

    return gspread.authorize(creds)


def extract_audio_as_mp3(url, log_callback, stop_event, cookie_path):
    log_callback("开始从链接提取音频...")
    cookie_path = ensure_cookie_path(cookie_path)
    if not cookie_path or not os.path.exists(cookie_path):
        log_callback(f"错误：无法找到Cookie缓存文件。请把 cookies.txt 或 {COOKIE_FILENAME} 放到软件exe同目录，或点击“更新Cookies”。")
        raise Exception("Cookie文件缺失")
    log_callback(f"已使用Cookie文件: {cookie_path}")
    try:
        ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        log_callback("正在尝试自动下载FFmpeg...")
        try:
            imageio_ffmpeg.download()
            ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception as e:
            raise Exception("FFmpeg初始化失败")

    ydl_opts = {'format': 'bestaudio/best', 'ffmpeg_location': ffmpeg_path, 'cookiefile': cookie_path,
                'outtmpl': os.path.join(tempfile.gettempdir(), '%(id)s.%(ext)s'),
                'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}],
                'quiet': True, 'noprogress': True, 'noplaylist': True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=True)
            base_filename = ydl.prepare_filename(info_dict).rsplit('.', 1)[0]
            mp3_filename = base_filename + '.mp3'
            if stop_event.is_set():
                if os.path.exists(mp3_filename): os.remove(mp3_filename)
                return None
            if os.path.exists(mp3_filename):
                log_callback("音频提取并转换为MP3成功。")
                return mp3_filename
            else:
                return None
    except Exception as e:
        log_callback(f"音频提取过程中发生错误: {e}")
        raise e


# ----------------------------
# 核心：转写函数 (Groq & Deepgram)
# ----------------------------
def groq_transcribe(file_path, groq_key, log_callback=None):
    """Groq 语音转文字"""
    api_base = "https://api.groq.com/openai/v1"
    url = f"{api_base}/audio/transcriptions"
    headers = {"Authorization": f"Bearer {groq_key}"}
    with open(file_path, "rb") as audio_file:
        files = {"file": audio_file}
        data = {"model": "whisper-large-v3-turbo"}
        if log_callback: log_callback("正在上传音频至 Groq...")
        response = requests.post(url, headers=headers, files=files, data=data)
    response.raise_for_status()
    result = response.json()
    if log_callback: log_callback("Groq 语音转文字成功。")
    return result.get("text", "")


def deepgram_transcribe(file_path, deepgram_key, log_callback=None):
    """Deepgram 语音转文字 (Nova-2)"""
    url = "https://api.deepgram.com/v1/listen"
    # smart_format=true 会自动添加标点，model=nova-2 效果好速度快
    params = {
        "model": "nova-2",
        "smart_format": "true",
        "language": "auto"  # 也可以指定 language='zh'
    }
    headers = {
        "Authorization": f"Token {deepgram_key}",
        "Content-Type": "audio/*"  # Deepgram 自动识别格式
    }

    if log_callback: log_callback("正在上传音频至 Deepgram (Nova-2)...")

    with open(file_path, "rb") as audio:
        response = requests.post(url, params=params, headers=headers, data=audio, timeout=120)

    response.raise_for_status()
    data = response.json()

    # 解析 Deepgram 返回的 JSON 结构
    transcript = ""
    if 'results' in data and 'channels' in data['results']:
        channels = data['results']['channels']
        if len(channels) > 0 and 'alternatives' in channels[0]:
            alternatives = channels[0]['alternatives']
            if len(alternatives) > 0:
                transcript = alternatives[0].get('transcript', "")

    if log_callback: log_callback("Deepgram 语音转文字成功。")
    return transcript


# ----------------------------
# 图片处理
# ----------------------------
def is_image_url(url):
    url_path = str(url).split('?')[0].split('#')[0]
    image_extensions = ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp']
    for ext in image_extensions:
        if url_path.lower().endswith(ext): return True
    if 'fbcdn.net' in url and ('.jpg' in url or '.png' in url): return True
    return False


def extract_url_from_formula(url_string):
    url_string = str(url_string).strip()
    match = re.search(r'=IMAGE\("([^"]+)"', url_string, re.IGNORECASE)
    if match: return match.group(1).strip()
    return url_string


async def async_translate_image(url, log_callback):
    if not LensAPI: return "", "错误: 缺少 chrome_lens_py"
    log_callback(f"处理图片: {url}")
    try:
        lens = LensAPI()
        result = await lens.process_image(image_path=url, target_translation_language="zh-CN")
        original_text = result.get("ocr_text", "") or result.get("text", "")
        translated_text = result.get("translated_text", "") or result.get("translation", "")
        return original_text.strip(), translated_text.strip()
    except Exception as e:
        log_callback(f"图片处理出错: {e}")
        log_callback(traceback.format_exc())
        raise e


def translate_image_url(url, log_callback, stop_event, config, google_keys, groq_keys, unknown_keys):
    loop = None
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        if stop_event.is_set(): return None, None
        future = loop.create_task(async_translate_image(url, log_callback))
        loop.run_until_complete(future)
        if stop_event.is_set(): return None, None
        original_text, translated_text = future.result()

        if original_text and not translated_text:
            log_callback("Lens未提供翻译，使用 AI 翻译...")
            translated_text = translate_and_correct(original_text, config.get("translate_require"),
                                                    google_keys, groq_keys, unknown_keys, log_callback)
        return original_text, translated_text
    except Exception as e:
        return f"图片失败: {e}", ""
    finally:
        if loop: loop.close()


# ----------------------------
# 核心流程：Process Conversion (包含 Groq -> Deepgram 切换逻辑)
# ----------------------------
def process_conversion(config, log_callback, stop_event):
    log_callback("开始批量转换...")
    global GOOGLE_API_KEYS, current_key_index, GEMINI_MODELS, GROQ_HAS_FAILED_THIS_SESSION

    api_keys_text = config.get("api_keys", "")
    # 解析出三种 key
    google_keys, groq_keys, deepgram_keys, unknown_keys = parse_combined_keys(api_keys_text)

    log_callback(f"Keys加载 - Google:{len(google_keys)}, Groq:{len(groq_keys)}, Deepgram:{len(deepgram_keys)}")
    GOOGLE_API_KEYS = google_keys.copy()
    current_key_index = 0
    GEMINI_MODELS = ORIGINAL_GEMINI_MODELS.copy()

    # 获取表格信息
    link = config.get("sheet_link", "")
    m = re.search(r"/d/([a-zA-Z0-9-_]+)", link)
    sheet_id = m.group(1) if m else None
    m_gid = re.search(r"(?:\?|&|#)gid=(\d+)", link)
    gid = m_gid.group(1) if m_gid else None

    if not sheet_id:
        log_callback("表格链接无效。")
        return

    try:
        gc = get_gspread_client()
        sh = gc.open_by_key(sheet_id)
        worksheet = sh.get_worksheet_by_id(int(gid)) if gid and gid.isdigit() else sh.sheet1
    except Exception as e:
        log_callback(f"表格连接失败：{e}")
        return

    all_rows = worksheet.get_all_values(value_render_option='FORMULA')

    def col_to_index(col_str):
        return ord(col_str.strip().upper()) - ord('A') if col_str else None

    link_col = col_to_index(config.get("link_col", ""))
    original_col = col_to_index(config.get("original_col", ""))
    trans_result_col = col_to_index(config.get("trans_result_col", ""))

    if link_col is None or original_col is None or trans_result_col is None:
        log_callback("列号设置错误。")
        return

    # 准备 Key
    groq_key = groq_keys[0] if groq_keys else next((k for k in unknown_keys if k.lower().startswith("gsk")), None)
    deepgram_key = deepgram_keys[0] if deepgram_keys else None

    if not groq_key and not deepgram_key and not LensAPI:
        log_callback("严重警告：未检测到 Groq 或 Deepgram Key，视频无法处理。")

    cookie_path = config.get("cookie_path")
    tasks = 0

    for i in range(1, len(all_rows)):
        if stop_event.is_set():
            log_callback("任务停止。")
            return

        row = all_rows[i]
        # 检查是否需要处理
        if len(row) > link_col and str(row[link_col]).strip() and (
                len(row) <= max(original_col, trans_result_col) or not (
                str(row[original_col]).strip() or str(row[trans_result_col]).strip())):

            url = extract_url_from_formula(str(row[link_col]).strip())
            log_callback(f"处理第 {i + 1} 行：{url}")

            original_text = ""
            translated_text = ""
            audio_file = None

            try:
                if is_image_url(url):
                    # 图片逻辑
                    if not LensAPI: raise Exception("缺少 chrome_lens_py")
                    original_text, translated_text = translate_image_url(url, log_callback, stop_event, config,
                                                                         google_keys, groq_keys, unknown_keys)
                    if original_text is None: continue
                else:
                    # 视频逻辑
                    audio_file = extract_audio_as_mp3(url, log_callback, stop_event, cookie_path)
                    if audio_file is None: continue

                    # [关键修改] 语音转文字逻辑：Groq -> 失败切换 -> Deepgram
                    transcribe_done = False

                    # 1. 尝试 Groq (如果当前会话没失败过，且有Key)
                    if not GROQ_HAS_FAILED_THIS_SESSION and groq_key:
                        try:
                            original_text = groq_transcribe(audio_file, groq_key, log_callback)
                            transcribe_done = True
                        except Exception as e:
                            log_callback(f"Groq 转写失败: {e}。本次运行将切换至 Deepgram。")
                            GROQ_HAS_FAILED_THIS_SESSION = True  # 标记失败，后续不再尝试 Groq

                    # 2. 尝试 Deepgram (如果 Groq 失败了，或者压根没配 Groq)
                    if not transcribe_done:
                        if deepgram_key:
                            try:
                                original_text = deepgram_transcribe(audio_file, deepgram_key, log_callback)
                                transcribe_done = True
                            except Exception as e:
                                log_callback(f"Deepgram 转写也失败: {e}")
                                original_text = f"转写失败: Groq和Deepgram均不可用。"
                        else:
                            if GROQ_HAS_FAILED_THIS_SESSION:
                                log_callback("Groq已失效，且未配置 Deepgram Key (需 deepgram: 开头)。无法处理。")
                            else:
                                log_callback("未配置任何有效的语音转文字 Key。")
                            original_text = "Key配置错误"

                    # 翻译逻辑
                    if transcribe_done and original_text:
                        if len(original_text.strip()) <= 5:
                            translated_text = original_text
                        else:
                            translated_text = translate_and_correct(original_text, config.get("translate_require"),
                                                                    google_keys, groq_keys, unknown_keys, log_callback)

            except Exception as e:
                log_callback(f"行 {i + 1} 错误：{e}")
                original_text = f"错误: {e}"
            finally:
                if audio_file and os.path.exists(audio_file):
                    try:
                        os.remove(audio_file)
                    except:
                        pass

            # 回写表格
            try:
                worksheet.update_cell(i + 1, original_col + 1, original_text)
                worksheet.update_cell(i + 1, trans_result_col + 1, translated_text)
                log_callback(f"第 {i + 1} 行完成。")
            except Exception as e:
                log_callback(f"回写表格失败：{e}")

            tasks += 1
            time.sleep(3)

    log_callback("全部完成。" if tasks > 0 else "无新任务。")


# ----------------------------
# 线程类
# ----------------------------
class ConversionWorker(QThread):
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.stop_event = threading.Event()

    def run(self):
        process_conversion(self.config, self.log_signal.emit, self.stop_event)
        self.finished_signal.emit()

    def stop(self): self.stop_event.set()


class CookieUpdateWorker(QThread):
    finished = pyqtSignal(bool, str)

    def __init__(self, cookie_path):
        super().__init__()
        self.cookie_path = cookie_path

    def run(self):
        parent_dir = os.path.dirname(self.cookie_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)
        if os.path.exists(self.cookie_path):
            try:
                os.remove(self.cookie_path)
            except Exception as e:
                self.finished.emit(False, f"删除旧Cookie失败: {e}")
                return
        ydl_opts = {'cookies_from_browser': ('chrome',), 'cookiefile': self.cookie_path}
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.extract_info("https://www.facebook.com", download=False, process=False)
            if os.path.exists(self.cookie_path):
                self.finished.emit(True, f"Cookies更新成功: {self.cookie_path}")
            else:
                self.finished.emit(False, "Cookie文件创建失败")
        except Exception as e:
            self.finished.emit(False, f"获取Cookies失败: {e}")


class SingleTranslationWorker(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(str, str)
    error = pyqtSignal(str)

    def __init__(self, url, config, cookie_path):
        super().__init__()
        self.url = url
        self.config = config
        self.cookie_path = cookie_path
        self.stop_event = threading.Event()

    def run(self):
        audio_file = None
        global GROQ_HAS_FAILED_THIS_SESSION
        try:
            api_keys_text = self.config.get("api_keys", "")
            google_keys, groq_keys, deepgram_keys, unknown_keys = parse_combined_keys(api_keys_text)

            groq_key = groq_keys[0] if groq_keys else next((k for k in unknown_keys if k.lower().startswith("gsk")),
                                                           None)
            deepgram_key = deepgram_keys[0] if deepgram_keys else None

            original_text = ""
            translated_text = ""

            if is_image_url(self.url):
                self.progress.emit("处理图片...")
                if not LensAPI: raise Exception("缺少 chrome_lens_py")
                original_text, translated_text = translate_image_url(self.url, self.progress.emit, self.stop_event,
                                                                     self.config, google_keys, groq_keys, unknown_keys)
                if original_text is None: return
            else:
                self.progress.emit("提取音频...")
                audio_file = extract_audio_as_mp3(self.url, self.progress.emit, self.stop_event, self.cookie_path)
                if not audio_file:
                    self.error.emit("音频提取失败")
                    return

                # --- 临时翻译也应用切换逻辑 ---
                transcribe_done = False

                # 1. Groq
                if not GROQ_HAS_FAILED_THIS_SESSION and groq_key:
                    try:
                        original_text = groq_transcribe(audio_file, groq_key, log_callback=self.progress.emit)
                        transcribe_done = True
                    except Exception as e:
                        self.progress.emit(f"Groq失败，切换Deepgram...")
                        GROQ_HAS_FAILED_THIS_SESSION = True

                # 2. Deepgram
                if not transcribe_done:
                    if deepgram_key:
                        try:
                            original_text = deepgram_transcribe(audio_file, deepgram_key,
                                                                log_callback=self.progress.emit)
                            transcribe_done = True
                        except Exception as e:
                            self.error.emit(f"Deepgram也失败: {e}")
                            return
                    else:
                        self.error.emit("无可用的语音API Key (Groq失败或未配，且无Deepgram Key)")
                        return

                if not original_text.strip():
                    translated_text = "内容为空"
                else:
                    self.progress.emit("翻译中...")
                    translated_text = translate_and_correct(original_text, self.config.get("translate_require"),
                                                            google_keys, groq_keys, unknown_keys, self.progress.emit)

            self.finished.emit(original_text, translated_text)
        except Exception as e:
            self.error.emit(str(e))
        finally:
            if audio_file and os.path.exists(audio_file):
                try:
                    os.remove(audio_file)
                except:
                    pass

    def stop(self):
        self.stop_event.set()


# ----------------------------
# 界面类
# ----------------------------
class TemporaryTranslatorDialog(QDialog):
    def __init__(self, config, cookie_path, parent=None):
        super().__init__(parent)
        self.config = config
        self.cookie_path = cookie_path
        self.worker = None
        self.initUI()
        if parent: self.setStyleSheet(parent.styleSheet())

    def initUI(self):
        self.setWindowTitle("临时翻译")
        self.setMinimumSize(500, 400)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.url_edit = QLineEdit(placeholderText="视频/图片链接")
        form.addRow("链接:", self.url_edit)
        layout.addLayout(form)
        self.orig_edit = QPlainTextEdit(readOnly=True)
        self.trans_edit = QPlainTextEdit(readOnly=True)
        layout.addWidget(QLabel("原文:"))
        layout.addWidget(self.orig_edit)
        layout.addWidget(QLabel("译文:"))
        layout.addWidget(self.trans_edit)
        self.status = QLabel("就绪")
        layout.addWidget(self.status)
        self.btn = QPushButton("开始")
        self.btn.clicked.connect(self.start)
        layout.addWidget(self.btn)

    def start(self):
        url = extract_url_from_formula(self.url_edit.text().strip())
        if not url: return
        self.btn.setEnabled(False)
        self.orig_edit.clear()
        self.trans_edit.clear()
        self.worker = SingleTranslationWorker(url, self.config, self.cookie_path)
        self.worker.progress.connect(self.status.setText)
        self.worker.finished.connect(self.done_work)
        self.worker.error.connect(self.err_work)
        self.worker.start()

    def done_work(self, o, t):
        self.orig_edit.setPlainText(o)
        self.trans_edit.setPlainText(t)
        self.status.setText("完成")
        self.btn.setEnabled(True)

    def err_work(self, e):
        QMessageBox.critical(self, "错误", e)
        self.status.setText("出错")
        self.btn.setEnabled(True)

    def closeEvent(self, e):
        if self.worker: self.worker.stop()
        e.accept()


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("语音与图片翻译工具 v2.3 (Groq+Deepgram)")
        self.resize(820, 640)
        self.cache_dir = QStandardPaths.writableLocation(QStandardPaths.CacheLocation)
        if not os.path.exists(self.cache_dir): os.makedirs(self.cache_dir)
        self.config_file = os.path.join(self.cache_dir, "config.json")
        self.cookie_path = get_default_cookie_path(self.cache_dir)
        self.config = {}
        self.initUI()
        self.load_config()
        self.update_auth_button_state()
        missing = check_runtime_dependencies()
        if missing:
            self.log(format_missing_dependency_message(missing))

    def initUI(self):
        self.setStyleSheet("""
            QWidget { background-color: #f5f7fa; font-family: 'Segoe UI', 'Microsoft YaHei'; }
            QLineEdit, QPlainTextEdit { background-color: #fafafa; border: 1px solid #ccc; border-radius: 5px; padding: 6px; font-size:16px; }
            QPushButton { border-radius: 5px; padding: 6px 12px; font-size:16px; }
        """)
        main_layout = QVBoxLayout(self)

        # Top Bar
        top = QHBoxLayout()
        top.addWidget(QLabel("配置面板", objectName="TitleLabel"))
        top.addStretch()
        temp_btn = QPushButton("临时翻译")
        temp_btn.clicked.connect(self.open_temp)
        temp_btn.setStyleSheet("background-color: #17a2b8; color: white;")
        top.addWidget(temp_btn)
        main_layout.addLayout(top)

        # Config
        form = QFormLayout()
        self.api_keys_text = QPlainTextEdit()
        self.api_keys_text.setFixedHeight(120)
        self.api_keys_text.setPlaceholderText(
            "输入API Keys，一行一个:\ngsk_... (Groq)\ndeepgram:xxxxx (Deepgram)\ngoogle:AIza... (Google)")
        self.req_text = QPlainTextEdit()
        self.req_text.setFixedHeight(100)
        self.req_text.setPlaceholderText("翻译要求(可选)")
        form.addRow("API Keys:", self.api_keys_text)
        form.addRow("翻译要求:", self.req_text)
        main_layout.addLayout(form)

        # Sheet Config
        main_layout.addWidget(QLabel("表格设置"))
        h_sheet = QHBoxLayout()
        self.sheet_link = QLineEdit(placeholderText="Google Sheet 链接")
        btn_open = QPushButton("打开", clicked=self.open_link)
        self.btn_auth = QPushButton(clicked=self.handle_auth)
        self.btn_cookie = QPushButton("更新Cookies", clicked=self.update_cookies)
        h_sheet.addWidget(self.sheet_link)
        h_sheet.addWidget(btn_open)
        h_sheet.addWidget(self.btn_auth)
        h_sheet.addWidget(self.btn_cookie)
        main_layout.addLayout(h_sheet)

        h_cols = QHBoxLayout()
        self.col_link = QLineEdit(placeholderText="链接列(A)")
        self.col_orig = QLineEdit(placeholderText="原文列(B)")
        self.col_trans = QLineEdit(placeholderText="译文列(C)")
        h_cols.addWidget(self.col_link)
        h_cols.addWidget(self.col_orig)
        h_cols.addWidget(self.col_trans)
        main_layout.addLayout(h_cols)

        # Action
        h_btn = QHBoxLayout()
        self.btn_start = QPushButton("开始翻译", clicked=self.toggle)
        self.btn_start.setStyleSheet("background-color: #28a745; color: white; padding: 10px; font-weight: bold;")
        h_btn.addWidget(self.btn_start)
        main_layout.addLayout(h_btn)

        # Log
        h_log = QHBoxLayout()
        h_log.addWidget(QLabel("日志"))
        h_log.addStretch()
        btn_doc = QPushButton("说明文档", clicked=self.open_doc)
        h_log.addWidget(btn_doc)
        main_layout.addLayout(h_log)

        self.log_text = QPlainTextEdit(readOnly=True)
        self.log_text.setFont(QFont("Consolas", 14))
        main_layout.addWidget(self.log_text)

        self.worker = None

    def open_temp(self):
        # 简单检查
        d = TemporaryTranslatorDialog(self.get_config(), self.cookie_path, self)
        d.exec_()

    def open_link(self):
        webbrowser.open(self.sheet_link.text())

    def open_doc(self):
        webbrowser.open(
            "https://docs.google.com/document/d/1XeblksqIxZKbvLpEoMVzMHJJuo7-cwidJZlipzFQhA0/edit?usp=sharing")

    def get_config(self):
        return {
            "api_keys": self.api_keys_text.toPlainText().strip(),
            "translate_require": self.req_text.toPlainText().strip(),
            "sheet_link": self.sheet_link.text().strip(),
            "link_col": self.col_link.text().strip(),
            "original_col": self.col_orig.text().strip(),
            "trans_result_col": self.col_trans.text().strip()
        }

    def handle_auth(self):
        auth = os.path.join(self.cache_dir, "credentials.json")
        token = os.path.join(self.cache_dir, "token.json")
        if os.path.exists(auth):
            if QMessageBox.question(self, "删除", "删除授权文件?") == QMessageBox.Yes:
                if os.path.exists(auth): os.remove(auth)
                if os.path.exists(token): os.remove(token)
                self.log("授权删除")
        else:
            f, _ = QFileDialog.getOpenFileName(self, "选JSON", "", "*.json")
            if f:
                shutil.copy(f, auth)
                self.log("授权载入")
        self.update_auth_button_state()

    def update_auth_button_state(self):
        exists = os.path.exists(os.path.join(self.cache_dir, "credentials.json"))
        self.btn_auth.setText("删除授权" if exists else "载入授权")

    def update_cookies(self):
        if QMessageBox.ok == QMessageBox.information(self, "提示", "请关闭Chrome后点击OK",
                                                     QMessageBox.Ok | QMessageBox.Cancel):
            # 如果 exe/脚本同目录已有 cookies.txt，就更新这个文件；否则更新缓存目录里的 facebook_cookies.txt
            self.cookie_path = get_default_cookie_path(self.cache_dir)
            self.btn_cookie.setEnabled(False)
            self.cw = CookieUpdateWorker(self.cookie_path)
            self.cw.finished.connect(
                lambda s, m: [self.log(m), self.btn_cookie.setEnabled(True), QMessageBox.information(self, "结果", m)])
            self.cw.start()

    def toggle(self):
        if self.worker:
            self.worker.stop()
            self.log("停止中...")
            self.btn_start.setEnabled(False)
        else:
            cfg = self.get_config()
            if not cfg['api_keys'] or not cfg['sheet_link']:
                return QMessageBox.warning(self, "错误", "请填写配置")

            # 检查是否有语音 Key
            _, g, d, u = parse_combined_keys(cfg['api_keys'])
            has_groq = bool(g or any(k.startswith('gsk') for k in u))
            has_dg = bool(d)
            if not has_groq and not has_dg and not LensAPI:
                return QMessageBox.warning(self, "缺失Key", "请至少填写一个 Groq Key 或 Deepgram Key (前缀 deepgram:)")

            self.save_config()
            cfg['cookie_path'] = self.cookie_path
            self.worker = ConversionWorker(cfg)
            self.worker.log_signal.connect(self.log)
            self.worker.finished_signal.connect(self.done)
            self.worker.start()
            self.btn_start.setText("停止")
            self.btn_start.setStyleSheet("background-color: #dc3545; color: white;")

    def done(self):
        self.worker = None
        self.btn_start.setText("开始翻译")
        self.btn_start.setEnabled(True)
        self.btn_start.setStyleSheet("background-color: #28a745; color: white;")
        self.log("任务结束")

    def log(self, t):
        self.log_text.appendPlainText(f"[{time.strftime('%H:%M:%S')}] {t}")

    def save_config(self):
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.get_config(), f, indent=4)
        except:
            pass

    def load_config(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    c = json.load(f)
                    self.api_keys_text.setPlainText(c.get('api_keys', ''))
                    self.req_text.setPlainText(c.get('translate_require', ''))
                    self.sheet_link.setText(c.get('sheet_link', ''))
                    self.col_link.setText(c.get('link_col', ''))
                    self.col_orig.setText(c.get('original_col', ''))
                    self.col_trans.setText(c.get('trans_result_col', ''))
            except:
                pass

    def closeEvent(self, e):
        self.save_config()
        if self.worker: self.worker.stop()
        e.accept()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())