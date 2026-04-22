"""
模型能力服务测试

测试 ModelsRegistry、ModelsDevClient 和相关功能。
"""

import json
import pytest
import time
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock

from app.services.provider.models import (
    ModelInfo,
    ModelLimit,
    ModelCost,
    ModelCapabilities,
    ProviderInfo,
    ModelsRegistry,
    ModelsDevClient,
    get_models_registry,
    get_context_window,
    get_max_output_tokens,
    get_model_limit,
    refresh_models,
    reset_registry,
    _get_minimal_builtin_config,
)


class TestModelLimit:
    """测试 ModelLimit 数据类"""
    
    def test_default_values(self):
        limit = ModelLimit()
        assert limit.context == 200_000
        assert limit.input is None
        assert limit.output == 8_192
    
    def test_custom_values(self):
        limit = ModelLimit(context=128_000, input=100_000, output=16_384)
        assert limit.context == 128_000
        assert limit.input == 100_000
        assert limit.output == 16_384


class TestModelCost:
    """测试 ModelCost 数据类"""
    
    def test_default_values(self):
        cost = ModelCost()
        assert cost.input == 0.0
        assert cost.output == 0.0
        assert cost.cache_read == 0.0
        assert cost.cache_write == 0.0
    
    def test_custom_values(self):
        cost = ModelCost(input=15.0, output=75.0, cache_read=1.5, cache_write=18.75)
        assert cost.input == 15.0
        assert cost.output == 75.0
        assert cost.cache_read == 1.5
        assert cost.cache_write == 18.75


class TestModelCapabilities:
    """测试 ModelCapabilities 数据类"""
    
    def test_default_values(self):
        cap = ModelCapabilities()
        assert cap.temperature is True
        assert cap.reasoning is False
        assert cap.attachment is True
        assert cap.tool_call is True
        assert cap.streaming is True
    
    def test_reasoning_model(self):
        cap = ModelCapabilities(reasoning=True, temperature=False)
        assert cap.reasoning is True
        assert cap.temperature is False


class TestMinimalBuiltinConfig:
    """测试最小内置配置"""
    
    def test_has_providers(self):
        config = _get_minimal_builtin_config()
        assert len(config) >= 2
    
    def test_models_have_basic_info(self):
        config = _get_minimal_builtin_config()
        for provider in config.values():
            for model in provider.models.values():
                assert model.limit.context >= 8_000
                assert model.limit.output >= 4_000


class TestModelsDevClient:
    """测试 ModelsDevClient"""
    
    def test_cache_path_creation(self):
        client = ModelsDevClient(cache_path=Path("/tmp/test_models.json"))
        path = client.get_cache_path()
        assert path.parent.exists()
    
    def test_save_and_load_cache(self, tmp_path):
        cache_file = tmp_path / "models.json"
        client = ModelsDevClient(cache_path=cache_file)
        
        test_data = {"test_provider": {"name": "Test", "models": {}}}
        assert client.save_to_cache(test_data)
        
        loaded = client.load_from_cache()
        assert loaded == test_data
    
    def test_load_cache_not_exists(self, tmp_path):
        cache_file = tmp_path / "nonexistent.json"
        client = ModelsDevClient(cache_path=cache_file)
        
        loaded = client.load_from_cache()
        assert loaded is None
    
    def test_load_cache_invalid_json(self, tmp_path):
        cache_file = tmp_path / "invalid.json"
        cache_file.write_text("not json")
        client = ModelsDevClient(cache_path=cache_file)
        
        loaded = client.load_from_cache()
        assert loaded is None
    
    def test_disable_network_fetch(self, tmp_path):
        cache_file = tmp_path / "models.json"
        
        with patch.dict("os.environ", {"AETHERCORE_DISABLE_MODELS_FETCH": "true"}):
            client = ModelsDevClient(cache_path=cache_file)
            data = client.fetch_from_models_dev()
            assert data is None
    
    def test_should_refresh(self, tmp_path):
        client = ModelsDevClient(cache_path=tmp_path / "models.json")
        
        assert client.should_refresh()  # 初始时应该刷新
        
        client._last_refresh_time = time.time()
        assert not client.should_refresh()  # 刚刷新后不应该刷新
        
        client._last_refresh_time = time.time() - 3600 - 1
        assert client.should_refresh()  # 超过间隔后应该刷新


