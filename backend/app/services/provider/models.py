"""
模型能力服务

提供动态模型能力查询，包括上下文窗口、输入输出限制等。
设计参考：OpenCode 的 ModelsDev 实现

数据来源优先级：
1. 本地缓存文件 ~/.cache/aethercore/models.json（命中即用，过期由后台线程刷新）
2. models.dev API（https://models.dev/api.json，仅首次无缓存时同步获取）
3. 内置最小配置（仅包含常用模型的基本信息，fallback）

特性：
- 请求路径只读缓存，网络刷新一律在后台线程，避免阻塞 asyncio 事件循环
- 支持通过 Settings.models_fetch_enabled（MODELS_FETCH_ENABLED）或
  AETHERCORE_DISABLE_MODELS_FETCH 关闭外网拉取（内网部署）
- 后台定时刷新并热更新内存注册表
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class ModelLimit:
    """模型限制配置"""
    context: int = 200_000
    input: int | None = None
    output: int = 8_192


@dataclass
class ModelCost:
    """模型成本配置"""
    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0


@dataclass
class ModelCapabilities:
    """模型能力配置"""
    temperature: bool = True
    reasoning: bool = False
    attachment: bool = True
    tool_call: bool = True
    streaming: bool = True


@dataclass
class ModelInfo:
    """完整模型信息"""
    id: str
    name: str
    provider: str
    limit: ModelLimit = field(default_factory=ModelLimit)
    cost: ModelCost = field(default_factory=ModelCost)
    capabilities: ModelCapabilities = field(default_factory=ModelCapabilities)
    status: str = "active"
    release_date: str | None = None
    variants: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class ProviderInfo:
    """提供商信息"""
    id: str
    name: str
    models: dict[str, ModelInfo] = field(default_factory=dict)


MODELS_DEV_URL = "https://models.dev/api.json"
DEFAULT_CACHE_DIR = Path.home() / ".cache" / "aethercore"
DEFAULT_CACHE_FILE = DEFAULT_CACHE_DIR / "models.json"
REFRESH_INTERVAL_SECONDS = 3600  # 1小时
REQUEST_TIMEOUT_SECONDS = 10


def _get_minimal_builtin_config() -> dict[str, ProviderInfo]:
    """
    获取最小内置配置（仅用于完全无法获取外部数据时的fallback）。
    
    包含最常用的几个模型的基本信息，确保核心功能可用。
    """
    return {
        "provider-a": ProviderInfo(
            id="provider-a",
            name="Provider A",
            models={
                "model-a-large": ModelInfo(
                    id="model-a-large",
                    name="Model A Large",
                    provider="provider-a",
                    limit=ModelLimit(context=200_000, output=32_000),
                ),
                "model-a-medium": ModelInfo(
                    id="model-a-medium",
                    name="Model A Medium",
                    provider="provider-a",
                    limit=ModelLimit(context=200_000, output=32_000),
                ),
                "model-a-small": ModelInfo(
                    id="model-a-small",
                    name="Model A Small",
                    provider="provider-a",
                    limit=ModelLimit(context=200_000, output=8_192),
                ),
            },
        ),
        "provider-b": ProviderInfo(
            id="provider-b",
            name="Provider B",
            models={
                "model-b-basic": ModelInfo(
                    id="model-b-basic",
                    name="Model B Basic",
                    provider="provider-b",
                    limit=ModelLimit(context=8_192, output=4_096),
                ),
                "model-b-advanced": ModelInfo(
                    id="model-b-advanced",
                    name="Model B Advanced",
                    provider="provider-b",
                    limit=ModelLimit(context=128_000, output=16_384),
                ),
            },
        ),
    }


class ModelsDevClient:
    """
    models.dev API客户端

    负责从 models.dev 获取模型配置并管理本地缓存。
    """

    def __init__(
        self,
        cache_path: Path | None = None,
        models_dev_url: str = MODELS_DEV_URL,
        refresh_interval: int = REFRESH_INTERVAL_SECONDS,
        request_timeout: int = REQUEST_TIMEOUT_SECONDS,
    ):
        self.cache_path = cache_path or DEFAULT_CACHE_FILE
        self.models_dev_url = models_dev_url
        self.refresh_interval = refresh_interval
        self.request_timeout = request_timeout
        self._last_refresh_time: float = 0
        self._refresh_thread: threading.Thread | None = None
        self._stop_refresh = False
        # 关闭开关：Settings（models_fetch_enabled / MODELS_FETCH_ENABLED）或旧版环境变量
        self._disable_network_fetch = (
            not settings.models_fetch_enabled
            or os.environ.get("AETHERCORE_DISABLE_MODELS_FETCH", "").lower() in ("1", "true", "yes")
        )

    @property
    def network_fetch_disabled(self) -> bool:
        """是否已禁用外网拉取（禁用时无需启动后台刷新线程）"""
        return self._disable_network_fetch
    
    def get_cache_path(self) -> Path:
        """确保缓存目录存在"""
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        return self.cache_path
    
    def fetch_from_models_dev(self) -> dict[str, Any] | None:
        """
        从 models.dev API 获取模型配置。
        
        返回原始JSON数据，失败时返回None。
        """
        if self._disable_network_fetch:
            logger.debug("网络获取已禁用（models_fetch_enabled=false 或 AETHERCORE_DISABLE_MODELS_FETCH）")
            return None
        
        try:
            logger.info(f"从 models.dev 获取模型配置: {self.models_dev_url}")
            request = urllib.request.Request(
                self.models_dev_url,
                headers={"User-Agent": "AetherCore/1.0", "Accept": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=self.request_timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
                logger.info(f"成功获取 models.dev 数据，包含 {len(data)} 个提供商")
                return data
        except urllib.error.URLError as e:
            logger.warning(f"无法连接 models.dev: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.warning(f"models.dev 返回无效JSON: {e}")
            return None
        except Exception as e:
            logger.warning(f"获取 models.dev 数据时发生错误: {e}")
            return None
    
    def load_from_cache(self) -> dict[str, Any] | None:
        """
        从本地缓存加载模型配置。
        
        返回缓存数据，不存在或无效时返回None。
        """
        try:
            if self.cache_path.exists():
                data = json.loads(self.cache_path.read_text(encoding="utf-8"))
                logger.debug(f"从缓存加载模型配置: {self.cache_path}")
                return data
        except json.JSONDecodeError:
            logger.warning(f"缓存文件无效: {self.cache_path}")
        except Exception as e:
            logger.warning(f"读取缓存失败: {e}")
        return None
    
    def save_to_cache(self, data: dict[str, Any]) -> bool:
        """
        保存模型配置到本地缓存。
        
        返回是否成功保存。
        """
        try:
            cache_path = self.get_cache_path()
            cache_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(f"模型配置已保存到缓存: {cache_path}")
            return True
        except Exception as e:
            logger.warning(f"保存缓存失败: {e}")
            return False
    
    def refresh(self) -> dict[str, Any] | None:
        """
        刷新模型配置（从网络获取并更新缓存）。
        
        返回最新的模型配置数据。
        """
        data = self.fetch_from_models_dev()
        if data:
            self.save_to_cache(data)
            self._last_refresh_time = time.time()
            return data
        
        # 网络获取失败，尝试使用缓存
        cached = self.load_from_cache()
        if cached:
            logger.info("使用缓存的模型配置")
            return cached
        
        return None
    
    def start_background_refresh(self) -> None:
        """
        启动后台定时刷新线程。
        
        每隔 refresh_interval 秒自动刷新模型配置。
        """
        if self._refresh_thread and self._refresh_thread.is_alive():
            return
        
        self._stop_refresh = False
        
        def refresh_loop():
            while not self._stop_refresh:
                time.sleep(self.refresh_interval)
                if not self._stop_refresh:
                    try:
                        self.refresh()
                    except Exception as e:
                        logger.warning(f"后台刷新失败: {e}")
        
        self._refresh_thread = threading.Thread(target=refresh_loop, daemon=True)
        self._refresh_thread.start()
        logger.info(f"后台刷新线程已启动，间隔 {self.refresh_interval} 秒")
    
    def stop_background_refresh(self) -> None:
        """停止后台刷新线程"""
        self._stop_refresh = True
        if self._refresh_thread:
            self._refresh_thread.join(timeout=5)
            self._refresh_thread = None
    
    def should_refresh(self) -> bool:
        """
        检查是否需要刷新。

        判断依据（避免进程重启后对新鲜缓存仍发起网络请求）：
        1. 进程内成功刷新过且未超过间隔 → 不刷新
        2. 缓存文件存在且落盘时间未超过间隔 → 不刷新
        """
        now = time.time()
        if self._last_refresh_time and now - self._last_refresh_time < self.refresh_interval:
            return False
        try:
            if self.cache_path.exists():
                return now - self.cache_path.stat().st_mtime >= self.refresh_interval
        except OSError:
            pass
        return True


class ModelsRegistry:
    """
    模型能力注册表

    提供模型能力查询服务，数据来源：
    1. 本地缓存（优先）
    2. models.dev API（后台线程刷新，不阻塞请求）
    3. 最小内置配置（fallback）
    """

    DEFAULT_MODEL_LIMIT = ModelLimit(context=200_000, output=8_192)

    def __init__(
        self,
        cache_path: Path | None = None,
        models_dev_url: str = MODELS_DEV_URL,
        enable_background_refresh: bool = True,
    ):
        self._client = ModelsDevClient(
            cache_path=cache_path,
            models_dev_url=models_dev_url,
        )
        self._providers: dict[str, ProviderInfo] = {}
        self._loaded = False
        self._enable_background_refresh = enable_background_refresh
        self._refresh_thread: threading.Thread | None = None
        self._stop_refresh = False

    def _ensure_loaded(self) -> None:
        """确保模型配置已加载"""
        if self._loaded:
            return
        self._load_providers()
        self._loaded = True
        if self._enable_background_refresh and not self._client.network_fetch_disabled:
            self._start_background_refresh()

    def _load_providers(self) -> None:
        """
        加载模型配置，按优先级尝试不同数据来源。

        关键约束：本方法运行在 asyncio 事件循环内（聊天请求懒加载触发），
        只要磁盘上有可用缓存就直接使用（哪怕已过期），刷新一律交给后台线程，
        避免同步 urlopen 冻结整个服务（内网黑洞环境单次可达 40s+）。

        优先级：
        1. 本地缓存（立即生效，过期数据由后台线程更新）
        2. models.dev API（仅首次无缓存时同步获取）
        3. 最小内置配置（fallback）
        """
        data = self._client.load_from_cache()

        if data is not None:
            self._providers = self._parse_models_dev_data(data)
            logger.info(f"模型配置已加载，来源: cache，提供商数: {len(self._providers)}")
            return

        refreshed = self._client.refresh()
        if refreshed:
            self._providers = self._parse_models_dev_data(refreshed)
            logger.info(f"模型配置已加载，来源: models.dev，提供商数: {len(self._providers)}")
        else:
            self._providers = _get_minimal_builtin_config()
            logger.warning("无法获取模型配置，使用最小内置配置")

    def _start_background_refresh(self) -> None:
        """启动后台刷新线程：启动后立即补一次刷新，之后按固定间隔轮转。"""
        if self._refresh_thread and self._refresh_thread.is_alive():
            return
        self._stop_refresh = False

        def refresh_loop():
            # 进程刚启动（缓存可能过期）：先在后台补一次刷新
            self._background_refresh_once()
            while not self._stop_refresh:
                time.sleep(self._client.refresh_interval)
                if not self._stop_refresh:
                    self._background_refresh_once()

        self._refresh_thread = threading.Thread(target=refresh_loop, daemon=True, name="models-registry-refresh")
        self._refresh_thread.start()
        logger.info(f"模型配置后台刷新线程已启动，间隔 {self._client.refresh_interval} 秒")

    def _background_refresh_once(self) -> None:
        """单次后台刷新：拉取最新数据并热更新内存中的注册表。"""
        try:
            data = self._client.refresh()
            if data:
                self._providers = self._parse_models_dev_data(data)
                logger.info(f"模型配置已后台刷新，提供商数: {len(self._providers)}")
        except Exception as e:
            logger.warning(f"后台刷新模型配置失败: {e}")

    def stop_background_refresh(self) -> None:
        """停止后台刷新线程"""
        self._stop_refresh = True
        if self._refresh_thread:
            self._refresh_thread.join(timeout=5)
            self._refresh_thread = None
    
    def _parse_models_dev_data(self, data: dict[str, Any]) -> dict[str, ProviderInfo]:
        """
        解析 models.dev API 返回的数据格式。
        
        models.dev 格式：
        {
          "anthropic": {
            "id": "anthropic",
            "name": "Anthropic",
            "models": {
              "deepseek-v4": {
                "id": "deepseek-v4",
                "name": "DeepSeek V4",
                "limit": {"context": 200000, "input": null, "output": 32000},
                "cost": {"input": 15, "output": 75, "cache_read": 1.5, "cache_write": 18.75},
                ...
              }
            }
          }
        }
        """
        providers: dict[str, ProviderInfo] = {}
        
        for provider_id, provider_data in data.items():
            if not isinstance(provider_data, dict):
                continue
            
            provider = ProviderInfo(
                id=provider_id,
                name=provider_data.get("name", provider_id),
            )
            
            models_data = provider_data.get("models", {})
            if isinstance(models_data, dict):
                for model_id, model_data in models_data.items():
                    if isinstance(model_data, dict):
                        provider.models[model_id] = self._parse_model(model_id, model_data, provider_id)
            
            providers[provider_id] = provider
        
        return providers
    
    def _parse_model(self, model_id: str, data: dict[str, Any], provider_id: str) -> ModelInfo:
        """解析单个模型数据"""
        limit_data = data.get("limit", {})
        cost_data = data.get("cost", {})
        cap_data = data.get("capabilities", {})
        
        if isinstance(limit_data, dict):
            context = limit_data.get("context", 200_000)
            input_limit = limit_data.get("input")
            output = limit_data.get("output", 8_192)
        else:
            context, input_limit, output = 200_000, None, 8_192
        
        if isinstance(cost_data, dict):
            input_cost = cost_data.get("input", 0.0)
            output_cost = cost_data.get("output", 0.0)
            cache_read = cost_data.get("cache_read", 0.0)
            cache_write = cost_data.get("cache_write", 0.0)
        else:
            input_cost, output_cost, cache_read, cache_write = 0.0, 0.0, 0.0, 0.0
        
        if isinstance(cap_data, dict):
            temperature = cap_data.get("temperature", True)
            reasoning = cap_data.get("reasoning", False)
            attachment = cap_data.get("attachment", True)
            tool_call = cap_data.get("tool_call", True)
            streaming = cap_data.get("streaming", True)
        else:
            temperature, reasoning, attachment, tool_call, streaming = True, False, True, True, True
        
        return ModelInfo(
            id=data.get("id", model_id),
            name=data.get("name", model_id),
            provider=provider_id,
            limit=ModelLimit(context=context, input=input_limit, output=output),
            cost=ModelCost(input=input_cost, output=output_cost, cache_read=cache_read, cache_write=cache_write),
            capabilities=ModelCapabilities(
                temperature=temperature,
                reasoning=reasoning,
                attachment=attachment,
                tool_call=tool_call,
                streaming=streaming,
            ),
            status=data.get("status", "active"),
            release_date=data.get("release_date"),
        )
    
    def get_model(self, model_id: str) -> ModelInfo | None:
        """根据模型ID获取模型信息（宽松匹配，优先最长匹配）"""
        self._ensure_loaded()
        model_id_lower = model_id.lower()
        
        candidates: list[tuple[int, ModelInfo]] = []
        
        for provider in self._providers.values():
            for model_key, model in provider.models.items():
                score = 0
                if model_key == model_id_lower or model.id.lower() == model_id_lower:
                    score = 100
                elif model_id_lower.startswith(model_key) or model_id_lower.startswith(model.id.lower()):
                    score = len(model_key) + len(model.id.lower())
                elif model_key in model_id_lower or model.id.lower() in model_id_lower:
                    score = len(model_key)
                
                if score > 0:
                    candidates.append((score, model))
        
        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            return candidates[0][1]
        
        return None
    
    def get_model_limit(self, model_id: str) -> ModelLimit:
        """获取模型限制配置"""
        model = self.get_model(model_id)
        if model:
            return model.limit
        return self.DEFAULT_MODEL_LIMIT
    
    def get_context_window(self, model_id: str, betas: list[str] | None = None) -> int:
        """
        获取模型的上下文窗口大小。
        
        支持以下扩展：
        1. [1m] 后缀：显式选择1M上下文
        2. betas 包含 context-1m：启用1M上下文
        """
        if "[1m]" in model_id.lower():
            return 1_000_000
        
        if betas and "context-1m" in betas:
            return 1_000_000
        
        model = self.get_model(model_id)
        if model:
            return model.limit.context
        
        return self.DEFAULT_MODEL_LIMIT.context
    
    def get_max_output_tokens(self, model_id: str) -> int:
        """获取模型最大输出tokens"""
        model = self.get_model(model_id)
        if model:
            return model.limit.output
        return self.DEFAULT_MODEL_LIMIT.output
    
    def get_all_providers(self) -> dict[str, ProviderInfo]:
        """获取所有提供商信息"""
        self._ensure_loaded()
        return dict(self._providers)
    
    def get_all_models(self) -> dict[str, ModelInfo]:
        """获取所有模型信息"""
        self._ensure_loaded()
        models: dict[str, ModelInfo] = {}
        for provider in self._providers.values():
            for model_id, model in provider.models.items():
                models[model_id] = model
        return models
    
    def refresh(self) -> bool:
        """
        手动刷新模型配置。
        
        返回是否成功刷新。
        """
        data = self._client.refresh()
        if data:
            self._providers = self._parse_models_dev_data(data)
            self._loaded = True
            return True
        return False
    
    def matches_model_id(self, query_id: str, model_id: str) -> bool:
        """检查查询ID是否匹配模型ID（宽松匹配）"""
        query_lower = query_id.lower()
        model_lower = model_id.lower()
        
        patterns = [
            query_lower,
            query_lower.replace("-", ""),
            query_lower.replace(".", "-"),
            re.sub(r"-v\d+$", "", query_lower),
        ]
        
        for pattern in patterns:
            if pattern in model_lower or model_lower in pattern:
                return True
        
        return False


_registry_instance: ModelsRegistry | None = None


def get_models_registry(
    cache_path: Path | None = None,
    enable_background_refresh: bool = True,
) -> ModelsRegistry:
    """获取全局模型注册表（延迟初始化）"""
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = ModelsRegistry(
            cache_path=cache_path,
            enable_background_refresh=enable_background_refresh,
        )
    return _registry_instance


def reset_registry() -> None:
    """重置全局注册表（用于测试）"""
    global _registry_instance
    if _registry_instance:
        _registry_instance.stop_background_refresh()
        _registry_instance._client.stop_background_refresh()
    _registry_instance = None


def get_model_limit(model_id: str) -> ModelLimit:
    """便捷函数：获取模型限制"""
    return get_models_registry().get_model_limit(model_id)


def get_context_window(model_id: str, betas: list[str] | None = None) -> int:
    """便捷函数：获取上下文窗口"""
    return get_models_registry().get_context_window(model_id, betas)


def get_max_output_tokens(model_id: str) -> int:
    """便捷函数：获取最大输出tokens"""
    return get_models_registry().get_max_output_tokens(model_id)


def refresh_models() -> bool:
    """便捷函数：手动刷新模型配置"""
    return get_models_registry().refresh()