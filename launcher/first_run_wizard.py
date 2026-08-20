# launcher/first_run_wizard.py
"""首次配置向导 (tkinter, 标准库无额外依赖)。

业务员首次启动时填写 API Key, 生成 .env。
阿里云 DashScope Key 同时用于 embedding + reranker (已验证);
LLM 可选 DeepSeek (默认) 或复用阿里云。
"""
import logging
import os
import threading
import tkinter as tk
from tkinter import ttk, messagebox

log = logging.getLogger(__name__)

# 阿里云 DashScope 默认配置 (用户可改)
DEFAULT_ALIYUN_HOST = "llm-2xly9c8surtss55f.cn-beijing.maas.aliyuncs.com"
DEFAULT_EMBEDDING_MODEL = "qwen3.7-text-embedding"
DEFAULT_RERANKER_MODEL = "qwen3-rerank"
DEFAULT_LLM_MODEL = "qwen-plus"

# LLM 服务商预设
LLM_PRESETS = {
    "阿里云 (复用上方 Key)": {
        "provider": "openai",
        "api_base": f"https://{DEFAULT_ALIYUN_HOST}/compatible-mode/v1",
        "model": DEFAULT_LLM_MODEL,
        "use_aliyun_key": True,
    },
    "DeepSeek": {
        "provider": "openai",
        "api_base": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "use_aliyun_key": False,
    },
    "OpenAI": {
        "provider": "openai",
        "api_base": "https://api.openai.com/v1",
        "model": "gpt-4o",
        "use_aliyun_key": False,
    },
}


def _test_aliyun(api_key: str) -> tuple[bool, str]:
    """测试阿里云 Key: 发一个最小 embedding 请求验证。"""
    try:
        import openai
        client = openai.OpenAI(
            api_key=api_key,
            base_url=f"https://{DEFAULT_ALIYUN_HOST}/compatible-mode/v1",
        )
        resp = client.embeddings.create(
            model=DEFAULT_EMBEDDING_MODEL, input="测试", dimensions=1024)
        if resp.data and resp.data[0].embedding:
            return True, "连接成功"
        return False, "返回数据为空"
    except Exception as e:
        return False, str(e)[:200]


def _test_llm(api_key: str, base: str, model: str) -> tuple[bool, str]:
    """测试 LLM: 发一个最小 chat 请求。"""
    try:
        import openai
        client = openai.OpenAI(api_key=api_key, base_url=base)
        resp = client.chat.completions.create(
            model=model, max_tokens=8,
            messages=[{"role": "user", "content": "hi"}])
        if resp.choices:
            return True, "连接成功"
        return False, "返回数据为空"
    except Exception as e:
        return False, str(e)[:200]


def _write_env(aliyun_key: str, llm_choice: str, llm_key: str) -> str:
    """生成 .env 内容并返回。"""
    preset = LLM_PRESETS[llm_choice]
    lines = [
        "# ===== 外贸客户知识库 .env (由配置向导生成) =====",
        "",
        "# ---------- LLM (生成) ----------",
        f"KB_LLM_PROVIDER={preset['provider']}",
        f"KB_LLM_MODEL={preset['model']}",
        f"KB_LLM_API_BASE={preset['api_base']}",
    ]
    if preset["use_aliyun_key"]:
        lines.append(f"KB_LLM_API_KEY={aliyun_key}")
    else:
        lines.append(f"KB_LLM_API_KEY={llm_key}")
    lines += [
        "",
        "# ---------- Embedding (向量化, 在线, 阿里云) ----------",
        "KB_EMBEDDING_PROVIDER=openai",
        f"KB_EMBEDDING_MODEL={DEFAULT_EMBEDDING_MODEL}",
        "KB_EMBEDDING_DIM=1024",
        f"KB_EMBEDDING_API_BASE=https://{DEFAULT_ALIYUN_HOST}/compatible-mode/v1",
        f"KB_EMBEDDING_API_KEY={aliyun_key}",
        "",
        "# ---------- Reranker (重排, 在线, 阿里云) ----------",
        "KB_RERANKER_PROVIDER=aliyun",
        f"KB_RERANKER_MODEL={DEFAULT_RERANKER_MODEL}",
        f"KB_RERANKER_API_BASE=https://{DEFAULT_ALIYUN_HOST}",
        f"KB_RERANKER_API_KEY={aliyun_key}",
        "",
    ]
    return "\n".join(lines)


