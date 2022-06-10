"""
Get username and password of docker registry
"""
from base64 import b64decode
import os
import json

def get_docker_registry_login(registry_name):
    """
    Get docker registry login by reading `$HOME/.docker/config.json` file
    """
    user_name, password = None, None
    DOCKER_REGISTRY_PASSWORD_FILE_PATH = os.path.join(os.environ['HOME'], '.docker/config.json')

    with open(DOCKER_REGISTRY_PASSWORD_FILE_PATH,'r',encoding='utf8')as fp:
        docker_config_json_data = json.load(fp)
        for docker_registry_item in docker_config_json_data["auths"]:
            if registry_name == docker_registry_item:
                authorizationData = docker_config_json_data["auths"][docker_registry_item]["auth"]
                auth_token = b64decode(authorizationData).decode()
                user_name, password = auth_token.split(":")
                return user_name, password
            else:
                print("No found this registry and unable locate credentials")
                return