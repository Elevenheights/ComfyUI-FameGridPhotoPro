from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

WEB_DIRECTORY = "./web"
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

try:
    from . import routes  # Register only inside a running ComfyUI server.
except ModuleNotFoundError as error:
    if error.name not in {"server", "folder_paths"}:
        raise
