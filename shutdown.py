import common
import control_progress
import paramiko
import socket

CONFIG = common.load_config("config.json")
CONTROLLER_HOSTNAMES = CONFIG["controller_hostnames"]
CONTROLLER_SSH_USERNAME = CONFIG["controller_ssh_username"]
CONTROLLER_SSH_PASSWORD = CONFIG["controller_ssh_password"]
SERVER_HOSTNAME = CONFIG["server_hostname"]
SERVER_SSH_USERNAME = CONFIG["server_ssh_username"]
SERVER_SSH_PASSWORD = CONFIG["server_ssh_password"]


def main() -> None:
    components: dict[str, dict] = {}
    control_progress.write_control_progress("shutdown", "running", {})

    for hostname in CONTROLLER_HOSTNAMES:
        ssh_client = paramiko.SSHClient()
        ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            ssh_client.connect(
                hostname,
                username=CONTROLLER_SSH_USERNAME,
                password=CONTROLLER_SSH_PASSWORD,
            )
            ssh_client.exec_command("sudo shutdown -h now")
            control_progress.record_host_result(
                components,
                "controller:" + hostname,
                ok=True,
                message="shutdown command sent",
            )
        except (socket.gaierror, TimeoutError, Exception) as e:
            control_progress.record_host_result(
                components,
                "controller:" + hostname,
                ok=False,
                message=str(e),
            )
        finally:
            try:
                ssh_client.close()
            except Exception:
                pass
        print("Controller " + hostname + " shut down")
        control_progress.write_control_progress("shutdown", "running", dict(components))

    ssh_client = paramiko.SSHClient()
    ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh_client.connect(
            SERVER_HOSTNAME,
            username=SERVER_SSH_USERNAME,
            password=SERVER_SSH_PASSWORD,
        )
        ssh_client.exec_command("sudo shutdown -h now")
        control_progress.record_host_result(
            components,
            "server:" + SERVER_HOSTNAME,
            ok=True,
            message="shutdown command sent",
        )
    except (socket.gaierror, TimeoutError, Exception) as e:
        control_progress.record_host_result(
            components,
            "server:" + SERVER_HOSTNAME,
            ok=False,
            message=str(e),
        )
    finally:
        try:
            ssh_client.close()
        except Exception:
            pass
    print("Server " + SERVER_HOSTNAME + " shut down")

    control_progress.write_control_progress("shutdown", "completed", components)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        control_progress.write_control_progress(
            "shutdown",
            "error",
            {"_error": {"ok": False, "message": str(e)}},
        )
        raise
