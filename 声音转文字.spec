# -*- mode: python ; coding: utf-8 -*-

import os
from PyInstaller.utils.hooks import collect_all

# 自动收集 PyInstaller 容易漏掉的动态依赖、资源文件和二进制文件。
packages_to_collect = [
    'chrome_lens_py',
    'google.protobuf',
    'google.genai',
    'googleapiclient',
    'google_auth_oauthlib',
    'google.auth',
    'gspread',
    'yt_dlp',
    'imageio_ffmpeg',
    'opencc',
    'grpc',
    'pydantic',
    'cryptography',
]

all_datas = []
all_binaries = []
all_hiddenimports = []

for package_name in packages_to_collect:
    try:
        datas, binaries, hiddenimports = collect_all(package_name)
        all_datas += datas
        all_binaries += binaries
        all_hiddenimports += hiddenimports
    except Exception:
        pass

manual_hiddenimports = [
    'chrome_lens_py',
    'betterproto',
    'google.protobuf',
    'google.genai',
    'googleapiclient',
    'googleapiclient.discovery',
    'google_auth_oauthlib',
    'google_auth_oauthlib.flow',
    'google.auth',
    'google.auth.transport.requests',
    'gspread',
    'yt_dlp',
    'imageio_ffmpeg',
    'opencc',
    'grpc',
    'pydantic',
    'cryptography',
]

# 这些文件如果存在，会被打进 _internal；同时建议发布时也放一份到 exe 同目录，便于用户替换。
optional_datas = []
for filename in [
    'voice_to_text_icon.ico',
    'cookies.txt',
    'facebook_cookies.txt',
    'credentials.json',
    'token.json',
]:
    if os.path.exists(filename):
        optional_datas.append((filename, '.'))


a = Analysis(
    ['voicetotext.py'],
    pathex=[],
    binaries=all_binaries,
    datas=all_datas + optional_datas,
    hiddenimports=manual_hiddenimports + all_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='声音转文字',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['voice_to_text_icon.ico'] if os.path.exists('voice_to_text_icon.ico') else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='声音转文字',
)
