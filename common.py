import collections
import json
import logging
import os
import socket
import sys

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def load_json(path):
    if path == "config.json" and os.environ.get("CONFIG_FILE"):
        path = os.environ.get("CONFIG_FILE")
    with open(path, encoding="utf-8") as file:
        return json.load(file, object_pairs_hook=lambda p: collections.OrderedDict(p))


def load_config(path="config.json"):
    """Load config (OrderedDict). Fills teacher-style keys and legacy keys for Neo4j / main_service / Streamlit UI."""
    cfg = load_json(path)

    if "neo4j" in cfg:
        neo = cfg["neo4j"]
        if os.environ.get("NEO4J_URI"):
            neo["uri"] = os.environ["NEO4J_URI"]
        if os.environ.get("NEO4J_USER"):
            neo["username"] = os.environ["NEO4J_USER"]
        if os.environ.get("NEO4J_PASSWORD"):
            neo["password"] = os.environ["NEO4J_PASSWORD"]

    mh = cfg.get("server_hostname") or cfg.get("mqtt_broker_host")
    mp = cfg.get("server_mqtt_port")
    if mp is None:
        mp = cfg.get("mqtt_broker_port", 1883)
    mp = int(mp)
    if not mh:
        mh = "localhost"
    cfg["mqtt_broker_host"] = mh
    cfg["mqtt_broker_port"] = mp
    cfg["server_hostname"] = cfg.get("server_hostname") or mh
    cfg["server_mqtt_port"] = int(cfg.get("server_mqtt_port") or mp)

    if os.environ.get("MQTT_BROKER_HOST"):
        h = os.environ["MQTT_BROKER_HOST"]
        cfg["mqtt_broker_host"] = h
        cfg["server_hostname"] = h
    if os.environ.get("MQTT_BROKER_PORT"):
        p = int(os.environ["MQTT_BROKER_PORT"])
        cfg["mqtt_broker_port"] = p
        cfg["server_mqtt_port"] = p

    if cfg.get("mqtt_broker_host") == "broker" or cfg.get("server_hostname") == "broker":
        try:
            socket.gethostbyname("broker")
        except socket.gaierror:
            fb = "broker.hivemq.com"
            cfg["mqtt_broker_host"] = fb
            cfg["server_hostname"] = fb

    cfg.setdefault("controller_ssh_username", cfg.get("ssh_username", "robot"))
    cfg.setdefault("controller_ssh_password", cfg.get("ssh_password", ""))
    cfg.setdefault(
        "server_ssh_username",
        cfg.get("mqtt_broker_ssh_username", cfg.get("controller_ssh_username", "robot")),
    )
    cfg.setdefault(
        "server_ssh_password",
        cfg.get("mqtt_broker_ssh_password", cfg.get("controller_ssh_password", "")),
    )
    if os.environ.get("SSH_USERNAME"):
        cfg["controller_ssh_username"] = os.environ["SSH_USERNAME"]
    if os.environ.get("SSH_PASSWORD"):
        cfg["controller_ssh_password"] = os.environ["SSH_PASSWORD"]

    if "controller_hostnames" not in cfg and cfg.get("program_script_paths"):
        cfg["controller_hostnames"] = list(cfg["program_script_paths"].keys())

    cfg.setdefault("system_start_delay", cfg.get("mqtt_publication_interval", 5.0))

    cfg.setdefault("ssh_username", cfg["controller_ssh_username"])
    cfg.setdefault("ssh_password", cfg["controller_ssh_password"])
    cfg.setdefault("mqtt_broker_ssh_username", cfg["server_ssh_username"])
    cfg.setdefault("mqtt_broker_ssh_password", cfg["server_ssh_password"])

    return cfg


def render_topic(context, source_id, target_id):
    return "/".join([context, source_id, target_id])


def parse_topic(topic):
    return tuple(topic.split("/"))


def serialize_object(object_):
    return json.dumps(object_, separators=(",", ":"))


def deserialize_object(string):
    return json.loads(string, object_pairs_hook=lambda p: collections.OrderedDict(p))


def setup_logger(
    name: str,
    level: int = logging.INFO,
    *,
    log_file: str | None = None,
) -> logging.Logger:
    """统一日志（改进4）：控制台 + 可选文件。"""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    if log_file:
        log_dir = os.path.dirname(os.path.abspath(log_file))
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    logger.propagate = False
    return logger


def new_event_log_session_id(when=None) -> str:
    """可读 Neo4j Session id，与 ``event_log_*.csv`` 风格一致：``event_log_YYYYMMDD_HHMMSS``。

    ``when`` 为 ``datetime.datetime`` 时用该时刻；否则为当前时间（live / 控制台新会话）。"""
    import datetime as _dt

    t = when if when is not None else _dt.datetime.now()
    return "event_log_{}".format(t.strftime("%Y%m%d_%H%M%S"))


def new_replay_output_session_id() -> str:
    """从已有 Session/CSV 回放写入 **新** Session 时的 id：``event_log_replay_YYYYMMDD_HHMMSS_####``（末四位为 PID 余数，降低同秒冲突）。"""
    import datetime as _dt

    t = _dt.datetime.now()
    return "event_log_replay_{}_{:04d}".format(
        t.strftime("%Y%m%d_%H%M%S"),
        os.getpid() % 10000,
    )
