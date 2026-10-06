import common
import control_progress
import paramiko
import time

CONFIG = common.load_config("config.json")
CONTROLLER_HOSTNAMES = CONFIG["controller_hostnames"]
CONTROLLER_SSH_USERNAME = CONFIG["controller_ssh_username"]
CONTROLLER_SSH_PASSWORD = CONFIG["controller_ssh_password"]
REMOTE_FOLDER_PATH = CONFIG["remote_folder_path"]
PROGRAM_SCRIPT_PATHS = CONFIG["program_script_paths"]
PROGRAM_STOP_TIME = CONFIG["program_stop_time"]
PROGRAM_PID_PATH = REMOTE_FOLDER_PATH + "/program.pid"
PROGRAM_LOG_PATH = REMOTE_FOLDER_PATH + "/program.log"


def main() -> None:
    components: dict[str, dict] = {}
    control_progress.write_control_progress("stop_programs", "running", {})

    ssh_clients: dict[str, paramiko.SSHClient] = {}
    try:
        for hostname in CONTROLLER_HOSTNAMES:
            script_path = REMOTE_FOLDER_PATH + "/" + PROGRAM_SCRIPT_PATHS[hostname]
            try:
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect(
                    hostname,
                    username=CONTROLLER_SSH_USERNAME,
                    password=CONTROLLER_SSH_PASSWORD,
                )
                ssh_clients[hostname] = client
                _, stdout, _ = client.exec_command(
                    "[ -f " + PROGRAM_PID_PATH + " ] && echo True || echo False"
                )
                if stdout.readline().strip() == "True":
                    _, stdout, _ = client.exec_command("cat " + PROGRAM_PID_PATH)
                    program_pid = stdout.readline().strip()
                    client.exec_command("kill -SIGTERM " + program_pid)
                    control_progress.record_host_result(
                        components,
                        hostname,
                        ok=True,
                        message="SIGTERM sent · {}".format(program_pid),
                    )
                else:
                    control_progress.record_host_result(
                        components,
                        hostname,
                        ok=True,
                        message="No PID file (already stopped?)",
                    )
            except Exception as e:
                control_progress.record_host_result(
                    components,
                    hostname,
                    ok=False,
                    message=str(e),
                )
            control_progress.write_control_progress(
                "stop_programs", "running", dict(components)
            )

        time.sleep(PROGRAM_STOP_TIME)

        for hostname in CONTROLLER_HOSTNAMES:
            script_path = REMOTE_FOLDER_PATH + "/" + PROGRAM_SCRIPT_PATHS[hostname]
            client = ssh_clients.get(hostname)
            if client is None:
                continue
            try:
                _, stdout, _ = client.exec_command(
                    "[ -f " + PROGRAM_PID_PATH + " ] && echo True || echo False"
                )
                still_there = stdout.readline().strip() == "True"
                if not still_there:
                    print("Script " + script_path + " halted on " + hostname)
                    control_progress.record_host_result(
                        components,
                        hostname,
                        ok=True,
                        message="Stopped (no PID file)",
                    )
                else:
                    print("Script " + script_path + " failed to halt on " + hostname)
                    log_lines: list[str] = []
                    try:
                        _, stdout, _ = client.exec_command("cat " + PROGRAM_LOG_PATH)
                        log_lines = stdout.readlines()
                        for line in log_lines:
                            print(line, end="")
                    except Exception:
                        pass
                    log_tail = "".join(log_lines[-30:]) if log_lines else ""
                    control_progress.record_host_result(
                        components,
                        hostname,
                        ok=False,
                        message=log_tail or "PID file still present",
                    )
            except Exception as e:
                control_progress.record_host_result(
                    components,
                    hostname,
                    ok=False,
                    message=str(e),
                )
            control_progress.write_control_progress(
                "stop_programs", "running", dict(components)
            )
    finally:
        for _h, c in ssh_clients.items():
            try:
                c.close()
            except Exception:
                pass

    all_ok = not CONTROLLER_HOSTNAMES or all(
        (components.get(h) or {}).get("ok") is True for h in CONTROLLER_HOSTNAMES
    )
    final = "completed" if all_ok else "completed_with_errors"
    control_progress.write_control_progress("stop_programs", final, components)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        control_progress.write_control_progress(
            "stop_programs",
            "error",
            {"_error": {"ok": False, "message": str(e)}},
        )
        raise
