import os
from cryptography.fernet import Fernet
import json
from logger_setup import global_logger as logger


class SecretStore:
    """
    加密存储服务，用于保存和读取 LLM 的 API Token 等敏感信息。
    """

    def __init__(self, key_path: str = "secret.key", store_path: str = "llm_config.enc"):
        self.key_path = key_path
        self.store_path = store_path
        self.key = self._load_or_create_key()
        self.cipher = Fernet(self.key)

    def _load_or_create_key(self) -> bytes:
        """
        加载现有的加密密钥，如果不存在则生成新密钥。
        注意：生产环境中应当妥善保管此文件。
        """
        if os.path.exists(self.key_path):
            with open(self.key_path, "rb") as f:
                return f.read()
        else:
            key = Fernet.generate_key()
            with open(self.key_path, "wb") as f:
                f.write(key)
            return key

    def save_config(self, config: dict):
        """
        加密并保存配置信息。
        包含：api_token, base_url, model, timeout, authorized 等。
        """
        config_bytes = json.dumps(config).encode("utf-8")
        encrypted_data = self.cipher.encrypt(config_bytes)

        with open(self.store_path, "wb") as f:
            f.write(encrypted_data)
        logger.info("配置信息已加密并保存。")

    def load_config(self) -> dict:
        """
        读取并解密配置信息。如果文件不存在则返回空字典。
        """
        if not os.path.exists(self.store_path):
            return {}

        try:
            with open(self.store_path, "rb") as f:
                encrypted_data = f.read()

            decrypted_bytes = self.cipher.decrypt(encrypted_data)
            return json.loads(decrypted_bytes.decode("utf-8"))
        except Exception as e:
            logger.error(f"解析配置信息失败: {e}")
            return {}

    def get_auth_status(self) -> bool:
        """获取当前系统是否已经永久授权"""
        config = self.load_config()
        return config.get("authorized", False)

    def set_auth_status(self, status: bool):
        """设置授权状态"""
        config = self.load_config()
        config["authorized"] = status
        self.save_config(config)

    def _normalize_profile_name(self, name: str) -> str:
        n = str(name or "").strip()
        if not n:
            return "default"
        return n

    def get_llm_profiles(self) -> dict:
        config = self.load_config() or {}
        profiles = {}
        raw_profiles = config.get("llm_profiles")
        if isinstance(raw_profiles, dict):
            for k, v in raw_profiles.items():
                if not isinstance(v, dict):
                    continue
                profiles[self._normalize_profile_name(k)] = dict(v)

        legacy_fields = ["api_token", "base_url", "model", "timeout", "retry"]
        if any(k in config for k in legacy_fields):
            default_profile = profiles.get("default") or {}
            for k in legacy_fields:
                if k in config and k not in default_profile:
                    default_profile[k] = config.get(k)
            profiles["default"] = default_profile

        if "default" not in profiles:
            profiles["default"] = {}
        return profiles

    def save_llm_profile(self, profile: str, updates: dict) -> None:
        p = self._normalize_profile_name(profile)
        config = self.load_config() or {}

        profiles = self.get_llm_profiles()
        existing = profiles.get(p) or {}
        incoming = dict(updates or {})
        if incoming.get("api_token") in ["", "********", None]:
            incoming.pop("api_token", None)

        merged = dict(existing)
        merged.update(incoming)

        profiles[p] = merged
        config["llm_profiles"] = profiles
        self.save_config(config)

    def get_llm_profile(self, profile: str, *, mask_token: bool = True) -> dict:
        p = self._normalize_profile_name(profile)
        profiles = self.get_llm_profiles()
        cfg = dict(profiles.get(p) or {})
        if mask_token and "api_token" in cfg:
            cfg["api_token"] = "********"
        return cfg

    def list_llm_profile_names(self) -> list:
        profiles = self.get_llm_profiles()
        names = sorted(set(profiles.keys()))
        if "default" in names:
            names.remove("default")
            names.insert(0, "default")
        return names
