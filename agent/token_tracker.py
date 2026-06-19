class TokenTracker:
    def __init__(self, context_window: int = 128000):
        self.session_input_tokens = 0
        self.session_output_tokens = 0
        self.context_window = max(1, int(context_window))
        self.context_used_tokens = 0

    def estimate_tokens(self, text: str) -> int:
        """Heuristically estimates token count for mixed CJK/ASCII text."""
        if not text:
            return 0
        cjk_count = sum(1 for c in text if ord(c) > 255)
        ascii_len = len(text) - cjk_count
        ascii_tokens = max(1, ascii_len // 4) if ascii_len > 0 else 0
        return cjk_count + ascii_tokens

    def add_tokens(self, input_count: int, output_count: int):
        self.session_input_tokens += input_count
        self.session_output_tokens += output_count

    def add_text(self, input_text: str, output_text: str):
        """Estimates and adds tokens based on raw text inputs and outputs."""
        self.session_input_tokens += self.estimate_tokens(input_text)
        self.session_output_tokens += self.estimate_tokens(output_text)

    @property
    def total_tokens(self) -> int:
        return self.session_input_tokens + self.session_output_tokens

    @property
    def context_percent(self) -> float:
        return (self.context_used_tokens / self.context_window) * 100.0

    def set_context_used(self, token_count: int):
        self.context_used_tokens = max(0, int(token_count))

    def reset(self):
        self.session_input_tokens = 0
        self.session_output_tokens = 0
        self.context_used_tokens = 0
