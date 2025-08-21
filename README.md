# FFmpeg Video Compressor

A simple Windows-ready GUI tool to compress MP4 and other video files using FFmpeg.  
You can adjust **bitrate, quality, resolution**, and now:

- ✅ Select multiple folders  
- ✅ Automatically scan for video files  
- ✅ Compressed videos are saved in a `compressed_videos` folder inside each source folder  
- ✅ Pause and Resume ongoing compression jobs  
- ✅ Works without needing a separate FFmpeg install (bundled in the build)  

---

## Features
- **Quality slider** (balance size vs clarity)  
- **Resolution adjuster** (downscale to save space)  
- **Bitrate presets** with hints on quality impact  
- **Multi-folder batch mode**  
- **Pause/Resume with one click**  

---

## Installation
1. Download the latest release `.exe` from the [Releases](../../releases) page.  
2. Run the `.exe` — no need to install FFmpeg separately.  

---

## Usage
1. Launch the app.   
2. Select one or more folders containing videos.  
3. Choose bitrate, resolution, and quality.  
4. Start compression.  
5. Find your new files inside a `compressed_videos` folder created next to the originals.  
6. Use **Pause/Resume** if you need to temporarily stop encoding.  

---

## Development

### Requirements
- Python 3.8+  
- [FFmpeg full build](https://www.gyan.dev/ffmpeg/builds/) (already bundled for end-users)  

Install dependencies:
```bash
pip install -r requirements.txt
```

# Build the executable:
```bash
pyinstaller --onefile --noconsole main.py
```

This generates dist/FFmpegCompressor.exe.

# Notes
1. Encoding speed depends on codec and preset:
2. ultrafast = larger file, faster
3. veryslow = smaller file, higher quality, slower
4. GPU acceleration available with h264_nvenc, h264_qsv, or hevc_qsv if supported by your hardware.

# License
MIT License
Feel free to use, modify, and share.
