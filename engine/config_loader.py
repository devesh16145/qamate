import json
import os
import shutil


def app_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ats_root():
    """Data root: ATS_ROOT when the app sets it (it differs from the code root in a
    packaged macOS app), else this checkout."""
    return os.environ.get("ATS_ROOT") or app_root()


def config_path():
    return os.path.join(ats_root(), "config.json")


def example_config_path():
    return os.path.join(app_root(), "config.example.json")


def ensure_config():
    """Create config.json from config.example.json when missing (first-run bootstrap)."""
    path = config_path()
    if os.path.exists(path):
        return path
    example = example_config_path()
    if os.path.exists(example):
        shutil.copyfile(example, path)
        return path
    return path


def load_config():
    path = ensure_config()
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_env_config(env_name):
    config = load_config()
    return config.get("environments", {}).get(env_name, {})


def get_user_config(index):
    config = load_config()
    users = config.get("users", [])
    if index < len(users):
        return users[index]
    return {}
