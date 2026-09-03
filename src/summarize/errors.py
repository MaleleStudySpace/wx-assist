"""LLM 调用相关的异常。

项目此前没有 LLM 异常体系，调用方只能 `except Exception`，导致 provider 的
结构化错误被压成不可诊断的信息。典型例子：中转 API 在上下文超限时返回
HTTP **200** + `{"choices": null, "base_resp": {"status_code": 2013,
"status_msg": "invalid params, context window exceeds limit"}}`，OpenAI SDK
因为状态码是 200 不抛 HTTP 错误，`response.choices[0]` 直接炸成
`TypeError: 'NoneType' object is not subscriptable`，最终推到任务中心的
错误串对用户毫无意义。

基类继承 RuntimeError 而非 Exception，以兼容既有的 `except RuntimeError`
兜底（如 `OpenAICompatSummarizer.consolidate_memory`、`AbstractSummarizer.chat`）。
"""


class LLMError(RuntimeError):
    """LLM 调用失败的通用基类。"""


class LLMResponseError(LLMError):
    """provider 返回了响应，但响应体不可用（choices 为 null / message 为空）。

    Attributes:
        prompt_tokens: provider 在 `usage` 里报告的输入 token 数。上下文超限时
            这个值仍然会返回（实测 299,247），是判断"超了多少"的唯一依据。
        status_code: 中转 API 的业务错误码（如 new-api 的 `base_resp.status_code`）。
        status_msg: 业务错误描述。
        raw: 原始响应对象，供上层需要时进一步检查。
    """

    def __init__(self, message: str, *, prompt_tokens: int | None = None,
                 status_code: int | None = None, status_msg: str = "", raw=None):
        super().__init__(message)
        self.prompt_tokens = prompt_tokens
        self.status_code = status_code
        self.status_msg = status_msg
        self.raw = raw


class LLMContextOverflowError(LLMResponseError):
    """输入超出模型上下文窗口。

    调用方可以据此**降级**（例如打包多会话摘要 → 逐会话摘要后拼接），
    而不是当成一次普通失败重试或放弃 —— 重试同样的输入只会同样失败。
    """
