# Audio Separator (Tkinter + Demucs)

一个基于 Python + Tkinter 的桌面音频分离工具，支持上传音频并导出你需要的目标声部。

## 功能

- 上传本地音频文件（wav/mp3/flac/m4a/ogg）
- 选择分离目标：`人声`、`鼓`、`贝斯`、`其他乐器`、`伴奏`
- 处理过程实时状态展示 + 进度条
- 分离完成后可：
  - 直接打开结果目录
  - 导出 ZIP（用于给用户下载）

## 技术方案

- 模型：Demucs `htdemucs`
- GUI：Tkinter
- 音频混合：`numpy + soundfile`
- 打包：PyInstaller

> 首次运行分离时，Demucs 会下载模型权重，需要联网且耗时更长。

## 1. 环境准备（Windows）

```powershell
cd "d:\VS Project\Separate"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

如果你希望强制安装 CPU 版 PyTorch，可用：

```powershell
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
```

## 2. 运行应用

```powershell
python app.py
```

## 3. 打包为 EXE

确保当前环境已安装依赖后执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

默认产物：

- `dist/AudioSeparator/AudioSeparator.exe`

## 4. 使用流程

1. 点击 `上传音频`
2. 勾选需要导出的目标声部
3. 点击 `开始分离`
4. 处理完成后点击：
   - `打开结果目录` 查看文件
   - `下载结果 (ZIP)` 生成压缩包给用户

## 项目结构

- `app.py`：Tkinter 界面与任务调度
- `separator_core.py`：Demucs 调用、进度解析、文件导出
- `build.ps1`：PyInstaller 打包脚本
- `requirements.txt`：依赖列表
