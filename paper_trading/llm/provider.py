"""OpenAI 兼容接口调用（标准库 urllib，无第三方依赖）。

错误信息里永不携带 Key。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional


class LLMError(Exception):
    """LLM 调用失败（网络/认证/协议）。message 中不含 Key。"""


@dataclass
class LLMConfig:
    base_url: str
    api_key: str = ""
    model: str = ""
    timeout: float = 30.0
    max_tokens: int = 4096  # 推理模型 thinking 占用输出预算，默认留足
    temperature: float = 0.2
    extra_body: dict = None  # 厂商透传参数（如 {"thinking": {"type": "disabled"}}）

    def __post_init__(self) -> None:
        if self.extra_body is None:
            self.extra_body = {}


def _join(base: str, path: str) -> str:
    return base.rstrip("/") + path


def _request(cfg: LLMConfig, path: str, payload: Optional[dict] = None) -> dict:
    url = _join(cfg.base_url, path)
    if not url.startswith(("http://", "https://")):
        raise LLMError(f"base_url 非法（须 http(s) 开头）：{cfg.base_url!r}")
    data = json.dumps(payload or {}).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"}
    # 本地/局域网接口（Ollama、LM Studio、vLLM、自建网关）通常不校验令牌。
    # 空 Key 时**不发** Authorization 头：有些服务收到空的 Bearer 反而直接 401。
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    req = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "ignore")[:300]
        except Exception:
            body = ""
        raise LLMError(f"HTTP {e.code} {path}：{body}") from None
    except Exception as e:
        raise LLMError(f"请求失败 {path}：{type(e).__name__}: {e}") from None


def list_models(cfg: LLMConfig) -> list[str]:
    """拉取模型列表（GET {base}/models）。返回模型 id 列表。"""
    body = _request(cfg, "/models")
    items = body.get("data", [])
    if not isinstance(items, list):
        raise LLMError("模型列表格式异常（缺 data 数组）")
    out = [str(x.get("id", "")) for x in items if isinstance(x, dict) and x.get("id")]
    if not out:
        raise LLMError("模型列表为空（地址能连通但没返回模型：确认服务已加载模型；"
                       "云端接口还需要有效的 Key）")
    return out


def chat(cfg: LLMConfig, messages: list[dict], system: str = "") -> str:
    """一轮对话，返回助手文本。"""
    if not cfg.model:
        raise LLMError("未指定模型（先选模型或手填模型名）")
    msgs = ([{"role": "system", "content": system}] if system else []) + list(messages)
    payload = {
        "model": cfg.model,
        "messages": msgs,
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_tokens,
    }
    if cfg.extra_body:
        payload.update(cfg.extra_body)
    body = _request(cfg, "/chat/completions", payload)
    try:
        choice = body["choices"][0]
        msg = choice["message"]
        content = msg.get("content")
    except (KeyError, IndexError, TypeError):
        raise LLMError("对话返回格式异常（缺 choices/message/content）") from None
    if not content:
        # 推理模型把输出预算全花在 thinking 上时，content 是空串、finish_reason=length。
        # 直接返回空会让面板看起来像"API 坏了"，这里给出能照着做的原因。
        finish = str(choice.get("finish_reason") or "")
        thinking = str(msg.get("reasoning_content") or "")
        if thinking:
            raise LLMError(
                f"模型只输了思考过程、答案为空（finish_reason={finish or '未知'}，"
                f"思考 {len(thinking)} 字）。把最大输出调大，或在模型设置里关闭思考"
                f"（本地模型可在请求里传 thinking=disabled）")
        raise LLMError(f"模型返回空答案（finish_reason={finish or '未知'}）")
    return str(content)


#: 始终思考的厂商错误指纹（GLM 5.3 系列 HTTP 400 / 1210「不支持关闭思考」）
THINKING_UNSUPPORTED_HINT = "1210"


def chat_with_retry(cfg: LLMConfig, messages: list[dict], system: str = "", *,
                    timeout: Optional[float] = None, retries: int = 1,
                    max_tokens: Optional[int] = None,
                    thinking: str = "disabled") -> str:
    """带超时重试与「思考透传降级」的一轮对话——**所有** LLM 调用路径共用的原语。

    为什么这段从 `agent/loop._ask_llm` 提到 provider（T64）：决策路径与问答路径必须共用
    同一份"配置怎么落到请求上"的逻辑。两条路径各抄一遍正是 `llm_timeout` 那类断层的温床——
    问答路径当初没抄 timeout/max_tokens/thinking，于是要么 30 秒超时，要么输出预算被
    思考吃光、返回空答案（`chat()` 里那条报错就是同一件事的用户可见面）。

    只重试超时：其他错误（HTTP 4xx、格式异常）重试只会掩盖问题。
    `thinking="disabled"` 时透传厂商开关；遇到"这模型始终思考"就降到 low 档重试一次，
    并把输出预算抬到至少 4096——不能因为一个透传参数把整条链路打死。
    """
    if timeout is not None:
        cfg.timeout = float(timeout)
    if max_tokens is not None:
        cfg.max_tokens = int(max_tokens)

    def _attempt(extra: dict) -> str:
        cfg.extra_body = extra
        last: Optional[Exception] = None
        for attempt in range(max(0, int(retries)) + 1):
            try:
                return chat(cfg, messages, system=system)
            except Exception as e:  # noqa: BLE001 - 仅超时重试，其余直接抛
                last = e
                low = str(e).lower()
                if "timed out" not in low and "timeout" not in low:
                    raise
                if attempt >= max(0, int(retries)):
                    raise
        raise last or LLMError("LLM unreachable")

    extra = {"thinking": {"type": "disabled"}} if thinking == "disabled" else {}
    try:
        return _attempt(extra)
    except LLMError as e:
        if thinking == "disabled" and THINKING_UNSUPPORTED_HINT in str(e):
            cfg.max_tokens = max(int(max_tokens or 0), 4096)
            return _attempt({"thinking": {"type": "enabled", "level": "low"}})
        raise
