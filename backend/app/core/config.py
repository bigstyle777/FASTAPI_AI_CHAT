from functools import lru_cache
import os
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 找项目路径
APP_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(
            PROJECT_ROOT / ".env",
            BACKEND_DIR / ".env",
        ),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    deepseek_api_key: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str | None = None
    ai_model: str | None = None
    jwt_secret_key: str = "myselectkey"
    jwt_algorithm: str = "HS256"
    redis_url: str = "redis://127.0.0.1:6379/0"
    cors_allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    access_token_ttl_seconds: int = 60 * 60 * 24 * 30
    captcha_ttl_seconds: int = 300
    user_cache_ttl_seconds: int = 60 * 60 * 24
    user_settings_cache_ttl_seconds: int = 3600
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/aichat"
    chat_ttl_seconds: int = 60 * 60
    # 喂给模型的历史条数上限。历史越长，模型要"读"的内容越多，首字延迟越久。
    chat_context_max_messages: int = 20
    # 喂给模型的历史字符预算。只限条数不够：20 条可能是 20 句闲聊，
    # 也可能是 20 段长文，后者照样把 prompt 撑爆，所以再压一道字符上限。
    chat_context_max_chars: int = 12000
    # 单用户每小时的聊天请求上限（频率限流，和"同时运行数"是两回事）
    chat_rate_limit_per_hour: int = 500
    # 进程内同时运行的最大生成数。打满后新请求直接拒绝而不是排队，
    # 排队只会把上游超时和内存压力往后推。
    chat_max_concurrent_generations: int = 20
    # ---- Agent 工程化（阶段 3）----
    # 整轮软超时：在步骤边界/流式 token 边界检查，超时后停止后续步骤但仍总结
    agent_run_timeout_seconds: int = 120
    agent_max_concurrent_runs: int = 4
    # 单个工具执行超时；超时当作工具失败，交给模型修正
    agent_tool_timeout_seconds: int = 15
    # 单个工具返回结果的最大字符数，超出截断并标注
    agent_tool_output_max_chars: int = 4000
    # 规划最多尝试次数（含首次）；解析失败才重试
    agent_planner_max_attempts: int = 2
    stop_generation_ttl_seconds: int = 60 * 5
    bootstrap_admin_username: str | None = "admin"
    bootstrap_admin_password: str | None = "admin123456"
    rag_enabled: bool = True
    rag_upload_dir: str = "backend/uploads/rag"
    rag_embedding_api_key: str | None = None
    rag_embedding_base_url: str = "https://api.siliconflow.cn/v1"
    rag_embedding_model: str = "BAAI/bge-large-zh-v1.5"
    rag_embedding_dimension: int = 1024
    rag_top_k: int = 5
    rag_chunk_size: int = 900
    rag_chunk_overlap: int = 150
    rag_max_context_chars: int = 5000
    tavily_api_key: str | None = None
    # ---- Memory System（Phase 1-6）----
    memory_enabled: bool = True
    # 检索返回的条数上限
    memory_top_k: int = 5
    # 每路候选各取 top_k 的几倍，合并排序后再截断到 top_k
    memory_candidate_multiplier: int = 3
    # 时效性归一化用的半衰期（天）：半衰期前的记忆 recency 记为 0.5。
    # 排序权重本身（五因子、按类型分套）已迁到 app/memory/config.py，
    # 这里只留这一个需要部署期按环境调的标量。
    memory_recency_half_life_days: float = 30.0
    memory_embedding_enabled: bool = True
    # 提取时喂给 LLM 的对话文本上限，避免长会话把 prompt 撑爆
    memory_extraction_max_chars: int = 4000
    # MCP server 列表（JSON 数组字符串，格式见 app/mcp/config.py；
    # None 时使用默认的 calculator server）
    mcp_servers: str | None = None
    log_level: str = "INFO"

    # ---- LLM 可靠性：超时与重试（阶段1A）----
    # httpx 连接超时：TCP 建连超过即失败，让网络问题快速暴露
    llm_connect_timeout_seconds: float = 5
    # 单次 create() 调用的超时；流式场景下同时约束"两块数据之间的最长停顿"
    llm_request_timeout_seconds: float = 60
    # 一条流式生成从开始到主动放弃的总预算，超时按"用户停止"同等路径收场
    llm_stream_max_seconds: float = 300
    # 重试：指数退避 + 全抖动，只作用于建连阶段（见 core/retry.py）
    llm_retry_max_attempts: int = 3
    llm_retry_base_seconds: float = 0.5
    llm_retry_cap_seconds: float = 4.0

    # ---- 阶段2：模型调用治理 ----
    # 熔断：只统计可重试类错误（网络/超时/429/5xx）。
    # 连续失败满阈值即打开，冷却期内直接拒绝，不再发起请求。
    llm_circuit_failure_threshold: int = 5
    llm_circuit_cooldown_seconds: int = 60
    # HALF_OPEN 探测锁的有效期，防止探测请求本身挂死后永久锁住
    llm_circuit_probe_ttl_seconds: int = 5
    # 是否启用备用模型降级；用户自带 Key 时只会换模型、不换供应商
    llm_fallback_enabled: bool = True
    # 备用 provider；空表示"自动选另一家"（deepseek ↔ openai）
    llm_fallback_provider: str | None = None
    # 同供应商内可替换的备用模型；空表示不换模型
    llm_fallback_model: str | None = None
    # 单条回答的输出上限（传给 max_tokens），0 表示不显式限制
    llm_max_output_tokens: int = 0
    # 每个用户每天可用的 token 总额度，0 表示不限制
    chat_user_daily_token_budget: int = 0
    # 全局每天的成本上限（元），0 表示不限制
    global_daily_cost_limit_yuan: float = 0

    @model_validator(mode="after")
    def fill_derived_defaults(self):
        # Docker secrets 惯例：<FIELD>_FILE 指向只读文件时，优先读取文件内容作为密钥。
        # 容器内由 compose 挂载 ./secrets -> /run/secrets 并注入 *_KEY_FILE 变量，
        # 避免密钥出现在环境变量（docker inspect 可见）；文件缺失时回退 env_file 注入。
        for field in ("deepseek_api_key", "openai_api_key", "tavily_api_key", "rag_embedding_api_key"):
            if getattr(self, field) is None:
                file_env = os.environ.get(f"{field.upper()}_FILE")
                if file_env:
                    try:
                        content = Path(file_env).read_text(encoding="utf-8").strip()
                        if content:
                            setattr(self, field, content)
                    except OSError:
                        pass
        if not self.openai_model:
            self.openai_model = self.ai_model or "gpt-4o-mini"
        if not self.deepseek_model:
            self.deepseek_model = self.ai_model or "deepseek-v4-flash"
        return self

    @property
    def cors_origins_list(self) -> list[str]:
        """将逗号分隔的 CORS 允许来源字符串解析为列表。"""
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    @property
    def is_default_jwt_secret(self) -> bool:
        """检测 JWT 密钥是否为不安全的默认值。"""
        return self.jwt_secret_key in ("myselectkey", "change-me", "")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