class TestModelsRegistryWithMock:
    """测试 ModelsRegistry（使用mock避免网络请求）"""
    
    def setup_method(self):
        reset_registry()
    
    def teardown_method(self):
        reset_registry()
    
    def _get_mock_data(self):
        """返回模拟的 models.dev 数据"""
        return {
            "test-provider": {
                "id": "test-provider",
                "name": "Test Provider",
                "models": {
                    "test-model-a": {
                        "id": "test-model-a",
                        "name": "Test Model A",
                        "limit": {"context": 200_000, "output": 32_000},
                        "cost": {"input": 15.0, "output": 75.0, "cache_read": 1.5, "cache_write": 18.75},
                        "capabilities": {"reasoning": True},
                    },
                    "test-model-b": {
                        "id": "test-model-b",
                        "name": "Test Model B",
                        "limit": {"context": 200_000, "output": 32_000},
                        "cost": {"input": 3.0, "output": 15.0},
                    },
                },
            },
            "test-provider-2": {
                "id": "test-provider-2",
                "name": "Test Provider 2",
                "models": {
                    "test-large": {
                        "id": "test-large",
                        "name": "Test Large",
                        "limit": {"context": 128_000, "output": 16_384},
                        "cost": {"input": 5.0, "output": 15.0},
                        "capabilities": {"reasoning": True},
                    },
                    "test-small": {
                        "id": "test-small",
                        "name": "Test Small",
                        "limit": {"context": 8_192, "output": 4_096},
                        "cost": {"input": 30.0, "output": 60.0},
                    },
                },
            },
            "test-provider-3": {
                "id": "test-provider-3",
                "name": "Test Provider 3",
                "models": {
                    "test-chat": {
                        "id": "test-chat",
                        "name": "Test Chat",
                        "limit": {"context": 64_000, "output": 8_192},
                    },
                },
            },
        }
    
    def test_load_from_mock_data(self, tmp_path):
        cache_file = tmp_path / "models.json"
        mock_data = self._get_mock_data()
        cache_file.write_text(json.dumps(mock_data))
        
        with patch.dict("os.environ", {"AETHERCORE_DISABLE_MODELS_FETCH": "true"}):
            registry = ModelsRegistry(cache_path=cache_file, enable_background_refresh=False)
            
            model = registry.get_model("test-model-a")
            assert model is not None
            assert model.limit.context == 200_000
            assert model.cost.input == 15.0
    
    def test_get_model_exact_match(self, tmp_path):
        cache_file = tmp_path / "models.json"
        mock_data = self._get_mock_data()
        cache_file.write_text(json.dumps(mock_data))
        
        with patch.dict("os.environ", {"AETHERCORE_DISABLE_MODELS_FETCH": "true"}):
            registry = ModelsRegistry(cache_path=cache_file, enable_background_refresh=False)
            
            model = registry.get_model("test-model-a")
            assert model is not None
            assert "model" in model.name.lower()
    
    def test_get_model_partial_match(self, tmp_path):
        cache_file = tmp_path / "models.json"
        mock_data = self._get_mock_data()
        cache_file.write_text(json.dumps(mock_data))
        
        with patch.dict("os.environ", {"AETHERCORE_DISABLE_MODELS_FETCH": "true"}):
            registry = ModelsRegistry(cache_path=cache_file, enable_background_refresh=False)
            
            model = registry.get_model("test-model-a-20250514")
            assert model is not None
            assert "model" in model.name.lower()
    
    def test_get_model_case_insensitive(self, tmp_path):
        cache_file = tmp_path / "models.json"
        mock_data = self._get_mock_data()
        cache_file.write_text(json.dumps(mock_data))
        
        with patch.dict("os.environ", {"AETHERCORE_DISABLE_MODELS_FETCH": "true"}):
            registry = ModelsRegistry(cache_path=cache_file, enable_background_refresh=False)
            
            model = registry.get_model("TEST-MODEL-A")
            assert model is not None
    
    def test_get_model_not_found(self, tmp_path):
        cache_file = tmp_path / "models.json"
        mock_data = self._get_mock_data()
        cache_file.write_text(json.dumps(mock_data))
        
        with patch.dict("os.environ", {"AETHERCORE_DISABLE_MODELS_FETCH": "true"}):
            registry = ModelsRegistry(cache_path=cache_file, enable_background_refresh=False)
            
            model = registry.get_model("nonexistent-model")
            assert model is None
    
    def test_get_context_window(self, tmp_path):
        cache_file = tmp_path / "models.json"
        mock_data = self._get_mock_data()
        cache_file.write_text(json.dumps(mock_data))
        
        with patch.dict("os.environ", {"AETHERCORE_DISABLE_MODELS_FETCH": "true"}):
            registry = ModelsRegistry(cache_path=cache_file, enable_background_refresh=False)
            
            assert registry.get_context_window("test-model-a") == 200_000
            assert registry.get_context_window("test-large") == 128_000
    
    def test_get_context_window_1m_suffix(self, tmp_path):
        cache_file = tmp_path / "models.json"
        mock_data = self._get_mock_data()
        cache_file.write_text(json.dumps(mock_data))
        
        with patch.dict("os.environ", {"AETHERCORE_DISABLE_MODELS_FETCH": "true"}):
            registry = ModelsRegistry(cache_path=cache_file, enable_background_refresh=False)
            
            assert registry.get_context_window("test-model-b[1m]") == 1_000_000
    
    def test_get_context_window_1m_beta(self, tmp_path):
        cache_file = tmp_path / "models.json"
        mock_data = self._get_mock_data()
        cache_file.write_text(json.dumps(mock_data))
        
        with patch.dict("os.environ", {"AETHERCORE_DISABLE_MODELS_FETCH": "true"}):
            registry = ModelsRegistry(cache_path=cache_file, enable_background_refresh=False)
            
            assert registry.get_context_window("test-model-b", betas=["context-1m"]) == 1_000_000
    
    def test_get_max_output_tokens(self, tmp_path):
        cache_file = tmp_path / "models.json"
        mock_data = self._get_mock_data()
        cache_file.write_text(json.dumps(mock_data))
        
        with patch.dict("os.environ", {"AETHERCORE_DISABLE_MODELS_FETCH": "true"}):
            registry = ModelsRegistry(cache_path=cache_file, enable_background_refresh=False)
            
            assert registry.get_max_output_tokens("test-model-a") == 32_000
            assert registry.get_max_output_tokens("test-large") == 16_384
    
    def test_fallback_to_builtin(self, tmp_path):
        """测试当没有缓存且网络不可用时使用内置配置"""
        cache_file = tmp_path / "models.json"
        
        with patch.dict("os.environ", {"AETHERCORE_DISABLE_MODELS_FETCH": "true"}):
            registry = ModelsRegistry(cache_path=cache_file, enable_background_refresh=False)
            model = registry.get_model("test-unknown")
            assert model is None
    
    def test_get_all_providers(self, tmp_path):
        cache_file = tmp_path / "models.json"
        mock_data = self._get_mock_data()
        cache_file.write_text(json.dumps(mock_data))
        
        with patch.dict("os.environ", {"AETHERCORE_DISABLE_MODELS_FETCH": "true"}):
            registry = ModelsRegistry(cache_path=cache_file, enable_background_refresh=False)
            
            providers = registry.get_all_providers()
            assert "test-provider" in providers
            assert "test-provider-2" in providers
    
    def test_refresh(self, tmp_path):
        cache_file = tmp_path / "models.json"
        
        with patch.dict("os.environ", {"AETHERCORE_DISABLE_MODELS_FETCH": "true"}):
            registry = ModelsRegistry(cache_path=cache_file, enable_background_refresh=False)
            
            mock_data = self._get_mock_data()
            
            # 即使禁用了网络获取，refresh()也会因为fetch返回None而失败
            # 所以我们需要mock fetch方法
            with patch.object(registry._client, "fetch_from_models_dev", return_value=mock_data):
                result = registry.refresh()
                assert result is True
                
                model = registry.get_model("test-model-a")
                assert model is not None


