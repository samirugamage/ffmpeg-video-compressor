import os
import sys
import shutil
import threading
import subprocess
import tempfile
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# NEW: process control for pause/resume
try:
    import psutil  # pip install psutil
except Exception:
    psutil = None  # we'll warn in the UI if it's missing

APP_TITLE = "FFmpeg MP4 Compressor"

RESOLUTION_PRESETS = [
    ("Keep original", None),
    ("2160p 3840x2160", (3840, 2160)),
    ("1440p 2560x1440", (2560, 1440)),
    ("1080p 1920x1080", (1920, 1080)),
    ("720p 1280x720",   (1280, 720)),
    ("480p 854x480",    (854, 480)),
    ("360p 640x360",    (640, 360)),
    ("240p 426x240",    (426, 240)),
    ("144p 256x144",    (256, 144)),
    ("Custom width",    "custom"),
]

# CPU and GPU encoders
CODEC_CHOICES = [
    ("H.264 (CPU x264)", "libx264"),
    ("H.265 (CPU x265)", "libx265"),
    ("H.264 (NVIDIA NVENC)", "h264_nvenc"),
    ("H.265 (NVIDIA NVENC)", "hevc_nvenc"),
    ("H.264 (Intel QSV)", "h264_qsv"),
    ("H.265 (Intel QSV)", "hevc_qsv"),
    ("H.264 (AMD AMF)", "h264_amf"),
    ("H.265 (AMD AMF)", "hevc_amf"),
]

PRESETS = [
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
    "veryslow"
]

AUDIO_BR_CHOICES = ["64k", "96k", "128k", "160k", "192k", "256k"]

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm", ".mts", ".m2ts"}


def find_ffmpeg():
    # Prefer ffmpeg.exe next to the packaged EXE (onedir)
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
        bundled = os.path.join(exe_dir, "ffmpeg.exe")
        if os.path.exists(bundled):
            return bundled
        # PyInstaller onefile temp
        meipass = getattr(sys, '_MEIPASS', None)
        if meipass:
            candidate = os.path.join(meipass, "ffmpeg.exe")
            if os.path.exists(candidate):
                return candidate

    # Fallback to PATH
    on_path = shutil.which("ffmpeg")
    if on_path:
        return on_path

    return "ffmpeg"


