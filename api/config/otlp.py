"""OpenTelemetry 配置组（v22 P2-3 从 __init__.py 拆出）。

仅依赖 os.getenv（模块加载时读环境变量），不依赖 settings 单例。
api.config 包 re-export 保持向后兼容：
- `from api.config import OTEL_ENABLED` 等读点不变
- `from api import config as app_config; app_config.OTEL_*` 包属性读点不变
- api/config/settings.py 的 `from . import OTEL_*` 批量兼容导入不变
"""

from __future__ import annotations

import os

# ── OpenTelemetry（IF_OTEL_*）───────────────────
OTEL_ENABLED = os.getenv("IF_OTEL_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
OTEL_SERVICE_NAME = os.getenv("IF_OTEL_SERVICE_NAME", "imagefree-api")
OTEL_EXPORTER_OTLP_ENDPOINT = os.getenv("IF_OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
OTEL_CONSOLE_EXPORTER = os.getenv("IF_OTEL_CONSOLE_EXPORTER", "0").strip().lower() in {"1", "true", "yes", "on"}
# P3-2: tail-based 采样策略 —— 错误请求（5xx/异常）100% 采样，正常请求按比例采样。
# 默认 sample_rate=0.1（10%），error_sample_rate=1.0（100%）。生产建议调低 sample_rate 到 0.05。
OTEL_SAMPLE_RATE = float(os.getenv("IF_OTEL_SAMPLE_RATE", "0.1"))
OTEL_ERROR_SAMPLE_RATE = float(os.getenv("IF_OTEL_ERROR_SAMPLE_RATE", "1.0"))