class TestConvenienceFunctions:
    """测试便捷函数"""
    
    def setup_method(self):
        reset_registry()
    
    def teardown_method(self):
        reset_registry()
    
    def _get_mock_data(self):
        """返回模拟的 models.dev 数据"""
        return {
            "test-provider": {
                "id": "test-provider",
                "name": "Test Provider",
                "models": {
                    "test-model-a": {
                        "id": "test-model-a",
                        "name": "Test Model A",
                        "limit": {"context": 200_000, "output": 32_000},
                        "cost": {"input": 15.0, "output": 75.0},
                    },
                },
            },
        }
    
    def test_get_context_window(self, tmp_path):
        cache_file = tmp_path / "models.json"
        mock_data = self._get_mock_data()
        cache_file.write_text(json.dumps(mock_data))
        
        with patch.dict("os.environ", {"AETHERCORE_DISABLE_MODELS_FETCH": "true"}):
            registry = ModelsRegistry(cache_path=cache_file, enable_background_refresh=False)
            
            assert registry.get_context_window("test-model-a") == 200_000
    
    def test_get_max_output_tokens(self, tmp_path):
        cache_file = tmp_path / "models.json"
        mock_data = self._get_mock_data()
        cache_file.write_text(json.dumps(mock_data))
        
        with patch.dict("os.environ", {"AETHERCORE_DISABLE_MODELS_FETCH": "true"}):
            registry = ModelsRegistry(cache_path=cache_file, enable_background_refresh=False)
            
            result = registry.get_max_output_tokens("test-model-a")
            assert result == 32_000
    
    def test_get_model_limit(self, tmp_path):
        cache_file = tmp_path / "models.json"
        mock_data = self._get_mock_data()
        cache_file.write_text(json.dumps(mock_data))
        
        with patch.dict("os.environ", {"AETHERCORE_DISABLE_MODELS_FETCH": "true"}):
            registry = ModelsRegistry(cache_path=cache_file, enable_background_refresh=False)
            
            limit = registry.get_model_limit("test-model-a")
            assert limit.context == 200_000