class App:
    def __init__(self, root):
        self.root = root
        root.title(APP_TITLE)
        root.geometry("900x650")

        self.ffmpeg_path = find_ffmpeg()
        self.input_files = []  # absolute paths

        # job control
        self.worker_thread = None
        self.pause_flag = threading.Event()
        self.pause_flag.set()  # running by default
        self.current_proc = None  # subprocess.Popen for ffmpeg
        self.is_running = False

        self.mode = tk.StringVar(value="crf")       # "crf" or "bitrate"
        self.crf = tk.IntVar(value=23)              # 14..35
        self.bitrate_kbps = tk.IntVar(value=2500)   # for bitrate mode
        self.twopass = tk.BooleanVar(value=True)    # CPU only

        self.codec = tk.StringVar(value="libx264")
        self.vpreset = tk.StringVar(value="medium")
        self.abitrate = tk.StringVar(value="128k")

        self.res_choice = tk.StringVar(value=RESOLUTION_PRESETS[0][0])  # label
        self.custom_width = tk.IntVar(value=1280)

        self._build_ui()
        self._update_mode_visibility()
        self._update_suggestions()
        self._log_ffmpeg_version()

        if psutil is None:
            self.log_write("Warning: psutil not installed. Pause/Resume mid-file will be disabled.\n")

    # ---------- UI ----------
    def _build_ui(self):
        pad = {"padx": 8, "pady": 6}

        # Input
        file_frame = ttk.LabelFrame(self.root, text="Input")
        file_frame.pack(fill="x", **pad)

        ttk.Button(file_frame, text="Add Files", command=self.add_files).pack(side="left", padx=6, pady=6)
        ttk.Button(file_frame, text="Add Folders", command=self.add_folders).pack(side="left", padx=6, pady=6)
        ttk.Button(file_frame, text="Remove selected", command=self.remove_selected).pack(side="left", padx=6, pady=6)

        self.listbox = tk.Listbox(file_frame, height=7, selectmode=tk.EXTENDED)
        self.listbox.pack(side="left", fill="x", expand=True, padx=6, pady=6)

        # Output note
        out_frame = ttk.LabelFrame(self.root, text="Output location")
        out_frame.pack(fill="x", **pad)
        ttk.Label(
            out_frame,
            text="Each source folder will get a subfolder named 'compressed videos' and outputs go there."
        ).pack(side="left", padx=6)

        # Video settings
        vid_frame = ttk.LabelFrame(self.root, text="Video settings")
        vid_frame.pack(fill="x", **pad)

        mode_frame = ttk.Frame(vid_frame)
        mode_frame.pack(fill="x", **pad)
        ttk.Label(mode_frame, text="Mode").pack(side="left")
        ttk.Radiobutton(mode_frame, text="Quality (CRF)", variable=self.mode, value="crf",
                        command=self._update_mode_visibility).pack(side="left", padx=10)
        ttk.Radiobutton(mode_frame, text="Bitrate (kbps)", variable=self.mode, value="bitrate",
                        command=self._update_mode_visibility).pack(side="left", padx=10)

        # CRF
        self.crf_frame = ttk.Frame(vid_frame)
        self.crf_frame.pack(fill="x", **pad)
        ttk.Label(self.crf_frame, text="CRF 14 best  →  35 smallest").pack(side="left")
        crf_scale = ttk.Scale(self.crf_frame, from_=14, to=35, variable=self.crf,
                              command=lambda e: self._update_suggestions())
        crf_scale.pack(side="left", fill="x", expand=True, padx=10)
        self.crf_val_label = ttk.Label(self.crf_frame, text="23")
        self.crf_val_label.pack(side="left", padx=6)
        self.crf.trace_add("write", lambda *_: (self.crf_val_label.config(text=str(self.crf.get())),
                                                self._update_suggestions()))

        # Bitrate
        self.br_frame = ttk.Frame(vid_frame)
        self.br_frame.pack(fill="x", **pad)
        ttk.Label(self.br_frame, text="Target video bitrate").pack(side="left")
        br_scale = ttk.Scale(self.br_frame, from_=200, to=20000, variable=self.bitrate_kbps,
                             command=lambda e: self._update_suggestions())
        br_scale.pack(side="left", fill="x", expand=True, padx=10)
        self.br_val_label = ttk.Label(self.br_frame, text="2500 kbps")
        self.br_val_label.pack(side="left", padx=6)
        self.bitrate_kbps.trace_add("write", lambda *_: self._update_suggestions())

        self.twopass_chk = ttk.Checkbutton(vid_frame, text="Two pass for tighter size (CPU only)",
                                           variable=self.twopass)
        self.twopass_chk.pack(anchor="w", padx=12)

        # Codec + preset
        row = ttk.Frame(vid_frame)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="Codec").pack(side="left")
        codec_combo = ttk.Combobox(row, textvariable=self.codec,
                                   values=[c for _, c in CODEC_CHOICES],
                                   width=28, state="readonly")
        codec_combo.pack(side="left", padx=8)
        codec_combo.bind("<<ComboboxSelected>>", lambda e: self._update_mode_visibility())

        ttk.Label(row, text="Encoder preset").pack(side="left", padx=16)
        ttk.Combobox(row, textvariable=self.vpreset, values=PRESETS, width=10,
                     state="readonly").pack(side="left", padx=8)

        # Resolution
        res_frame = ttk.Frame(vid_frame)
        res_frame.pack(fill="x", **pad)
        ttk.Label(res_frame, text="Resolution").pack(side="left")
        ttk.Combobox(res_frame, textvariable=self.res_choice,
                     values=[label for label, _ in RESOLUTION_PRESETS],
                     width=22, state="readonly").pack(side="left", padx=8)
        ttk.Label(res_frame, text="Custom width").pack(side="left", padx=16)
        self.cw_entry = ttk.Entry(res_frame, textvariable=self.custom_width, width=8)
        self.cw_entry.pack(side="left")
        ttk.Label(res_frame, text="Height keeps aspect").pack(side="left", padx=8)

        # Audio
        aud_frame = ttk.LabelFrame(self.root, text="Audio")
        aud_frame.pack(fill="x", **pad)
        ttk.Label(aud_frame, text="Bitrate").pack(side="left")
        ttk.Combobox(aud_frame, textvariable=self.abitrate, values=AUDIO_BR_CHOICES,
                     width=8, state="readonly").pack(side="left", padx=8)

        # Suggestions
        sug_frame = ttk.LabelFrame(self.root, text="Suggestions")
        sug_frame.pack(fill="x", **pad)
        self.suggestion_lbl = ttk.Label(sug_frame, text="", justify="left")
        self.suggestion_lbl.pack(fill="x", padx=6, pady=4)

        # Controls
        run_frame = ttk.Frame(self.root)
        run_frame.pack(fill="x", **pad)
        self.start_btn = ttk.Button(run_frame, text="Start", command=self.start)
        self.start_btn.pack(side="left")
        self.pause_btn = ttk.Button(run_frame, text="Pause", command=self.pause, state="disabled")
        self.pause_btn.pack(side="left", padx=6)
        self.resume_btn = ttk.Button(run_frame, text="Resume", command=self.resume, state="disabled")
        self.resume_btn.pack(side="left")
        self.stop_btn = ttk.Button(run_frame, text="Quit", command=self.root.destroy)
        self.stop_btn.pack(side="left", padx=8)

        # Log
        log_frame = ttk.LabelFrame(self.root, text="Log")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(log_frame, height=15)
        self.log.pack(fill="both", expand=True, padx=6, pady=6)

        # Show ffmpeg path
        self.log_write(f"Using ffmpeg at: {self.ffmpeg_path}\n")

        # react to resolution changes
        self.res_choice.trace_add("write", lambda *_: self._update_suggestions())
        self.custom_width.trace_add("write", lambda *_: self._update_suggestions())

    # ---------- Input ops ----------
    def add_files(self):
        files = filedialog.askopenfilenames(
            title="Choose video files",
            filetypes=[("Video", "*.mp4 *.mov *.mkv *.m4v *.avi *.webm *.mts *.m2ts"),
                       ("All files", "*.*")]
        )
        if not files:
            return
        self._append_files(files)

    def add_folders(self):
        # Tk doesn't support multi-select directories, so loop until user cancels
        while True:
            folder = filedialog.askdirectory(title="Choose a folder (Cancel to finish)")
            if not folder:
                break
            self._scan_and_add(folder)

    def _scan_and_add(self, folder):
        count = 0
        for root, _, files in os.walk(folder):
            for name in files:
                ext = os.path.splitext(name)[1].lower()
                if ext in VIDEO_EXTS:
                    path = os.path.abspath(os.path.join(root, name))
                    if path not in self.input_files:
                        self.input_files.append(path)
                        self.listbox.insert(tk.END, path)
                        count += 1
        self.log_write(f"Scanned '{folder}', added {count} video(s).\n")

    def _append_files(self, files):
        added = 0
        for f in files:
            path = os.path.abspath(f)
            if path not in self.input_files:
                self.input_files.append(path)
                self.listbox.insert(tk.END, path)
                added += 1
        if added:
            self.log_write(f"Added {added} file(s).\n")

    def remove_selected(self):
        sel = list(self.listbox.curselection())
        sel.reverse()
        for i in sel:
            path = self.listbox.get(i)
            if path in self.input_files:
                self.input_files.remove(path)
            self.listbox.delete(i)

    # ---------- Behavior ----------
    def _log_ffmpeg_version(self):
        try:
            out = subprocess.check_output([self.ffmpeg_path, "-version"],
                                          universal_newlines=True, stderr=subprocess.STDOUT)
            if out:
                self.log_write(out.splitlines()[0] + "\n")
        except Exception as e:
            self.log_write(f"Could not run ffmpeg -version: {e}\n")

    def _is_gpu_encoder(self, enc):
        return enc in ("h264_nvenc", "hevc_nvenc", "h264_qsv", "hevc_qsv", "h264_amf", "hevc_amf")

    def _update_mode_visibility(self):
        if self.mode.get() == "crf":
            self.crf_frame.pack_configure()
            self.br_frame.forget()
        else:
            self.br_frame.pack_configure()
            self.crf_frame.forget()

        if self._is_gpu_encoder(self.codec.get()):
            self.twopass_chk.state(["disabled"])
        else:
            self.twopass_chk.state(["!disabled"])

        self._update_suggestions()

    def _estimate_reco_bitrate(self):
        label = self.res_choice.get()
        width = None
        for l, v in RESOLUTION_PRESETS:
            if l == label:
                if v == "custom":
                    width = self.custom_width.get()
                elif v is None:
                    width = None
                else:
                    width = v[0]
                break

        if width is None:
            return "Depends on source resolution"
        if width >= 3840:
            return "8000 to 20000 kbps usually ok"
        if width >= 2560:
            return "6000 to 12000 kbps usually ok"
        if width >= 1920:
            return "4000 to 8000 kbps usually ok"
        if width >= 1280:
            return "2000 to 4000 kbps usually ok"
        if width >= 854:
            return "1000 to 2000 kbps usually ok"
        if width >= 640:
            return "800 to 1500 kbps usually ok"
        if width >= 426:
            return "500 to 900 kbps usually ok"
        return "300 to 700 kbps usually ok"

    def _update_suggestions(self):
        if self.mode.get() == "crf":
            c = self.crf.get()
            self.crf_val_label.config(text=str(c))
            if c <= 18:
                msg = "Near lossless. Big file."
            elif c <= 22:
                msg = "High quality. Good size cut."
            elif c <= 26:
                msg = "Good default. Minor loss on a phone."
            elif c <= 30:
                msg = "Noticeable softness. Small file."
            else:
                msg = "Strong artifacts. Very small file."
            txt = f"CRF {c}. {msg}\nUse H.264 for best compatibility. H.265 is smaller at the same quality."
        else:
            kbps = self.bitrate_kbps.get()
            self.br_val_label.config(text=f"{kbps} kbps")
            bands = self._estimate_reco_bitrate()
            try:
                first_num = int(bands.split()[0])
                feel = "Should look fine" if kbps >= first_num else "May look blocky"
            except Exception:
                feel = "Depends on content"
            txt = f"Target bitrate {kbps} kbps. {feel}.\nTypical range for chosen resolution: {bands}."
            if self.twopass.get() and not self._is_gpu_encoder(self.codec.get()):
                txt += "\nTwo pass gives tighter sizes for bitrate mode."
        if self._is_gpu_encoder(self.codec.get()):
            txt += "\nNote: Two-pass is CPU-only; hardware encoders use single-pass VBR/CQ."
        self.suggestion_lbl.config(text=txt)

    # ---------- Run / Pause / Resume ----------
    def start(self):
        if not self.input_files:
            messagebox.showerror("No files", "Please add at least one video or folder.")
            return

        self.start_btn.config(state="disabled")
        self.pause_btn.config(state="normal")
        self.resume_btn.config(state="disabled")
        self.is_running = True
        self.pause_flag.set()

        self.worker_thread = threading.Thread(target=self._run_all, daemon=True)
        self.worker_thread.start()

    def pause(self):
        if not self.is_running:
            return
        self.pause_flag.clear()  # pause between files
        self.pause_btn.config(state="disabled")
        self.resume_btn.config(state="normal")
        # try mid-file pause via psutil
        if psutil and self.current_proc and self.current_proc.poll() is None:
            try:
                psutil.Process(self.current_proc.pid).suspend()
                self.log_write("Paused (process suspended).\n")
            except Exception as e:
                self.log_write(f"Pause warning: {e}\n")
        else:
            self.log_write("Pause requested. Will pause before next file.\n")

    def resume(self):
        if not self.is_running:
            return
        # resume process first
        if psutil and self.current_proc and self.current_proc.poll() is None:
            try:
                psutil.Process(self.current_proc.pid).resume()
                self.log_write("Resumed (process resumed).\n")
            except Exception as e:
                self.log_write(f"Resume warning: {e}\n")
        self.pause_flag.set()
        self.pause_btn.config(state="normal")
        self.resume_btn.config(state="disabled")

    def _run_all(self):
        try:
            for src in list(self.input_files):
                # allow pause between files
                while not self.pause_flag.is_set():
                    time.sleep(0.2)

                self._convert_one(src)
            self.log_write("All done.\n")
            messagebox.showinfo("Done", "All conversions finished.")
        except Exception as e:
            self.log_write(f"Error: {e}\n")
            messagebox.showerror("Error", str(e))
        finally:
            self.is_running = False
            self.start_btn.config(state="normal")
            self.pause_btn.config(state="disabled")
            self.resume_btn.config(state="disabled")
            self.current_proc = None

    # ---------- Encoding ----------
    def _map_preset_args(self, vcodec, preset):
        if vcodec in ("libx264", "libx265"):
            return ["-preset", preset]
        if vcodec in ("h264_nvenc", "hevc_nvenc"):
            map_nv = {
                "ultrafast": "p1",
                "superfast": "p2",
                "veryfast":  "p3",
                "faster":     "p3",
                "fast":       "p4",
                "medium":     "p4",
                "slow":       "p5",
                "slower":     "p6",
                "veryslow":   "p7",
            }
            return ["-preset", map_nv.get(preset, "p4"), "-tune", "hq"]
        if vcodec in ("h264_qsv", "hevc_qsv"):
            return ["-preset", preset]
        if vcodec in ("h264_amf", "hevc_amf"):
            return ["-quality", "quality"]
        return []

    def _convert_one(self, src_path):
        base = os.path.splitext(os.path.basename(src_path))[0]

        # NEW: per-source folder output subdir
        src_dir = os.path.dirname(src_path)
        outdir = os.path.join(src_dir, "compressed videos")
        os.makedirs(outdir, exist_ok=True)

        dst_path = os.path.join(outdir, f"{base}.mp4")  # same name, separate folder

        vcodec = self.codec.get()
        preset = self.vpreset.get()
        ab = self.abitrate.get()

        scale_filter = self._build_scale_filter()
        vf_args = []
        if scale_filter:
            vf_args = ["-vf", scale_filter]

        # Decode HW accel when using GPU encoders
        hw_flags = []
        if vcodec in ("h264_nvenc", "hevc_nvenc"):
            hw_flags = ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
        elif vcodec in ("h264_qsv", "hevc_qsv", "h264_amf", "hevc_amf"):
            hw_flags = ["-hwaccel", "d3d11va"]

        common = [
            self.ffmpeg_path, "-y",
            "-hide_banner",
        ] + hw_flags + [
            "-i", src_path,
            "-map", "0:v:0?",
            "-map", "0:a:0?",
            "-c:v", vcodec,
        ] + self._map_preset_args(vcodec, preset) + vf_args

        if self.mode.get() == "crf":
            c = int(self.crf.get())
            v_args = []
            if vcodec in ("h264_nvenc", "hevc_nvenc"):
                cq = max(14, min(35, c))
                v_args = ["-rc", "vbr", "-cq", str(cq), "-tune", "hq"]
            elif vcodec in ("h264_qsv", "hevc_qsv"):
                gq = max(16, min(35, c))
                v_args = ["-global_quality", str(gq)]
            elif vcodec in ("h264_amf", "hevc_amf"):
                qp = max(18, min(35, c))
                v_args = ["-rc", "vbr_latency", "-quality", "quality",
                          "-qp_i", str(qp - 1), "-qp_p", str(qp), "-qp_b", str(qp + 2)]
            else:
                v_args = ["-crf", str(c)]

            args = common + v_args + [
                "-c:a", "aac",
                "-b:a", ab,
                "-movflags", "+faststart",
                dst_path
            ]
            self._run_ffmpeg(args)

        else:
            kbps = str(self.bitrate_kbps.get()) + "k"
            if self.twopass.get() and vcodec in ("libx264", "libx265"):
                with tempfile.TemporaryDirectory() as td:
                    logf = os.path.join(td, "ffpass.log")
                    args1 = common + [
                        "-b:v", kbps,
                        "-pass", "1",
                        "-passlogfile", logf,
                        "-an",
                        "-f", "mp4",
                        "NUL"
                    ]
                    self._run_ffmpeg(args1)
                    args2 = common + [
                        "-b:v", kbps,
                        "-pass", "2",
                        "-passlogfile", logf,
                        "-c:a", "aac",
                        "-b:a", ab,
                        "-movflags", "+faststart",
                        dst_path
                    ]
                    self._run_ffmpeg(args2)
            else:
                v_args = []
                if vcodec in ("h264_nvenc", "hevc_nvenc"):
                    v_args = ["-rc", "vbr", "-b:v", kbps, "-tune", "hq"]
                elif vcodec in ("h264_qsv", "hevc_qsv"):
                    v_args = ["-b:v", kbps]
                elif vcodec in ("h264_amf", "hevc_amf"):
                    v_args = ["-rc", "vbr_latency", "-b:v", kbps, "-quality", "quality"]
                else:
                    v_args = ["-b:v", kbps]

                args = common + v_args + [
                    "-c:a", "aac",
                    "-b:a", ab,
                    "-movflags", "+faststart",
                    dst_path
                ]
                self._run_ffmpeg(args)

        self.log_write(f"Saved: {dst_path}\n")

    def _build_scale_filter(self):
        label = self.res_choice.get()
        choice = None
        for l, v in RESOLUTION_PRESETS:
            if l == label:
                choice = v
                break
        if choice is None:
            return None
        if choice == "custom":
            w = max(16, int(self.custom_width.get()))
            return f"scale={w}:-2"
        w, h = choice
        return f"scale={w}:{h}"

    # ---------- Subprocess driver with pause-aware UI ----------
    def _run_ffmpeg(self, args):
        self.log_write(" ".join([self._quote(a) for a in args]) + "\n")
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        self.current_proc = proc
        last = []
        try:
            for line in proc.stdout:
                if not line:
                    continue
                self.log_write(line)
                last.append(line.rstrip())
                if len(last) > 80:
                    last.pop(0)
        finally:
            proc.wait()
            self.current_proc = None

        if proc.returncode != 0:
            tail = "\n".join(last)
            raise RuntimeError(f"ffmpeg failed with code {proc.returncode}\n\nLast lines:\n{tail}")

    # ---------- Utils ----------
    def log_write(self, s):
        self.log.insert(tk.END, s)
        self.log.see(tk.END)
        self.root.update_idletasks()

    @staticmethod
    def _quote(s):
        if " " in s:
            return f"\"{s}\""
        return s


def main():
    root = tk.Tk()
    style = ttk.Style()
    if "vista" in style.theme_names():
        style.theme_use("vista")
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
