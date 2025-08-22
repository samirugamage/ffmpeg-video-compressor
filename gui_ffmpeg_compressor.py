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


def find_ffprobe(ffmpeg_path):
    # Try next to ffmpeg
    ffdir = os.path.dirname(ffmpeg_path)
    cand = os.path.join(ffdir, "ffprobe.exe")
    if os.path.exists(cand):
        return cand
    cand = os.path.join(ffdir, "ffprobe")
    if os.path.exists(cand):
        return cand
    on_path = shutil.which("ffprobe")
    return on_path or "ffprobe"


def platform_null_sink():
    return "NUL" if os.name == "nt" else "/dev/null"


class App:
    def __init__(self, root):
        self.root = root
        root.title(APP_TITLE)
        root.geometry("980x860")

        self.ffmpeg_path = find_ffmpeg()
        self.ffprobe_path = find_ffprobe(self.ffmpeg_path)
        self.input_files = []  # absolute paths

        # job control
        self.worker_thread = None
        self.pause_flag = threading.Event()
        self.pause_flag.set()
        self.current_proc = None
        self.is_running = False

        # plan and progress tracking
        self.plan = {}               # path -> {"duration": sec, "passes": 1 or 2}
        self.total_work_units = 0.0  # sum(duration * passes)
        self.work_done_units = 0.0
        self.batch_start_ts = None
        self.cur_file = None
        self.cur_pass_index = 1
        self.cur_pass_count = 1
        self.failed_files = []
        self.last_speed_x = 1.0
        self.cur_file_start_ts = None
        self.cur_file_pass_progress_sec = 0.0

        # settings
        self.mode = tk.StringVar(value="crf")       # "crf" or "bitrate"
        self.crf = tk.IntVar(value=23)
        self.bitrate_kbps = tk.IntVar(value=2500)
        self.twopass = tk.BooleanVar(value=True)    # CPU only

        self.codec = tk.StringVar(value="libx264")
        self.vpreset = tk.StringVar(value="medium")
        self.abitrate = tk.StringVar(value="128k")

        self.res_choice = tk.StringVar(value=RESOLUTION_PRESETS[0][0])
        self.custom_width = tk.IntVar(value=1280)

        # UI progress vars
        self.total_progress_var = tk.DoubleVar(value=0.0)
        self.current_progress_var = tk.DoubleVar(value=0.0)

        self.total_count_lbl_var = tk.StringVar(value="0 / 0")
        self.total_percent_lbl_var = tk.StringVar(value="0.0%")
        self.current_percent_lbl_var = tk.StringVar(value="0.0%")

        self.total_eta_lbl_var = tk.StringVar(value="ETA total: --:--:--")
        self.current_eta_lbl_var = tk.StringVar(value="ETA current: --:--:--")
        self.elapsed_lbl_var = tk.StringVar(value="Elapsed: 00:00:00")
        self.current_name_lbl_var = tk.StringVar(value="Current: —")
        self.current_pass_lbl_var = tk.StringVar(value="Pass: —")
        self.speed_lbl_var = tk.StringVar(value="Speed: —")

        self.completed_count = 0

        # compare & prune summary
        self.prune_summary_var = tk.StringVar(value="Compare & Prune: not run")

        self._build_ui()
        self._update_mode_visibility()
        self._update_suggestions()
        self._log_ffmpeg_version()

        if psutil is None:
            self.log_write("Warning: psutil not installed. Pause/Resume mid-file will be disabled.\n")

        self._ui_tick()

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

        # Progress group
        prog = ttk.LabelFrame(self.root, text="Progress")
        prog.pack(fill="x", **pad)

        ttk.Label(prog, text="Total").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.total_bar = ttk.Progressbar(prog, variable=self.total_progress_var, maximum=100.0)
        self.total_bar.grid(row=0, column=1, sticky="ew", padx=6, pady=4)
        prog.columnconfigure(1, weight=1)
        ttk.Label(prog, textvariable=self.total_percent_lbl_var, width=10, anchor="e").grid(row=0, column=2, padx=6)
        ttk.Label(prog, textvariable=self.total_count_lbl_var, width=12, anchor="e").grid(row=0, column=3, padx=6)

        ttk.Label(prog, text="Current").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        self.cur_bar = ttk.Progressbar(prog, variable=self.current_progress_var, maximum=100.0)
        self.cur_bar.grid(row=1, column=1, sticky="ew", padx=6, pady=4)
        ttk.Label(prog, textvariable=self.current_percent_lbl_var, width=10, anchor="e").grid(row=1, column=2, padx=6)
        ttk.Label(prog, textvariable=self.current_name_lbl_var, anchor="w").grid(row=2, column=0, columnspan=4, sticky="w", padx=6)

        status = ttk.Frame(self.root)
        status.pack(fill="x", **pad)
        ttk.Label(status, textvariable=self.current_eta_lbl_var).pack(side="left", padx=6)
        ttk.Label(status, text="  ").pack(side="left")
        ttk.Label(status, textvariable=self.total_eta_lbl_var).pack(side="left", padx=6)
        ttk.Label(status, text="  ").pack(side="left")
        ttk.Label(status, textvariable=self.elapsed_lbl_var).pack(side="left", padx=6)
        ttk.Label(status, text="  ").pack(side="left")
        ttk.Label(status, textvariable=self.current_pass_lbl_var).pack(side="left", padx=6)
        ttk.Label(status, text="  ").pack(side="left")
        ttk.Label(status, textvariable=self.speed_lbl_var).pack(side="left", padx=6)

        # Compare & Prune controls
        prune_frame = ttk.LabelFrame(self.root, text="Compare & Prune")
        prune_frame.pack(fill="x", **pad)
        ttk.Button(prune_frame, text="Compare & Prune Now", command=self.compare_and_prune_now).pack(side="left", padx=6)
        ttk.Label(prune_frame, textvariable=self.prune_summary_var).pack(side="left", padx=12)

        # Failed list
        fail_frame = ttk.LabelFrame(self.root, text="Videos not compressed")
        fail_frame.pack(fill="both", **pad)
        self.fail_list = tk.Listbox(fail_frame, height=4)
        self.fail_list.pack(fill="both", expand=True, padx=6, pady=6)

        # Log
        log_frame = ttk.LabelFrame(self.root, text="Log")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(log_frame, height=12)
        self.log.pack(fill="both", expand=True, padx=6, pady=6)

        self.log_write(f"Using ffmpeg at: {self.ffmpeg_path}\n")
        self.log_write(f"Using ffprobe at: {self.ffprobe_path}\n")

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
            txt += "\nNote: Two-pass is CPU-only; hardware encoders use single-pass VBR or CQ."
        self.suggestion_lbl.config(text=txt)

    # ---------- Run / Pause / Resume ----------
    def start(self):
        if not self.input_files:
            messagebox.showerror("No files", "Please add at least one video or folder.")
            return

        snapshot_codec = self.codec.get()
        snapshot_mode = self.mode.get()
        snapshot_twopass = bool(self.twopass.get() and not self._is_gpu_encoder(snapshot_codec))

        # Probe durations and build plan
        self.plan.clear()
        total_units = 0.0
        for src in self.input_files:
            dur = self._probe_duration(src)
            passes = 2 if (snapshot_mode == "bitrate" and snapshot_twopass) else 1
            self.plan[src] = {"duration": dur, "passes": passes}
            total_units += dur * passes

        self.total_work_units = max(0.001, total_units)
        self.work_done_units = 0.0
        self.completed_count = 0
        self.failed_files = []
        self.fail_list.delete(0, tk.END)
        self.total_progress_var.set(0.0)
        self.current_progress_var.set(0.0)
        self.total_percent_lbl_var.set("0.0%")
        self.current_percent_lbl_var.set("0.0%")
        self.total_count_lbl_var.set(f"0 / {len(self.input_files)}")
        self.current_eta_lbl_var.set("ETA current: --:--:--")
        self.total_eta_lbl_var.set("ETA total: --:--:--")
        self.elapsed_lbl_var.set("Elapsed: 00:00:00")
        self.current_name_lbl_var.set("Current: —")
        self.current_pass_lbl_var.set("Pass: —")
        self.speed_lbl_var.set("Speed: —")
        self.prune_summary_var.set("Compare & Prune: not run")

        self.start_btn.config(state="disabled")
        self.pause_btn.config(state="normal")
        self.resume_btn.config(state="disabled")
        self.is_running = True
        self.pause_flag.set()
        self.batch_start_ts = time.time()

        self.worker_thread = threading.Thread(
            target=self._run_all,
            args=(snapshot_mode, snapshot_twopass, snapshot_codec),
            daemon=True
        )
        self.worker_thread.start()

    def pause(self):
        if not self.is_running:
            return
        self.pause_flag.clear()
        self.pause_btn.config(state="disabled")
        self.resume_btn.config(state="normal")
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
        if psutil and self.current_proc and self.current_proc.poll() is None:
            try:
                psutil.Process(self.current_proc.pid).resume()
                self.log_write("Resumed (process resumed).\n")
            except Exception as e:
                self.log_write(f"Resume warning: {e}\n")
        self.pause_flag.set()
        self.pause_btn.config(state="normal")
        self.resume_btn.config(state="disabled")

    def _run_all(self, snapshot_mode, snapshot_twopass, snapshot_codec):
        try:
            total_files = len(self.input_files)
            for idx, src in enumerate(list(self.input_files), start=1):
                while not self.pause_flag.is_set():
                    time.sleep(0.2)

                self.cur_file = src
                self.current_name_lbl_var.set(f"Current: {os.path.basename(src)}")
                self.cur_file_start_ts = time.time()

                try:
                    self._convert_one(
                        src,
                        two_pass=(snapshot_mode == "bitrate" and snapshot_twopass),
                        snapshot_codec=snapshot_codec
                    )
                    self.completed_count += 1
                except Exception as e:
                    self.failed_files.append(src)
                    self.fail_list.insert(tk.END, src)
                    self.log_write(f"[ERROR] {os.path.basename(src)} failed. {e}\n")
                    # mark remaining planned work for this file as done to keep ETA advancing
                    planned = self.plan.get(src, {}).get("duration", 0.0) * self.plan.get(src, {}).get("passes", 1)
                    self.work_done_units += max(0.0, planned - self.cur_file_pass_progress_sec)

                self.total_count_lbl_var.set(f"{self.completed_count} / {total_files}")

            # Auto compare and prune after batch
            self._compare_and_prune()

            self.log_write("All done.\n")
            if self.failed_files:
                messagebox.showwarning(
                    "Done with errors",
                    f"Finished with {len(self.failed_files)} failure(s). See list and log."
                )
            else:
                messagebox.showinfo("Done", "All conversions finished.")
        finally:
            self.is_running = False
            self.start_btn.config(state="normal")
            self.pause_btn.config(state="disabled")
            self.resume_btn.config(state="disabled")
            self.current_proc = None
            self.cur_file = None
            self.current_name_lbl_var.set("Current: —")
            self.current_pass_lbl_var.set("Pass: —")
            self.speed_lbl_var.set("Speed: —")
            self.current_eta_lbl_var.set("ETA current: --:--:--")
            self.total_eta_lbl_var.set("ETA total: --:--:--")

    # ---------- Encoding ----------
    def _map_preset_args(self, vcodec, preset):
        if vcodec in ("libx264", "libx265"):
            return ["-preset", preset]
        if vcodec in ("h264_nvenc", "hevc_nvenc"):
            map_nv = {
                "ultrafast": "p1", "superfast": "p2", "veryfast": "p3",
                "faster": "p3", "fast": "p4", "medium": "p4",
                "slow": "p5", "slower": "p6", "veryslow": "p7",
            }
            return ["-preset", map_nv.get(preset, "p4"), "-tune", "hq"]
        if vcodec in ("h264_qsv", "hevc_qsv"):
            return ["-preset", preset]
        if vcodec in ("h264_amf", "hevc_amf"):
            return ["-quality", "quality"]
        return []

    def _convert_one(self, src_path, two_pass, snapshot_codec):
        base = os.path.splitext(os.path.basename(src_path))[0]
        src_dir = os.path.dirname(src_path)
        outdir = os.path.join(src_dir, "compressed videos")
        os.makedirs(outdir, exist_ok=True)
        dst_path = os.path.join(outdir, f"{base}.mp4")

        vcodec = self.codec.get()
        preset = self.vpreset.get()
        ab = self.abitrate.get()

        scale_filter = self._build_scale_filter()
        vf_args = ["-vf", scale_filter] if scale_filter else []

        hw_flags = []
        if vcodec in ("h264_nvenc", "hevc_nvenc"):
            hw_flags = ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
        elif vcodec in ("h264_qsv", "hevc_qsv", "h264_amf", "hevc_amf"):
            hw_flags = ["-hwaccel", "d3d11va"]

        common = [
            self.ffmpeg_path, "-y",
            "-hide_banner",
            "-progress", "pipe:1",
        ] + hw_flags + [
            "-i", src_path,
            "-map", "0:v:0?",
            "-map", "0:a:0?",
            "-c:v", vcodec,
        ] + self._map_preset_args(vcodec, preset) + vf_args

        duration = float(self.plan.get(src_path, {}).get("duration", 0.0))
        self.cur_file_pass_progress_sec = 0.0

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
            self.cur_pass_index = 1
            self.cur_pass_count = 1
            self.current_pass_lbl_var.set("Pass: 1 / 1")
            self._run_ffmpeg_with_progress(args, duration, pass_index=1, pass_count=1)
        else:
            kbps = str(self.bitrate_kbps.get()) + "k"
            if two_pass and vcodec in ("libx264", "libx265"):
                with tempfile.TemporaryDirectory() as td:
                    logf = os.path.join(td, "ffpass.log")
                    # First pass
                    args1 = common + [
                        "-b:v", kbps,
                        "-pass", "1",
                        "-passlogfile", logf,
                        "-an",
                        "-f", "mp4",
                        platform_null_sink()
                    ]
                    self.cur_pass_index = 1
                    self.cur_pass_count = 2
                    self.current_pass_lbl_var.set("Pass: 1 / 2")
                    self._run_ffmpeg_with_progress(args1, duration, pass_index=1, pass_count=2)

                    # Second pass
                    args2 = common + [
                        "-b:v", kbps,
                        "-pass", "2",
                        "-passlogfile", logf,
                        "-c:a", "aac",
                        "-b:a", ab,
                        "-movflags", "+faststart",
                        dst_path
                    ]
                    self.cur_pass_index = 2
                    self.cur_pass_count = 2
                    self.current_pass_lbl_var.set("Pass: 2 / 2")
                    self.cur_file_pass_progress_sec = 0.0
                    self._run_ffmpeg_with_progress(args2, duration, pass_index=2, pass_count=2)
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
                self.cur_pass_index = 1
                self.cur_pass_count = 1
                self.current_pass_lbl_var.set("Pass: 1 / 1")
                self._run_ffmpeg_with_progress(args, duration, pass_index=1, pass_count=1)

        # Validate output immediately
        if not self._is_media_ok(dst_path):
            self.log_write(f"[WARN] Output looks corrupt: {os.path.basename(dst_path)}. Deleting it.\n")
            self._safe_remove(dst_path)
            # mark as failed so it appears in the list
            if src_path not in self.failed_files:
                self.failed_files.append(src_path)
                self.fail_list.insert(tk.END, src_path)
        else:
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
            try:
                w = max(16, int(self.custom_width.get()))
            except Exception:
                w = 1280
            return f"scale={w}:-2"
        w, h = choice
        return f"scale={w}:{h}"

    # ---------- Subprocess driver with progress parsing ----------
    def _run_ffmpeg_with_progress(self, args, duration_sec, pass_index=1, pass_count=1):
        self.log_write(" ".join([self._quote(a) for a in args]) + "\n")
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                universal_newlines=True, bufsize=1)
        self.current_proc = proc
        last_lines = []
        out_time_sec = 0.0
        self.cur_file_pass_progress_sec = 0.0
        self.last_speed_x = 1.0

        try:
            for raw in proc.stdout:
                if raw is None:
                    continue
                line = raw.strip()
                if not line:
                    continue

                last_lines.append(line)
                if len(last_lines) > 120:
                    last_lines.pop(0)

                if "=" in line:
                    key, val = line.split("=", 1)
                else:
                    key, val = "", ""

                if key == "out_time_ms":
                    try:
                        out_time_sec = max(0.0, float(val) / 1_000_000.0)
                    except Exception:
                        pass
                    self.cur_file_pass_progress_sec = out_time_sec

                    cur_pct = 0.0
                    if duration_sec > 0:
                        frac = min(1.0, out_time_sec / duration_sec)
                        cur_pct = frac * 100.0
                    self.current_progress_var.set(cur_pct)
                    self.current_percent_lbl_var.set(f"{cur_pct:.1f}%")

                    worked_units = out_time_sec
                    total_done = self.work_done_units + worked_units
                    total_pct = min(100.0, max(0.0, (total_done / self.total_work_units) * 100.0))
                    self.total_progress_var.set(total_pct)
                    self.total_percent_lbl_var.set(f"{total_pct:.1f}%")

                    eta_cur = self._eta_seconds(
                        remaining_sec=max(0.0, duration_sec - out_time_sec),
                        speed_x=self.last_speed_x
                    )
                    self.current_eta_lbl_var.set(f"ETA current: {self._fmt_hms(eta_cur)}")

                    # remaining units
                    remaining_current_pass = max(0.0, duration_sec - out_time_sec)
                    remaining_passes_this_file = 0.0
                    if pass_count == 2 and pass_index == 1:
                        remaining_passes_this_file += duration_sec

                    remaining_other_files = 0.0
                    after = False
                    for f in self.input_files:
                        if f == self.cur_file:
                            after = True
                            continue
                        if after:
                            info = self.plan.get(f, {"duration": 0.0, "passes": 1})
                            remaining_other_files += info["duration"] * info["passes"]

                    total_remaining_units = remaining_current_pass + remaining_passes_this_file + remaining_other_files
                    eta_total = self._eta_seconds(remaining_sec=total_remaining_units, speed_x=self.last_speed_x)
                    self.total_eta_lbl_var.set(f"ETA total: {self._fmt_hms(eta_total)}")
                    self.speed_lbl_var.set(f"Speed: {self.last_speed_x:.2f}x")
                    self.root.update_idletasks()

                elif key == "speed":
                    try:
                        if val.endswith("x"):
                            self.last_speed_x = max(0.01, float(val[:-1]))
                        else:
                            self.last_speed_x = max(0.01, float(val))
                    except Exception:
                        pass

            proc.wait()
        finally:
            self.current_proc = None

        if proc.returncode != 0:
            tail = "\n".join(last_lines[-40:])
            raise RuntimeError(f"ffmpeg failed with code {proc.returncode}\n\nLast lines:\n{tail}")

        # pass completed successfully, advance work units
        self.work_done_units += duration_sec

    # ---------- Probing and validation ----------
    def _probe_duration(self, path):
        try:
            out = subprocess.check_output(
                [self.ffprobe_path, "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "format=duration", "-of", "csv=p=0", path],
                universal_newlines=True, stderr=subprocess.STDOUT
            )
            dur = float(out.strip())
            if dur > 0:
                return dur
        except Exception:
            pass

        try:
            out = subprocess.check_output(
                [self.ffmpeg_path, "-hide_banner", "-i", path],
                universal_newlines=True, stderr=subprocess.STDOUT
            )
        except subprocess.CalledProcessError as e:
            out = e.output or ""

        for line in out.splitlines():
            line = line.strip()
            if "Duration:" in line:
                try:
                    part = line.split("Duration:", 1)[1].split(",", 1)[0].strip()
                    h, m, s = part.split(":")
                    dur = int(h) * 3600 + int(m) * 60 + float(s)
                    if dur > 0:
                        return dur
                except Exception:
                    pass

        self.log_write(f"[WARN] Could not probe duration for {os.path.basename(path)}. Assuming 60s.\n")
        return 60.0

    def _is_media_ok(self, path):
        # Quick sanity: file must exist and be > 0 bytes
        try:
            if not os.path.exists(path) or os.path.getsize(path) <= 0:
                return False
        except Exception:
            return False

        # Must have a video stream
        try:
            out = subprocess.check_output(
                [self.ffprobe_path, "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=codec_name", "-of", "csv=p=0", path],
                universal_newlines=True, stderr=subprocess.STDOUT
            ).strip()
            if not out:
                return False
        except Exception:
            return False

        # Duration must parse and be positive
        try:
            out = subprocess.check_output(
                [self.ffprobe_path, "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", path],
                universal_newlines=True, stderr=subprocess.STDOUT
            ).strip()
            dur = float(out)
            if not (dur > 0):
                return False
        except Exception:
            return False

        return True

    # ---------- Compare & Prune ----------
    def compare_and_prune_now(self):
        if self.is_running:
            messagebox.showwarning("Busy", "Please wait until current conversions finish before pruning.")
            return
        self._compare_and_prune()

    def _compare_and_prune(self):
        # Scan pairs from the input list. For each original file, look for an mp4 inside its 'compressed videos' subfolder.
        deleted_originals = 0
        deleted_compressed = 0
        corrupt_compressed = 0
        kept_originals = 0
        kept_compressed = 0
        missing_compressed = 0

        seen_fail_added = set()

        for original in self.input_files:
            base = os.path.splitext(os.path.basename(original))[0]
            src_dir = os.path.dirname(original)
            comp_dir = os.path.join(src_dir, "compressed videos")
            comp_path = os.path.join(comp_dir, f"{base}.mp4")

            if not os.path.exists(comp_path):
                missing_compressed += 1
                # mark not compressed
                if original not in self.failed_files and original not in seen_fail_added:
                    self.failed_files.append(original)
                    self.fail_list.insert(tk.END, original)
                    seen_fail_added.add(original)
                continue

            # If compressed exists, validate it
            if not self._is_media_ok(comp_path):
                self.log_write(f"[PRUNE] Compressed file corrupt. Deleting: {comp_path}\n")
                self._safe_remove(comp_path)
                corrupt_compressed += 1
                kept_originals += 1
                continue

            # Optional: you can also validate original, but requirement focuses on compressed corruption.
            # Compare sizes and keep the smaller one
            try:
                orig_sz = os.path.getsize(original)
                comp_sz = os.path.getsize(comp_path)
            except Exception as e:
                self.log_write(f"[PRUNE] Size check failed for {base}: {e}\n")
                continue

            if comp_sz < orig_sz:
                # keep compressed, delete original
                try:
                    self._safe_remove(original)
                    deleted_originals += 1
                    kept_compressed += 1
                    self.log_write(f"[PRUNE] Kept compressed ({self._fmt_bytes(comp_sz)}). Deleted original ({self._fmt_bytes(orig_sz)}): {original}\n")
                except Exception as e:
                    self.log_write(f"[PRUNE] Could not delete original {original}: {e}\n")
            else:
                # keep original, delete compressed (also if equal size)
                try:
                    self._safe_remove(comp_path)
                    deleted_compressed += 1
                    kept_originals += 1
                    self.log_write(f"[PRUNE] Kept original ({self._fmt_bytes(orig_sz)}). Deleted compressed ({self._fmt_bytes(comp_sz)}): {comp_path}\n")
                except Exception as e:
                    self.log_write(f"[PRUNE] Could not delete compressed {comp_path}: {e}\n")

        summary = (
            f"Compare & Prune -> "
            f"kept_orig={kept_originals}, kept_comp={kept_compressed}, "
            f"del_orig={deleted_originals}, del_comp={deleted_compressed}, "
            f"corrupt_comp_deleted={corrupt_compressed}, missing_comp={missing_compressed}"
        )
        self.prune_summary_var.set(summary)
        self.log_write(summary + "\n")

    # ---------- Utils ----------
    def _safe_remove(self, path):
        try:
            os.remove(path)
        except PermissionError:
            # Try to make writable then delete
            try:
                os.chmod(path, 0o666)
                os.remove(path)
            except Exception as e:
                self.log_write(f"[DEL] Permission error removing {path}: {e}\n")
        except Exception as e:
            self.log_write(f"[DEL] Error removing {path}: {e}\n")

    def _fmt_bytes(self, n):
        try:
            n = float(n)
        except Exception:
            return f"{n} B"
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if n < 1024.0:
                return f"{n:.1f} {unit}"
            n /= 1024.0
        return f"{n:.1f} PB"

    def _ui_tick(self):
        if self.batch_start_ts:
            elapsed = int(time.time() - self.batch_start_ts)
            self.elapsed_lbl_var.set(f"Elapsed: {self._fmt_hms(elapsed)}")
        self.root.after(500, self._ui_tick)

    def _eta_seconds(self, remaining_sec, speed_x):
        spd = max(0.05, float(speed_x or 1.0))
        return int(remaining_sec / spd)

    def _fmt_hms(self, secs):
        try:
            s = int(max(0, secs))
        except Exception:
            s = 0
        h = s // 3600
        m = (s % 3600) // 60
        s2 = s % 60
        return f"{h:02d}:{m:02d}:{s2:02d}"

    def log_write(self, s):
        self.log.insert(tk.END, s)
        self.log.see(tk.END)
        self.root.update_idletasks()

    @staticmethod
    def _quote(s):
        if isinstance(s, str) and " " in s:
            return f"\"{s}\""
        return str(s)


def main():
    root = tk.Tk()
    style = ttk.Style()
    try:
        if "vista" in style.theme_names():
            style.theme_use("vista")
        elif "clam" in style.theme_names():
            style.theme_use("clam")
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