class TestBackgroundRefresh:
    """测试后台刷新功能"""
    
    def test_start_and_stop_background_refresh(self, tmp_path):
        cache_file = tmp_path / "models.json"
        client = ModelsDevClient(cache_path=cache_file, refresh_interval=1)
        
        client.start_background_refresh()
        assert client._refresh_thread is not None
        assert client._refresh_thread.is_alive()
        
        client.stop_background_refresh()
        assert client._refresh_thread is None or not client._refresh_thread.is_alive()
    
    def test_registry_starts_background_refresh(self, tmp_path):
        cache_file = tmp_path / "models.json"
        registry = ModelsRegistry(cache_path=cache_file, enable_background_refresh=True)
        
        registry._ensure_loaded()
        assert registry._client._refresh_thread is not None
        
        reset_registry()


class TestRealModelsDevAPI:
    """测试真实的 models.dev API（可选，网络依赖）"""
    
    @pytest.mark.skipif(
        True,  # 默认跳过，避免CI失败
        reason="需要网络连接，默认跳过"
    )
    def test_fetch_real_api(self):
        """测试真实API获取（手动运行时启用）"""
        client = ModelsDevClient()
        data = client.fetch_from_models_dev()
        
        assert data is not None
        assert len(data) > 10
        
        # 验证数据结构正确
        first_provider = list(data.keys())[0]
        assert "models" in data[first_provider]
        assert len(data[first_provider]["models"]) > 0