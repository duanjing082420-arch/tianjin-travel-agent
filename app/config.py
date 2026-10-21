# -*- coding: utf-8 -*-
"""
配置中心：所有开关和密钥都从这里读，别在业务代码里散落硬编码。

为什么要自己写 .env 加载？
    Windows 上 python-dotenv 不是默认装的，而且我们只有十几个配置项，
    用标准库写 20 行就够，少一个依赖少一堆麻烦。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# 项目根目录：config.py 在 app/ 下，所以 parent 就是根
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)


def load_dotenv(path: Path | None = None) -> dict[str, str]:
    """极简 .env 解析器：读 KEY=VALUE，忽略 # 注释和空行。

    已存在于真实环境变量里的键不会被覆盖（方便部署时用环境变量顶替文件）。
    """
    env_file = path or (ROOT / ".env")
    loaded: dict[str, str] = {}
    if not env_file.exists():
        return loaded

    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded[key] = value
    return loaded


# 模块导入即加载，业务代码直接 Settings.XXX 就行
load_dotenv()


@dataclass(frozen=True)
class Settings:
    # --- 硅基流动 ---
    api_key: str = os.getenv("SILICONFLOW_API_KEY", "")
    base_url: str = os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
    chat_model: str = os.getenv("CHAT_MODEL", "Qwen/Qwen2.5-7B-Instruct")
    embed_model: str = os.getenv("EMBED_MODEL", "BAAI/bge-m3")

    # --- 服务 ---
    host: str = os.getenv("APP_HOST", "127.0.0.1")
    port: int = int(os.getenv("APP_PORT", "8000"))
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    # --- 路径 ---
    root: Path = ROOT
    data_dir: Path = DATA_DIR
    db_path: Path = DATA_DIR / "app.db"
    vector_store_path: Path = DATA_DIR / "vector_store.json"
    embed_cache_path: Path = DATA_DIR / "embedding_cache.json"

    # --- 检索参数（RAG 用，先占位） ---
    top_k: int = 6               # 混合检索返回条数
    keyword_weight: float = 0.45  # 关键词得分占比，剩下 0.55 给向量

    def validate(self) -> None:
        if not self.api_key:
            raise RuntimeError(
                "没读到 SILICONFLOW_API_KEY，检查项目根目录的 .env 文件是否存在"
            )


settings = Settings()
