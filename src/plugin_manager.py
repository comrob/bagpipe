"""
Compatibility shim: plugins written as `from plugin_manager import BasePlugin`
keep working. New plugins should import from bagpipe.convert.plugin_manager.
"""
from bagpipe.convert.plugin_manager import BasePlugin, PluginManager  # noqa: F401
