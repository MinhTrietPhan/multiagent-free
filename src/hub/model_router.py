"""LLM Router with automatic model failover + usage tracking."""
import logging
import time
from dataclasses import dataclass, field
from functools import lru_cache
from .llm import get_llm_models
logger = logging.getLogger(__name__)

COOLDOWN_SECONDS = 3600


def is_retryable_error(error: Exception) -> bool:
    message = str(error).lower()
    patterns = ['429', 'resource_exhausted', 'rate_limit', 'quota exceeded', '500', '502', '503', '504', 'service unavailable', 'timeout']
    return any((pattern in message for pattern in patterns))


def _reason(error: Exception) -> str:
    message = str(error).lower()
    if any(k in message for k in ('429', 'resource_exhausted', 'rate_limit', 'quota')):
        return 'hết quota/rate limit'
    return 'lỗi tạm thời'


@dataclass
class UsageTracker:
    """Đếm request theo model + ghi lại model nào đã trả lời trong lượt chat hiện tại.

    Lưu ý: Gemini API không trả về quota còn lại, nên đây chỉ là số request *đã gọi thành công*
    trong phiên chạy này, dùng để ước lượng.
    """
    calls: dict[str, int] = field(default_factory=dict)
    failures: dict[str, int] = field(default_factory=dict)
    cooldowns: dict[str, float] = field(default_factory=dict)
    turn_models: list[str] = field(default_factory=list)
    route_models: list[str] = field(default_factory=list)
    turn_events: list[str] = field(default_factory=list)

    def begin_turn(self):
        self.turn_models, self.route_models, self.turn_events = [], [], []

    def mark_routing(self):
        """Supervisor gọi sau khi route xong: các model đã dùng tới giờ là để điều phối, không phải để trả lời."""
        self.route_models, self.turn_models = self.turn_models, []

    def cooldown_left(self, model: str) -> int:
        return max(0, int(self.cooldowns.get(model, 0) - time.time()))

    def is_cooling(self, model: str) -> bool:
        return self.cooldown_left(model) > 0

    def success(self, model: str):
        self.calls[model] = self.calls.get(model, 0) + 1
        if model not in self.turn_models:
            self.turn_models.append(model)

    def failure(self, model: str, error: Exception):
        self.failures[model] = self.failures.get(model, 0) + 1
        self.cooldowns[model] = time.time() + COOLDOWN_SECONDS
        self.turn_events.append(f'{model}: {_reason(error)} → chuyển model kế tiếp')

    def next_model(self, pool: list[str]) -> str | None:
        return next((m for m in pool if not self.is_cooling(m)), None)


TRACKER = UsageTracker()


class ModelRouter:

    def __init__(self, models, tracker: UsageTracker | None=None):
        self.models = models
        self.tracker = tracker or TRACKER

    def _invoke(self, input_data, structured_schema=None, **kwargs):
        errors = []
        for model in self.models:
            model_name = model.model
            if self.tracker.is_cooling(model_name):
                continue
            try:
                logger.info('Trying model: %s', model_name)
                runnable = model.with_structured_output(structured_schema) if structured_schema else model
                result = runnable.invoke(input_data, **kwargs)
                logger.info('Model succeeded: %s', model_name)
                self.tracker.success(model_name)
                return result
            except Exception as error:
                if not is_retryable_error(error):
                    raise
                logger.warning('Model %s failed: %s', model_name, error)
                self.tracker.failure(model_name, error)
                errors.append(error)
        if errors:
            raise RuntimeError('Tất cả model trong MODEL_POOL đều không khả dụng.') from errors[-1]
        raise RuntimeError('Không còn model khả dụng trong MODEL_POOL.')

    def invoke(self, input_data, **kwargs):
        return self._invoke(input_data, **kwargs)

    def with_structured_output(self, schema, **kwargs):
        return StructuredOutputRouter(self, schema, kwargs)


class StructuredOutputRouter:

    def __init__(self, router, schema, config):
        self.router = router
        self.schema = schema
        self.config = config

    def invoke(self, input_data, **kwargs):
        return self.router._invoke(input_data, structured_schema=self.schema, **self.config, **kwargs)


@lru_cache(maxsize=1)
def get_model_router():
    """Return one shared ModelRouter."""
    return ModelRouter(get_llm_models())


def get_llm():
    """Return the shared ModelRouter."""
    return get_model_router()
