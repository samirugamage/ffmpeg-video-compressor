# FFmpeg Video Compressor

A lightweight, Windows-ready GUI tool to batch compress MP4 and other video files with **FFmpeg**.  
You can easily adjust **bitrate, quality, resolution**, and more — all without needing a separate FFmpeg install.

---

## ✨ Features
- ✅ **Multi-folder batch mode** — scan entire folders for videos  
- ✅ **Automatic save** — compressed files go into a `compressed_videos` folder  
- ✅ **Pause / Resume** — control long-running jobs with one click  
- ✅ **Quality slider** — balance size vs clarity  
- ✅ **Resolution adjuster** — downscale to save storage  
- ✅ **Bitrate presets** — with hints on quality impact  
- ✅ **Bundled FFmpeg** — no external install needed  
- ✅ **Hardware acceleration** — supports `h264_nvenc`, `h264_qsv`, and `hevc_qsv` on supported GPUs  

---

## 🚀 Installation
1. Download the latest `.exe` from the [Releases](../../releases) page.  
2. Run the `.exe`. No separate FFmpeg setup required.  

⚠️ **Note:** Some antivirus tools may flag the executable as suspicious due to high CPU usage during encoding. This is a **false positive**. Video compression is CPU/GPU intensive, and the program does not contain any malicious code.

---

## 🖥 Usage
1. Launch the app.  
2. Select one or more folders with video files.  
3. Pick your **bitrate, resolution, and quality**.  
4. Start compression.  
5. Find results inside `compressed_videos` next to the originals.  
6. Use **Pause / Resume** as needed during processing.  

---

## 🔧 Development

### Requirements
- Python 3.8+  
- [FFmpeg full build](https://www.gyan.dev/ffmpeg/builds/) (already bundled for users)  

Install dependencies:
```bash
pip install -r requirements.txt
```

Build the executable:
```bash
pyinstaller --onefile --noconsole main.py
```

The output will be in `dist/FFmpegCompressor.exe`.

---

## ⚡ Notes
- **Encoding speed vs size:**  
  - `ultrafast` = faster but bigger files  
  - `veryslow` = slower but smaller, higher quality  
- **GPU acceleration:** Enable if your system supports NVIDIA/Intel GPU encoding.  

---

## 📜 License
MIT License  
Free to use, modify, and share.  
