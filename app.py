from __future__ import annotations

import os
import queue
import shutil
import threading
import traceback
import zipfile
import tkinter as tk
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, VERTICAL, W, X, Y, BooleanVar, StringVar, Tk, filedialog, messagebox
from tkinter import ttk
import pygame

from separator_core import AVAILABLE_TARGETS, SeparationResult, separate_audio

TARGET_LABELS = {
    "vocals": "Vocals (人声)",
    "drums": "Drums (鼓)",
    "bass": "Bass (贝斯)",
    "other": "Other (其他乐器)",
    "accompaniment": "Accompaniment (伴奏)",
}

AUDIO_FILETYPES = [
    ("Audio files", "*.wav *.mp3 *.flac *.m4a *.ogg"),
    ("All files", "*.*"),
]


class CustomProgress(tk.Canvas):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, height=24, bg="#e0e0e0", highlightthickness=0, **kwargs)
        self.rect = self.create_rectangle(0, 0, 0, 24, fill="#4caf50", width=0)
        self.bind("<Configure>", self._on_resize)
        self._percent = 0.0

    def set_progress(self, percent: float):
        self._percent = max(0.0, min(100.0, percent))
        self._update_rect()

    def _on_resize(self, event):
        self._update_rect()

    def _update_rect(self):
        width = self.winfo_width() * (self._percent / 100.0)
        self.coords(self.rect, 0, 0, width, self.winfo_height())


