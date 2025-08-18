import os
import sys
import shutil
import threading
import subprocess
import tempfile
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

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

CODEC_CHOICES = [
    ("H.264", "libx264"),
    ("H.265", "libx265"),
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
def find_ffmpeg():
    # First try system PATH (winget-installed ffmpeg)
    sys_ffmpeg = shutil.which("ffmpeg")
    if sys_ffmpeg:
        return sys_ffmpeg

    # Fall back to bundled ffmpeg if available
    if getattr(sys, 'frozen', False):
        meipass = getattr(sys, '_MEIPASS', None)
        if meipass:
            candidate = os.path.join(meipass, "ffmpeg.exe")
            if os.path.exists(candidate):
                return candidate
        exe_dir = os.path.dirname(sys.executable)
        candidate = os.path.join(exe_dir, "ffmpeg.exe")
        if os.path.exists(candidate):
            return candidate

    # As last resort
    return "ffmpeg"


class App:
    def __init__(self, root):
        self.root = root
        root.title(APP_TITLE)
        root.geometry("880x600")

        self.ffmpeg_path = find_ffmpeg()
        self.input_files = []

        self.mode = tk.StringVar(value="crf")       # "crf" or "bitrate"
        self.crf = tk.IntVar(value=23)              # 14 to 35 suggested
        self.bitrate_kbps = tk.IntVar(value=2500)   # for bitrate mode
        self.twopass = tk.BooleanVar(value=True)

        self.codec = tk.StringVar(value="libx264")
        self.vpreset = tk.StringVar(value="medium")
        self.abitrate = tk.StringVar(value="128k")

        self.res_choice = tk.StringVar(value=RESOLUTION_PRESETS[0][0])  # label
        self.custom_width = tk.IntVar(value=1280)

        self.output_dir = tk.StringVar(value="")

        self._build_ui()
        self._update_mode_visibility()
        self._update_suggestions()

    def _build_ui(self):
        pad = {"padx": 8, "pady": 6}

        # Row 0: file chooser
        file_frame = ttk.LabelFrame(self.root, text="Input videos")
        file_frame.pack(fill="x", **pad)

        btn_add = ttk.Button(file_frame, text="Add MP4 files", command=self.add_files)
        btn_add.pack(side="left", padx=6, pady=6)

        btn_remove = ttk.Button(file_frame, text="Remove selected", command=self.remove_selected)
        btn_remove.pack(side="left", padx=6, pady=6)

        self.listbox = tk.Listbox(file_frame, height=5, selectmode=tk.EXTENDED)
        self.listbox.pack(side="left", fill="x", expand=True, padx=6, pady=6)

        # Row 1: output folder
        out_frame = ttk.LabelFrame(self.root, text="Output")
        out_frame.pack(fill="x", **pad)

        ttk.Label(out_frame, text="Folder").pack(side="left", padx=6)
        self.out_entry = ttk.Entry(out_frame, textvariable=self.output_dir, width=60)
        self.out_entry.pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(out_frame, text="Browse", command=self.choose_output_dir).pack(side="left", padx=6)

        # Row 2: video options
        vid_frame = ttk.LabelFrame(self.root, text="Video settings")
        vid_frame.pack(fill="x", **pad)

        # Mode
        mode_frame = ttk.Frame(vid_frame)
        mode_frame.pack(fill="x", **pad)
        ttk.Label(mode_frame, text="Mode").pack(side="left")
        ttk.Radiobutton(mode_frame, text="Quality (CRF)", variable=self.mode, value="crf", command=self._update_mode_visibility).pack(side="left", padx=10)
        ttk.Radiobutton(mode_frame, text="Bitrate (kbps)", variable=self.mode, value="bitrate", command=self._update_mode_visibility).pack(side="left", padx=10)

        # CRF controls
        crf_frame = ttk.Frame(vid_frame)
        crf_frame.pack(fill="x", **pad)
        self.crf_frame = crf_frame

        ttk.Label(crf_frame, text="CRF 14 best to 35 smallest").pack(side="left")
        crf_scale = ttk.Scale(crf_frame, from_=14, to=35, variable=self.crf, command=lambda e: self._update_suggestions())
        crf_scale.pack(side="left", fill="x", expand=True, padx=10)
        self.crf_val_label = ttk.Label(crf_frame, text="23")
        self.crf_val_label.pack(side="left", padx=6)

        def on_crf_change(*_):
            self.crf_val_label.config(text=str(self.crf.get()))
            self._update_suggestions()
        self.crf.trace_add("write", on_crf_change)

        # Bitrate controls
        br_frame = ttk.Frame(vid_frame)
        br_frame.pack(fill="x", **pad)
        self.br_frame = br_frame

        ttk.Label(br_frame, text="Target video bitrate").pack(side="left")
        br_scale = ttk.Scale(br_frame, from_=200, to=20000, variable=self.bitrate_kbps, command=lambda e: self._update_suggestions())
        br_scale.pack(side="left", fill="x", expand=True, padx=10)
        self.br_val_label = ttk.Label(br_frame, text="2500 kbps")
        self.br_val_label.pack(side="left", padx=6)
        self.bitrate_kbps.trace_add("write", lambda *_: self._update_suggestions())

        self.twopass_chk = ttk.Checkbutton(vid_frame, text="Two pass for tighter size", variable=self.twopass)
        self.twopass_chk.pack(anchor="w", padx=12)

        # Codec and preset
        row = ttk.Frame(vid_frame)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="Codec").pack(side="left")
        ttk.Combobox(row, textvariable=self.codec, values=[c for _, c in CODEC_CHOICES], width=10, state="readonly").pack(side="left", padx=8)

        ttk.Label(row, text="Encoder preset").pack(side="left", padx=16)
        ttk.Combobox(row, textvariable=self.vpreset, values=PRESETS, width=10, state="readonly").pack(side="left", padx=8)

        # Resolution
        res_frame = ttk.Frame(vid_frame)
        res_frame.pack(fill="x", **pad)
        ttk.Label(res_frame, text="Resolution").pack(side="left")
        ttk.Combobox(res_frame, textvariable=self.res_choice, values=[label for label, _ in RESOLUTION_PRESETS], width=22, state="readonly").pack(side="left", padx=8)
        ttk.Label(res_frame, text="Custom width").pack(side="left", padx=16)
        self.cw_entry = ttk.Entry(res_frame, textvariable=self.custom_width, width=8)
        self.cw_entry.pack(side="left")
        ttk.Label(res_frame, text="Height keeps aspect").pack(side="left", padx=8)

        # Audio
        aud_frame = ttk.LabelFrame(self.root, text="Audio")
        aud_frame.pack(fill="x", **pad)
        ttk.Label(aud_frame, text="Bitrate").pack(side="left")
        ttk.Combobox(aud_frame, textvariable=self.abitrate, values=AUDIO_BR_CHOICES, width=8, state="readonly").pack(side="left", padx=8)

        # Suggestions
        sug_frame = ttk.LabelFrame(self.root, text="Suggestions")
        sug_frame.pack(fill="x", **pad)
        self.suggestion_lbl = ttk.Label(sug_frame, text="", justify="left")
        self.suggestion_lbl.pack(fill="x", padx=6, pady=4)

        # Run
        run_frame = ttk.Frame(self.root)
        run_frame.pack(fill="x", **pad)
        self.start_btn = ttk.Button(run_frame, text="Start", command=self.start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(run_frame, text="Quit", command=self.root.destroy)
        self.stop_btn.pack(side="left", padx=8)

        # Log
        log_frame = ttk.LabelFrame(self.root, text="Log")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(log_frame, height=12)
        self.log.pack(fill="both", expand=True, padx=6, pady=6)

        # Show current ffmpeg
        self.log_write(f"Using ffmpeg at: {self.ffmpeg_path}\n")

        # React to resolution changes
        def on_res_change(*_):
            self._update_suggestions()
        self.res_choice.trace_add("write", on_res_change)
        self.custom_width.trace_add("write", on_res_change)

    def add_files(self):
        files = filedialog.askopenfilenames(title="Choose MP4 files", filetypes=[("MP4", "*.mp4"), ("All files", "*.*")])
        if not files:
            return
        for f in files:
            if f not in self.input_files:
                self.input_files.append(f)
                self.listbox.insert(tk.END, f)

    def remove_selected(self):
        sel = list(self.listbox.curselection())
        sel.reverse()
        for i in sel:
            path = self.listbox.get(i)
            self.input_files.remove(path)
            self.listbox.delete(i)

    def choose_output_dir(self):
        d = filedialog.askdirectory(title="Choose output folder")
        if d:
            self.output_dir.set(d)

    def _update_mode_visibility(self):
        if self.mode.get() == "crf":
            self.crf_frame.pack_configure()
            self.br_frame.forget()
            self.twopass_chk.state(["disabled"])
        else:
            self.br_frame.pack_configure()
            self.crf_frame.forget()
            self.twopass_chk.state(["!disabled"])
        self._update_suggestions()

    def _estimate_reco_bitrate(self):
        label = self.res_choice.get()
        # Simple pick based on width
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

        # Default guesses for 30 fps H.264
        # These values usually look fine
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
            feel = "Should look fine" if "ok" in bands and kbps >= int(bands.split()[0]) else "May look blocky"
            txt = f"Target bitrate {kbps} kbps. {feel}.\nTypical range for chosen resolution: {bands}."
            if self.twopass.get():
                txt += "\nTwo pass gives tighter sizes for bitrate mode."

        self.suggestion_lbl.config(text=txt)

    def start(self):
        if not self.input_files:
            messagebox.showerror("No files", "Please add at least one video.")
            return
        outdir = self.output_dir.get().strip()
        if not outdir:
            outdir = os.path.dirname(self.input_files[0]) or os.getcwd()
            self.output_dir.set(outdir)
        if not os.path.isdir(outdir):
            messagebox.showerror("Bad folder", "Output folder does not exist.")
            return

        self.start_btn.config(state="disabled")
        t = threading.Thread(target=self._run_all, daemon=True)
        t.start()

    def _run_all(self):
        try:
            for src in self.input_files:
                self._convert_one(src)
            self.log_write("All done.\n")
            messagebox.showinfo("Done", "All conversions finished.")
        except Exception as e:
            self.log_write(f"Error: {e}\n")
            messagebox.showerror("Error", str(e))
        finally:
            self.start_btn.config(state="normal")

    def _convert_one(self, src_path):
        base = os.path.splitext(os.path.basename(src_path))[0]
        outdir = self.output_dir.get().strip() or os.path.dirname(src_path)
        dst_path = os.path.join(outdir, f"{base}_small.mp4")

        vcodec = self.codec.get()
        preset = self.vpreset.get()
        ab = self.abitrate.get()

        scale_filter = self._build_scale_filter()
        vf_args = []
        if scale_filter:
            vf_args = ["-vf", scale_filter]

        common = [
            self.ffmpeg_path, "-y",
            "-hide_banner",
            "-i", src_path,
            "-map", "0:v:0?",
            "-map", "0:a:0?",
            "-c:v", vcodec,
            "-preset", preset,
        ] + vf_args

        if self.mode.get() == "crf":
            crf = str(self.crf.get())
            args = common + [
                "-crf", crf,
                "-c:a", "aac",
                "-b:a", ab,
                "-movflags", "+faststart",
                dst_path
            ]
            self._run_ffmpeg(args)
        else:
            kbps = str(self.bitrate_kbps.get()) + "k"
            if self.twopass.get():
                with tempfile.TemporaryDirectory() as td:
                    logf = os.path.join(td, "ffpass.log")
                    # pass 1
                    args1 = common + [
                        "-b:v", kbps,
                        "-pass", "1",
                        "-passlogfile", logf,
                        "-an",
                        "-f", "mp4",
                        "NUL"
                    ]
                    self._run_ffmpeg(args1)
                    # pass 2
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
                args = common + [
                    "-b:v", kbps,
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
            # Use -2 to keep aspect and align to 2
            return f"scale={w}:-2"
        w, h = choice
        return f"scale={w}:{h}"

    def _run_ffmpeg(self, args):
        self.log_write(" ".join([self._quote(a) for a in args]) + "\n")
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        last = []
        for line in proc.stdout:
            if not line:
                continue
            self.log_write(line)
            last.append(line.rstrip())
            if len(last) > 60:
                last.pop(0)
        proc.wait()
        if proc.returncode != 0:
            tail = "\n".join(last)
            raise RuntimeError(f"ffmpeg failed with code {proc.returncode}\n\nLast lines:\n{tail}")


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
