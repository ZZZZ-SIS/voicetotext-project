# 语音与图片翻译工具

这是一个 Windows 桌面工具，用于从 Facebook/视频链接中提取音频，调用 Groq 或 Deepgram 进行语音转文字，并使用 Google Gemini 或 Groq 对文本进行翻译和修正。程序界面基于 PyQt5。

## 主要功能

- 从链接中下载音频并转换为 MP3
- Groq 语音转文字
- Deepgram 语音转文字回落
- Google Gemini / Groq 文本翻译与修正
- Google Sheets 数据读取与写入
- Chrome Cookies 更新
- 图片翻译功能依赖 `chrome-lens-py`

## 本仓库内容

```text
voicetotext.py              主程序源码
requirements.txt            Python 依赖
声音转文字.spec             PyInstaller 打包配置
voice_to_text_icon.ico       程序图标
.github/workflows/release.yml GitHub Actions 自动构建与 Release 配置
```

## 不应提交到仓库的内容

以下内容包含本地环境或敏感信息，不应上传：

```text
Lib/
Scripts/
share/
pyvenv.cfg
credentials.json
token.json
facebook_cookies.txt
config.json
Audio_Downloads/
Video_Downloads/
```

## 本地运行

建议使用 Python 3.12。

```powershell
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python voicetotext.py
```

## 本地打包

```powershell
pip install -r requirements.txt
pyinstaller "声音转文字.spec"
```

打包结果会生成在 `dist/声音转文字/` 目录下。

## Google Sheets 授权说明

程序运行时需要用户手动选择 Google OAuth `credentials.json` 文件。该文件属于用户本地私密文件，不包含在仓库中。

## API Key 说明

程序支持在界面中输入以下 Key：

```text
gsk_...                Groq Key
deepgram:xxxxx         Deepgram Key
google:AIza...         Google Gemini Key
```

API Key 由用户在本地输入，不应硬编码在源码或提交到仓库。
