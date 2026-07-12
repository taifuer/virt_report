"""配置加载。

从 config.yaml 读取配置，解析为带类型的 dataclass，并把相对路径解析为
相对于项目根目录的绝对路径。LLM API key 从环境变量读取（不在配置文件里）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


@dataclass
class MailingListSource:
    name: str
    type: str  # mbox | lore | lore_git | hyperkitty | mailarchive
    url: str


@dataclass
class GitLabSource:
    name: str
    project: str  # e.g. libvirt/libvirt
    url: str  # e.g. https://gitlab.com


@dataclass
class Sources:
    mailing_lists: list[MailingListSource] = field(default_factory=list)
    gitlab: list[GitLabSource] = field(default_factory=list)


@dataclass
class Storage:
    db_path: Path = PROJECT_ROOT / "data" / "virt_report.db"


@dataclass
class LLMConfig:
    provider: str = "deepseek"
    base_url: str = "https://api.deepseek.com"
    api_key_env: str = "DEEPSEEK_API_KEY"
    daily_model: str = "deepseek-flash"
    weekly_model: str = "deepseek-v4-pro"
    monthly_model: str = "deepseek-v4-pro"
    daily_top_n: int = 30

    @property
    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env)


@dataclass
class Render:
    output_dir: Path = PROJECT_ROOT / "site"
    site_url: str = ""


@dataclass
class Schedule:
    daily_cron: str = "17 9 * * *"
    weekly_cron: str = "23 9 * * 1"
    monthly_cron: str = "33 9 1 * *"


@dataclass
class Config:
    name: str = "virt-report"
    timezone: str = "Asia/Shanghai"
    sources: Sources = field(default_factory=Sources)
    storage: Storage = field(default_factory=Storage)
    llm: LLMConfig = field(default_factory=LLMConfig)
    render: Render = field(default_factory=Render)
    schedule: Schedule = field(default_factory=Schedule)

    @property
    def db_path(self) -> Path:
        return self.storage.db_path

    @property
    def output_dir(self) -> Path:
        return self.render.output_dir


def _resolve_path(p: str | Path, base: Path) -> Path:
    path = Path(p)
    return path if path.is_absolute() else (base / path).resolve()


def _load_dotenv(path: Path) -> None:
    """读取 .env 文件并设置环境变量 (不覆盖已存在的)。无依赖版本。"""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] in "\"'" and val[-1] == val[0]:
            val = val[1:-1]
        if key and key not in os.environ:
            os.environ[key] = val


def load_config(path: str | Path | None = None) -> Config:
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    # 加载项目根目录下的 .env (若存在), 让直接跑 CLI 也能读到 API key
    _load_dotenv(cfg_path.parent / ".env")
    with open(cfg_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    base = cfg_path.parent

    src = raw.get("sources", {})
    mls = [
        MailingListSource(name=s["name"], type=s["type"], url=s["url"])
        for s in src.get("mailing_lists", [])
    ]
    gls = [
        GitLabSource(name=s["name"], project=s["project"], url=s["url"])
        for s in src.get("gitlab", [])
    ]

    storage_raw = raw.get("storage", {})
    storage = Storage(db_path=_resolve_path(storage_raw.get("db_path", "data/virt_report.db"), base))

    render_raw = raw.get("render", {})
    render = Render(
        output_dir=_resolve_path(render_raw.get("output_dir", "site"), base),
        site_url=render_raw.get("site_url", ""),
    )

    proj = raw.get("project", {})
    config = Config(
        name=proj.get("name", "virt-report"),
        timezone=proj.get("timezone", "Asia/Shanghai"),
        sources=Sources(mailing_lists=mls, gitlab=gls),
        storage=storage,
        llm=LLMConfig(**raw.get("llm", {})),
        render=render,
        schedule=Schedule(**raw.get("schedule", {})),
    )
    # 确保 DB 目录存在
    config.db_path.parent.mkdir(parents=True, exist_ok=True)
    config.output_dir.mkdir(parents=True, exist_ok=True)
    return config