class FirstRunWizard:
    def __init__(self, env_path: str):
        self.env_path = env_path
        self.result = None  # True=已保存, False=取消
        self.root = tk.Tk()
        self.root.title("外贸客户知识库 · 首次配置")
        self.root.geometry("560x460")
        self.root.resizable(False, False)
        self._build_ui()

    def _build_ui(self):
        pad = {"padx": 12, "pady": 6}
        frm = ttk.Frame(self.root, padding=16)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="外贸客户知识库 · 首次配置",
                  font=("Microsoft YaHei", 14, "bold")).pack(anchor="w", **pad)
        ttk.Label(frm, text="请填写 API Key 以启用 AI 功能。所有 Key 仅保存在本机 .env 文件。",
                  foreground="#666").pack(anchor="w", **pad)

        # 阿里云 DashScope Key
        ttk.Label(frm, text="阿里云 DashScope API Key (用于向量化 + 重排):",
                  font=("Microsoft YaHei", 10)).pack(anchor="w", **pad)
        self.aliyun_key = ttk.Entry(frm, width=60, show="*")
        self.aliyun_key.pack(anchor="w", **pad)
        ttk.Label(frm, text="在阿里云百炼控制台创建 API Key, 形如 sk-...",
                  foreground="#999", font=("Microsoft YaHei", 8)).pack(anchor="w", **pad)

        # LLM 服务商
        ttk.Label(frm, text="LLM 服务商:", font=("Microsoft YaHei", 10)).pack(anchor="w", **pad)
        self.llm_choice = ttk.Combobox(frm, state="readonly",
                                       values=list(LLM_PRESETS.keys()), width=40)
        self.llm_choice.set("DeepSeek")
        self.llm_choice.pack(anchor="w", **pad)

        # LLM Key (DeepSeek/OpenAI 时)
        ttk.Label(frm, text="LLM API Key (DeepSeek/OpenAI 时填写):",
                  font=("Microsoft YaHei", 10)).pack(anchor="w", **pad)
        self.llm_key = ttk.Entry(frm, width=60, show="*")
        self.llm_key.pack(anchor="w", **pad)

        # 按钮
        btn_frm = ttk.Frame(frm)
        btn_frm.pack(anchor="w", **pad)
        self.test_btn = ttk.Button(btn_frm, text="测试连接", command=self._on_test)
        self.test_btn.pack(side="left", padx=(0, 8))
        self.save_btn = ttk.Button(btn_frm, text="保存并继续", command=self._on_save)
        self.save_btn.pack(side="left")
        self.status = ttk.Label(frm, text="", foreground="#c00")
        self.status.pack(anchor="w", **pad)

    def _on_test(self):
        aliyun_key = self.aliyun_key.get().strip()
        if not aliyun_key:
            self.status.config(text="请先填写阿里云 API Key")
            return
        self.status.config(text="测试中...", foreground="#c00")
        self.test_btn.config(state="disabled")

        def work():
            ok, msg = _test_aliyun(aliyun_key)
            self.root.after(0, lambda: self._test_done(ok, msg))

        threading.Thread(target=work, daemon=True).start()

    def _test_done(self, ok, msg):
        self.test_btn.config(state="normal")
        if ok:
            self.status.config(text=f"阿里云连接成功 ✓", foreground="#080")
        else:
            self.status.config(text=f"连接失败: {msg}", foreground="#c00")

    def _on_save(self):
        aliyun_key = self.aliyun_key.get().strip()
        if not aliyun_key:
            messagebox.showerror("错误", "请填写阿里云 DashScope API Key")
            return
        llm_choice = self.llm_choice.get()
        preset = LLM_PRESETS[llm_choice]
        if not preset["use_aliyun_key"] and not self.llm_key.get().strip():
            messagebox.showerror("错误", f"请填写 {llm_choice} 的 API Key")
            return
        content = _write_env(aliyun_key, llm_choice, self.llm_key.get().strip())
        try:
            with open(self.env_path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            messagebox.showerror("错误", f"写入 .env 失败: {e}")
            return
        self.result = True
        self.root.destroy()

    def run(self) -> bool:
        self.root.mainloop()
        return bool(self.result)


def run_wizard(env_path: str) -> bool:
    """运行配置向导, 返回是否已保存 .env。"""
    w = FirstRunWizard(env_path)
    return w.run()