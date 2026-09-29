class ProviderError(Exception):
    def __init__(self, message: str, provider: str, error_type: str = "provider_error"):
        self.message = message
        self.provider = provider
        self.error_type = error_type
        super().__init__(f"[{provider}] {error_type}: {message}")

class ProviderConfigError(ProviderError):
    def __init__(self, message: str, provider: str):
        super().__init__(message, provider, "configuration_error")

class ProviderAuthError(ProviderError):
    def __init__(self, message: str, provider: str):
        super().__init__(message, provider, "authentication_error")

class ProviderRateLimitError(ProviderError):
    def __init__(self, message: str, provider: str):
        super().__init__(message, provider, "rate_limit_error")

class ProviderTimeoutError(ProviderError):
    def __init__(self, message: str, provider: str):
        super().__init__(message, provider, "timeout_error")

class ProviderEmptyResult(ProviderError):
    def __init__(self, message: str, provider: str):
        super().__init__(message, provider, "empty_result")