class AudioSeparatorApp:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title("Audio Separator")
        
        window_width = 800
        window_height = 450  # 缩小高度，因为移除了下方的预览框
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        center_x = int(screen_width / 2 - window_width / 2)
        center_y = int(screen_height / 2 - window_height / 2)
        
        self.root.geometry(f"{window_width}x{window_height}+{center_x}+{center_y}")
        self.root.minsize(740, 400)

        pygame.mixer.init()

        import sys
        if getattr(sys, 'frozen', False):
            self.workspace_dir = Path(sys.executable).resolve().parent
        else:
            self.workspace_dir = Path(__file__).resolve().parent
            
        self.input_path_var = StringVar()
        self.status_var = StringVar(value="请选择音频文件并设置要导出的声部。")

        self.progress_var = tk_double_var(self.root, 0.0)
        self.event_queue: queue.Queue[tuple[str, object]] = queue.Queue()

        self.processing = False
        self.latest_result: SeparationResult | None = None
        self._last_log_message = ""
        self.current_playing_file: Path | None = None

        self.target_vars: dict[str, BooleanVar] = {
            target: BooleanVar(value=(target == "vocals")) for target in AVAILABLE_TARGETS
        }

        self._build_ui()
        self._poll_events()

    def _build_ui(self) -> None:
        main_frame = ttk.Frame(self.root, padding=16)
        main_frame.pack(fill=BOTH, expand=True)

        title = ttk.Label(main_frame, text="音频人声/乐器分离", font=("Segoe UI", 18, "bold"))
        title.pack(anchor=W)

        subtitle = ttk.Label(
            main_frame,
            text="上传音频后，选择要导出的目标声部。首次运行会自动下载模型，耗时会更久。",
        )
        subtitle.pack(anchor=W, pady=(6, 14))

        file_frame = ttk.Frame(main_frame)
        file_frame.pack(fill="x")

        file_entry = ttk.Entry(file_frame, textvariable=self.input_path_var)
        file_entry.pack(side=LEFT, fill="x", expand=True)

        self.browse_button = ttk.Button(file_frame, text="上传音频", command=self._choose_file)
        self.browse_button.pack(side=RIGHT, padx=(10, 0))

        targets_labelframe = ttk.LabelFrame(main_frame, text="分离目标", padding=12)
        targets_labelframe.pack(fill="x", pady=12)

        for idx, target in enumerate(AVAILABLE_TARGETS):
            check = ttk.Checkbutton(
                targets_labelframe,
                text=TARGET_LABELS[target],
                variable=self.target_vars[target],
            )
            row = idx // 2
            col = idx % 2
            check.grid(row=row, column=col, sticky=W, padx=8, pady=4)

        progress_frame = ttk.Frame(main_frame)
        progress_frame.pack(fill="x", pady=(6, 0))

        self.progress = CustomProgress(progress_frame)
        self.progress.pack(fill="x", pady=(0, 4))

        status_label = ttk.Label(main_frame, textvariable=self.status_var)
        status_label.pack(anchor=W, pady=(8, 6))

        button_row = ttk.Frame(main_frame)
        button_row.pack(fill="x", pady=(4, 10))

        self.start_button = ttk.Button(button_row, text="开始分离", command=self._start_separation)
        self.start_button.pack(side=LEFT)

        info_label = ttk.Label(main_frame, text="Author Email: eatonchan0127@gmail.com", foreground="gray")
        info_label.pack(side="bottom", anchor="se", pady=(10, 0))

    def _choose_file(self) -> None:
        selected = filedialog.askopenfilename(title="选择音频文件", filetypes=AUDIO_FILETYPES)
        if selected:
            self._stop_playback()
            self.input_path_var.set(selected)
            self.latest_result = None
            self.progress.set_progress(0.0)
            self.status_var.set("文件已选择，点击“开始分离”开始处理。")

    def _start_separation(self) -> None:
        if self.processing:
            return

        raw_path = self.input_path_var.get().strip()
        if not raw_path:
            messagebox.showwarning("缺少音频", "请先上传一个音频文件。")
            return

        input_path = Path(raw_path)
        if not input_path.exists():
            messagebox.showerror("文件不存在", f"找不到文件:\n{input_path}")
            return

        selected_targets = [target for target, var in self.target_vars.items() if var.get()]
        if not selected_targets:
            messagebox.showwarning("缺少目标", "至少选择一个分离目标。")
            return

        self.processing = True
        self.latest_result = None
        self._last_log_message = ""
        self.progress.set_progress(0.0)
        self.status_var.set("任务已开始，正在排队处理...")
        self._set_busy_controls(is_busy=True)
        self._stop_playback()

        worker = threading.Thread(
            target=self._run_worker,
            args=(input_path, selected_targets),
            daemon=True,
        )
        worker.start()

    def _run_worker(self, input_path: Path, selected_targets: list[str]) -> None:
        try:
            def callback(progress: float, message: str) -> None:
                self.event_queue.put(("progress", (progress, message)))

            result = separate_audio(
                input_audio=input_path,
                selected_targets=selected_targets,
                workspace_dir=self.workspace_dir,
                callback=callback,
            )
            self.event_queue.put(("done", result))
        except Exception as exc:  # noqa: BLE001
            details = "\n".join(traceback.format_exception_only(type(exc), exc)).strip()
            self.event_queue.put(("error", details))

    def _poll_events(self) -> None:
        while True:
            try:
                event, payload = self.event_queue.get_nowait()
            except queue.Empty:
                break

            if event == "progress":
                progress, message = payload  # type: ignore[misc]
                self._update_progress(float(progress), str(message))
            elif event == "done":
                self._handle_done(payload)  # type: ignore[arg-type]
            elif event == "error":
                self._handle_error(str(payload))

        self.root.after(120, self._poll_events)

    def _update_progress(self, progress: float, message: str) -> None:
        clamped = max(0.0, min(100.0, progress))
        self.progress.set_progress(clamped)
        self.status_var.set(message)
        if message and message != self._last_log_message:
            self._last_log_message = message

    def _handle_done(self, result: SeparationResult) -> None:
        self.processing = False
        self.latest_result = result
        self.progress.set_progress(100.0)
        self.status_var.set(f"分离完成，共导出 {len(result.files)} 个文件。")

        self._build_preview_list(result)

        self._set_busy_controls(is_busy=False)
        messagebox.showinfo("完成", "处理完成，可以在下方试听并选择需要的音轨进行另存为。")

    def _handle_error(self, details: str) -> None:
        self.processing = False
        self.progress.set_progress(0.0)
        self.status_var.set("分离失败，请检查依赖或重试。")
        self._set_busy_controls(is_busy=False)
        messagebox.showerror("处理失败", details)

    def _set_busy_controls(self, is_busy: bool) -> None:
        state = "disabled" if is_busy else "normal"
        self.start_button.configure(state=state)
        self.browse_button.configure(state=state)

    def _build_preview_list(self, result: SeparationResult) -> None:
        preview_win = tk.Toplevel(self.root)
        preview_win.title("音频预览与下载")
        
        window_width = 600
        window_height = 400
        screen_width = preview_win.winfo_screenwidth()
        screen_height = preview_win.winfo_screenheight()
        center_x = int(screen_width / 2 - window_width / 2)
        center_y = int(screen_height / 2 - window_height / 2)
        preview_win.geometry(f"{window_width}x{window_height}+{center_x}+{center_y}")
        preview_win.minsize(500, 300)
        
        # 窗口关闭时停止播放
        preview_win.protocol("WM_DELETE_WINDOW", lambda: (self._stop_playback(), preview_win.destroy()))

        main_frame = ttk.Frame(preview_win, padding=16)
        main_frame.pack(fill=BOTH, expand=True)

        canvas = tk.Canvas(main_frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=canvas.yview)
        preview_inner = ttk.Frame(canvas)
        
        preview_inner.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=preview_inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar.pack(side=RIGHT, fill=Y)

        for idx, file_path in enumerate(result.files):
            row = ttk.Frame(preview_inner)
            row.pack(fill="x", pady=6)
            
            name_label = ttk.Label(row, text=file_path.name, width=35, anchor=W)
            name_label.pack(side=LEFT, padx=(5, 10))

            play_btn = ttk.Button(row, text="▶ 试听", command=lambda fp=file_path: self._play_audio(fp))
            play_btn.pack(side=LEFT, padx=5)

            stop_btn = ttk.Button(row, text="⏹ 停止", command=self._stop_playback)
            stop_btn.pack(side=LEFT, padx=5)

            save_btn = ttk.Button(row, text="💾 另存为...", command=lambda fp=file_path: self._save_file_as(fp))
            save_btn.pack(side=RIGHT, padx=5)

    def _play_audio(self, file_path: Path) -> None:
        try:
            pygame.mixer.music.load(str(file_path))
            pygame.mixer.music.play()
        except Exception as e:
            messagebox.showerror("播放失败", f"无法播放该音频: {e}")

    def _stop_playback(self) -> None:
        try:
            if pygame.mixer.music.get_busy():
                pygame.mixer.music.stop()
        except Exception:
            pass

    def _save_file_as(self, file_path: Path) -> None:
        target = filedialog.asksaveasfilename(
            title="另存为",
            defaultextension=".wav",
            filetypes=[("WAV Audio", "*.wav"), ("All files", "*.*")],
            initialfile=file_path.name,
        )
        if target:
            try:
                shutil.copy2(file_path, target)
                messagebox.showinfo("保存成功", f"文件已保存至:\n{target}")
            except Exception as e:
                messagebox.showerror("保存失败", f"无法保存文件: {e}")


def tk_double_var(root: Tk, value: float):
    import tkinter as tk

    return tk.DoubleVar(master=root, value=value)


def main() -> None:
    import sys
    import multiprocessing
    multiprocessing.freeze_support()
    
    if len(sys.argv) >= 3 and sys.argv[1] == "-m" and sys.argv[2] == "demucs.separate":
        sys.argv = [sys.argv[0]] + sys.argv[3:]
        
        try:
            import torchaudio
            def _patched_load(uri, *args, **kwargs):
                import soundfile as sf
                import torch
                data, sr = sf.read(str(uri), always_2d=True)
                return torch.from_numpy(data.T).float(), sr
            def _patched_save(uri, src, sample_rate, *args, **kwargs):
                import soundfile as sf
                sf.write(str(uri), src.numpy().T, sample_rate, subtype="PCM_16")
            torchaudio.load = _patched_load
            torchaudio.save = _patched_save
        except Exception:
            pass

        from demucs.separate import main as demucs_main
        sys.exit(demucs_main())

    root = Tk()
    ttk.Style(root).theme_use("clam")
    app = AudioSeparatorApp(root)
    del app
    root.mainloop()


if __name__ == "__main__":
    main()
