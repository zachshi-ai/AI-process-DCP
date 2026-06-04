import importlib
import os
import sys
from logger_setup import global_logger as logger


class PluginManager:
    """
    插件管理系统。
    允许动态加载位于 plugins/ 目录下的 Python 脚本。
    每个插件必须实现一个 `process(data: dict) -> dict` 方法。
    """

    def __init__(self, plugin_dir: str):
        self.plugin_dir = plugin_dir
        self.plugins = {}
        self._load_plugins()

    def _load_plugins(self):
        """
        遍历 plugin_dir，动态导入所有的 .py 文件（忽略以 _ 开头的文件）。
        """
        if not os.path.exists(self.plugin_dir):
            os.makedirs(self.plugin_dir, exist_ok=True)
            logger.info(f"已创建插件目录: {self.plugin_dir}")
            return

        sys.path.insert(0, self.plugin_dir)

        for filename in os.listdir(self.plugin_dir):
            if filename.endswith(".py") and not filename.startswith("_"):
                module_name = filename[:-3]
                try:
                    module = importlib.import_module(module_name)
                    if hasattr(module, "process"):
                        self.plugins[module_name] = module
                        logger.info(f"成功加载插件: {module_name}")
                    else:
                        logger.warning(f"插件 {module_name} 缺少 process 方法，跳过加载。")
                except Exception as e:
                    logger.error(f"加载插件 {module_name} 失败: {e}")

        sys.path.pop(0)

    def execute_plugins(self, data: dict) -> dict:
        """
        按顺序执行所有已加载的插件，对数据进行链式处理。
        """
        current_data = data
        for name, plugin in self.plugins.items():
            try:
                logger.info(f"正在执行插件: {name}")
                current_data = plugin.process(current_data)
            except Exception as e:
                logger.error(f"执行插件 {name} 时出错: {e}")
        return current_data
