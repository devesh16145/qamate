import json
import os

def load_config():
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config.json')
    if not os.path.exists(config_path):
        return {}
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def get_env_config(env_name):
    config = load_config()
    return config.get('environments', {}).get(env_name, {})

def get_user_config(index):
    config = load_config()
    users = config.get('users', [])
    if index < len(users):
        return users[index]
    return {}
