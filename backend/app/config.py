import os
import tomllib
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
CFG = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))

DATA = Path(os.environ["LOCALAPPDATA"]) / "TradingAssistant"
DATA.mkdir(parents=True, exist_ok=True)
(DATA / "logs").mkdir(exist_ok=True)
