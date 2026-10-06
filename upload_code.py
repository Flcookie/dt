import common
import paramiko
import os

CONFIG = common.load_config("config.json")
CONTROLLER_HOSTNAMES = CONFIG["controller_hostnames"]
CONTROLLER_SSH_USERNAME = CONFIG["controller_ssh_username"]
CONTROLLER_SSH_PASSWORD = CONFIG["controller_ssh_password"]
REMOTE_FOLDER_PATH = CONFIG["remote_folder_path"]
LOCAL_FOLDER_PATH = CONFIG["local_folder_path"]
LOCAL_CODE_PATHS = CONFIG["local_code_paths"]

for hostname in CONTROLLER_HOSTNAMES:
    code_paths = LOCAL_CODE_PATHS[hostname]
    ssh_client = paramiko.SSHClient()
    ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh_client.connect(
        hostname, username=CONTROLLER_SSH_USERNAME, password=CONTROLLER_SSH_PASSWORD
    )
    sftp_session = ssh_client.open_sftp()
    for code_path in code_paths:
        local_path = os.path.normpath(os.path.join(LOCAL_FOLDER_PATH, code_path))
        remote_path = REMOTE_FOLDER_PATH + "/" + os.path.basename(code_path)
        sftp_session.put(local_path, remote_path)
        print(
            "Code " + local_path + " uploaded to folder " + REMOTE_FOLDER_PATH
            + " on controller " + hostname
        )
    sftp_session.close()
    ssh_client.close()
