"""只解析已知 CLI 选项，不执行历史命令，也不输出历史中的密钥。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from guild_manager_bench.api.api_profiles import ApiProfiles, fingerprint
from guild_manager_bench.api.workbench_models import RunSpec

VALUE = r"""("(?:`.|[^"`])*"|'(?:''|[^'])*'|[^\s;|]+)"""
FLAGS = re.compile(r"--(provider|base-url|api-key|model)(?:\s*=\s*|\s+)" + VALUE)
ASSIGNMENT = re.compile(r"(\$(?:env:)?[\w]+)\s*=\s*" + VALUE, re.IGNORECASE)
COMMAND = re.compile(r"\bguild-manager(?:\.exe)?\b|\bguild_manager_bench\b|\brun_gpt[\w]*\.py\b")
LABELS = {
    "openai": "Chat",
    "openai-responses": "Responses",
    "anthropic": "Anthropic",
    "google": "Gemini",
}


def history_paths() -> list[Path]:
    paths = []
    if os.environ.get("APPDATA"):
        appdata = Path(os.environ["APPDATA"])
        paths += [
            appdata / "Microsoft/Windows/PowerShell/PSReadLine/ConsoleHost_history.txt",
            appdata / "Microsoft/PowerShell/PSReadLine/ConsoleHost_history.txt",
        ]
    paths += [Path.home() / ".bash_history", Path.home() / ".zsh_history"]
    return [path for path in paths if path.is_file()]


def literal(token: str, variables: dict[str, str]) -> str | None:
    if token.startswith("$"):
        return variables.get(token.lower())
    if token.startswith("'") and token.endswith("'"):
        return token[1:-1].replace("''", "'")
    if token.startswith('"') and token.endswith('"'):
        value = token[1:-1]
        # 只支持字面值和明确的变量引用，不猜测 PowerShell 插值或表达式。
        if "$" in value:
            return None
        return re.sub(r"`(.)", r"\1", value)
    if token.startswith("--"):
        return None
    return token


def parse_history(text: str) -> dict:
    variables: dict[str, str] = {}
    candidates: dict[str, dict] = {}
    skipped = 0
    variants: dict[tuple[str, str], int] = {}
    text = re.sub(r"`\r?\n|\\\r?\n", " ", text)
    for line in text.splitlines():
        # 逐个解析分号分隔的命令，不能把一行里两次运行的参数混在一起。
        segments = re.split(r""";(?=(?:[^"']|"[^"]*"|'[^']*')*$)""", line)
        for segment in segments:
            for name, token in ASSIGNMENT.findall(segment):
                value = literal(token, variables)
                if value is not None:
                    variables[name.lower()] = value
            if not COMMAND.search(segment):
                continue
            flags = {key: literal(token, variables) for key, token in FLAGS.findall(segment)}
            if not flags:
                continue
            provider = flags.get("provider", "openai")
            base = flags.get("base-url")
            if (
                provider not in LABELS
                or not base
                or ("api-key" in flags and flags["api-key"] is None)
            ):
                skipped += 1
                continue
            try:
                base = RunSpec.safe_url(base)
                parsed = urlsplit(base)
                _ = parsed.port  # 无效端口不能导入为可用连接。
                if re.match(r"^/:\d+(?:/|$)", parsed.path):
                    raise ValueError("端口误写在路径中")
            except ValueError:
                skipped += 1
                continue
            secret = flags.get("api-key") or None
            identity = fingerprint(provider, base, secret)
            model = flags.get("model")
            if model and (len(model) > 200 or "://" in model or model.startswith("$")):
                skipped += 1
                continue
            if identity not in candidates:
                key = (provider, base)
                variants[key] = variants.get(key, 0) + 1
                suffix = f" / 凭证{variants[key]}" if variants[key] > 1 else ""
                name = f"{parsed.netloc}{parsed.path} / {LABELS[provider]}{suffix}"[:120]
                candidates[identity] = {
                    "name": name,
                    "provider": provider,
                    "base_url": base,
                    "api_key": secret,
                    "default_model": None,
                    "known_models": [],
                }
            entry = candidates[identity]
            if model:
                entry["default_model"] = model
                if model not in entry["known_models"]:
                    entry["known_models"].append(model)
    return {"candidates": list(candidates.values()), "skipped_unresolved": skipped}


def collect_history(paths: list[Path] | None = None) -> dict:
    sources = history_paths() if paths is None else paths
    # 每个 shell 文件独立解析，避免把不同 shell 的同名变量串在一起。
    unique = {}
    skipped = 0
    for path in sources:
        parsed = parse_history(path.read_text(encoding="utf-8-sig"))
        skipped += parsed["skipped_unresolved"]
        for candidate in parsed["candidates"]:
            key = fingerprint(candidate["provider"], candidate["base_url"], candidate["api_key"])
            if key in unique:
                candidate["known_models"] = list(
                    dict.fromkeys([*unique[key]["known_models"], *candidate["known_models"]])
                )
            unique[key] = candidate
    return {
        "candidates": list(unique.values()),
        "skipped_unresolved": skipped,
        "source_count": len(sources),
    }


def import_local_history(store: ApiProfiles) -> dict:
    collected = collect_history()
    return store.import_history(collected["candidates"]) | {
        key: collected[key] for key in ("skipped_unresolved", "source_count")
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="从本机命令历史导入 API 配置，不输出密钥")
    parser.add_argument("--import", dest="save", action="store_true")
    parser.add_argument("--root", type=Path, default=Path("runs/.workbench"))
    args = parser.parse_args()
    collected = collect_history()
    if args.save:
        result = ApiProfiles(args.root).import_history(collected["candidates"])
        print(
            json.dumps(
                {key: result[key] for key in ("created", "skipped_existing")}
                | {
                    "skipped_unresolved": collected["skipped_unresolved"],
                    "source_count": collected["source_count"],
                },
                ensure_ascii=False,
            )
        )
    else:
        print(
            json.dumps(
                {
                    "count": len(collected["candidates"]),
                    "skipped_unresolved": collected["skipped_unresolved"],
                    "profiles": [
                        {k: c[k] for k in ("name", "provider", "base_url", "default_model")}
                        | {
                            "credential_present": bool(c["api_key"]),
                            "historical_models": len(c["known_models"]),
                        }
                        for c in collected["candidates"]
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
